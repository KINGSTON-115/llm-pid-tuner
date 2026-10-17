"""Usage accounting and compression keep measured evidence and unknown costs explicit."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests

from core.buffer import AdvancedDataBuffer
from core.history import TuningHistory
from llm.client import LLMTuner
from llm.providers import AnthropicSDKProvider, HTTPFallbackProvider, OpenAISDKProvider
from llm.prompts import build_user_prompt, get_system_prompt
from llm.usage import normalize_usage


def openai_usage(inputs=100, outputs=20):
    return {"prompt_tokens": inputs, "completion_tokens": outputs,
            "prompt_tokens_details": {"cached_tokens": 30},
            "completion_tokens_details": {"reasoning_tokens": 4}}


class StreamResponse:
    def __init__(self, events):
        self.events = events
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def raise_for_status(self):
        pass

    def iter_lines(self):
        for event in self.events:
            yield ("data: " + json.dumps(event)).encode()
        yield b"data: [DONE]"


def http_provider(events, anthropic=False):
    response = StreamResponse(events)
    transport = SimpleNamespace(post=Mock(return_value=response))
    provider = HTTPFallbackProvider("test", "https://test.invalid/v1", "test", 5,
                                    anthropic, requests_module=transport)
    return provider, transport, response


def tuner_with(provider, name="openai"):
    with patch.object(LLMTuner, "_initialize_provider", return_value=provider):
        return LLMTuner("test", "https://test.invalid/v1", "test", name, emit_console=False)


def text_event(text='{"p":1,"i":0,"d":0}'):
    return {"choices": [{"delta": {"content": text}}]}


def test_usage_normalization_does_not_double_count_cached_or_reasoning_tokens():
    actual = normalize_usage(openai_usage())
    assert actual == dict(input_tokens=100, output_tokens=20, cached_input_tokens=30,
                          cache_creation_input_tokens=None, reasoning_tokens=4)
    assert normalize_usage({"prompt_tokens": 0, "completion_tokens": 0})["input_tokens"] == 0
    assert normalize_usage({"prompt_tokens": -1, "completion_tokens": True})["input_tokens"] is None
    assert normalize_usage({"prompt_tokens": -1, "completion_tokens": True})["output_tokens"] is None


def test_sdk_openai_consumes_usage_only_chunk_and_closes_stream():
    stream = Mock()
    stream.__iter__ = Mock(return_value=iter([
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"))], usage=None),
        SimpleNamespace(choices=[], usage=SimpleNamespace(**openai_usage())),
    ]))
    factory = Mock()
    factory.return_value.chat.completions.create.return_value = stream
    with patch.dict("sys.modules", {"openai": SimpleNamespace(OpenAI=factory)}):
        provider = OpenAISDKProvider("test", "https://test.invalid", "test", 5)
    chunks = []
    provider.execute_request([], [], "", chunks.append)
    assert chunks == ["ok"]
    assert provider.last_usage["input_tokens"] == 100
    assert provider.usage_final
    assert factory.return_value.chat.completions.create.call_args.kwargs["stream_options"] == {"include_usage": True}
    stream.close.assert_called_once()


@pytest.mark.parametrize("cancel", [False, True])
def test_anthropic_sdk_usage_and_cancel_do_not_force_consuming_final_message(cancel):
    stream = Mock()
    stream.text_stream = iter(["ok"])
    stream.get_final_message.return_value.usage = SimpleNamespace(
        input_tokens=10, output_tokens=5, cache_read_input_tokens=20, cache_creation_input_tokens=4,
    )
    manager = Mock()
    manager.__enter__ = Mock(return_value=stream)
    manager.__exit__ = Mock()
    factory = Mock()
    factory.return_value.messages.stream.return_value = manager
    with patch.dict("sys.modules", {"anthropic": SimpleNamespace(Anthropic=factory)}):
        provider = AnthropicSDKProvider("test", "https://test.invalid", "test", 5)
    provider.execute_request([], [], "", lambda _: None, abort_check=lambda: cancel)
    assert provider.usage_final is not cancel
    if cancel:
        stream.get_final_message.assert_not_called()
    else:
        assert provider.last_usage["input_tokens"] == 34
        assert provider.last_usage["output_tokens"] == 5
    manager.__exit__.assert_called_once()


def test_anthropic_http_merges_cumulative_events_and_cache_buckets_once():
    provider, _, response = http_provider([
        {"type": "message_start", "message": {"usage": {
            "input_tokens": 12, "output_tokens": 0,
            "cache_read_input_tokens": 30, "cache_creation_input_tokens": 4}}},
        {"type": "content_block_delta", "delta": {"text": '{"p":1}'}},
        {"type": "message_delta", "usage": {"output_tokens": 5}},
        {"type": "message_delta", "usage": {"output_tokens": 9}},
        {"type": "message_stop"},
    ], anthropic=True)
    tuner = tuner_with(provider, name="anthropic")
    assert tuner.request_json(system_prompt="sys", user_prompt="user")["p"] == 1
    usage = tuner.get_usage_summary()
    assert usage["reported_tokens"]["input_tokens"] == 46
    assert usage["reported_tokens"]["output_tokens"] == 9
    assert usage["reported_tokens"]["cached_input_tokens"] == 30
    assert usage["usage_complete"]
    assert usage["input_chars"] == 7
    assert response.closed


def test_missing_usage_is_unknown_and_previous_request_usage_is_not_reused():
    provider, transport, _ = http_provider([text_event(), {"usage": openai_usage(), "choices": []}])
    tuner = tuner_with(provider)
    tuner.request_json(system_prompt="s", user_prompt="u")
    transport.post.return_value = StreamResponse([text_event("invalid json")])
    assert tuner.request_json(system_prompt="s", user_prompt="u") is None
    usage = tuner.get_usage_summary()
    assert usage["transport_attempts"] == usage["logical_requests"] == 2
    assert usage["reported_tokens"]["input_tokens"] == 100
    assert usage["attempts_with_complete_usage"] == 1
    assert not usage["usage_complete"]
    assert usage["attempts"][-1]["usage"] == {}
    assert tuner.get_usage_summary(start_attempt=1)["reported_tokens"]["input_tokens"] is None


def test_relay_rejection_negotiates_usage_option_once_and_accounts_for_both_attempts():
    provider, transport, response = http_provider([text_event()])
    rejected = requests.Response()
    rejected.status_code = 400
    rejected._content = b'{"error":{"message":"Unknown parameter stream_options"}}'
    transport.post.side_effect = [requests.HTTPError(response=rejected), response, response]
    tuner = tuner_with(provider)
    assert tuner.request_json(system_prompt="s", user_prompt="u")
    assert tuner.request_json(system_prompt="s", user_prompt="u")
    payloads = [call.kwargs["json"] for call in transport.post.call_args_list]
    assert "stream_options" in payloads[0]
    assert all("stream_options" not in p for p in payloads[1:])
    usage = tuner.get_usage_summary()
    assert usage["logical_requests"] == 2
    assert usage["transport_attempts"] == 3
    assert not usage["usage_complete"]
    assert usage["reported_tokens"]["input_tokens"] is None


def test_retry_keeps_partial_cost_and_output_of_failed_attempt():
    provider, _, _ = http_provider([])
    count = 0

    def execute(**kwargs):
        nonlocal count
        count += 1
        provider.last_usage = normalize_usage(openai_usage(inputs=10 * count, outputs=count))
        kwargs["on_chunk"]("partial" if count == 1 else '{"p":1}')
        if count == 1:
            raise requests.ConnectionError("interrupted")
        provider.usage_final = True

    provider.execute_request = execute
    tuner = tuner_with(provider)
    with patch.object(tuner, "_interruptible_sleep", return_value=True):
        assert tuner.request_json(system_prompt="s", user_prompt="u")
    usage = tuner.get_usage_summary()
    assert usage["logical_requests"] == 1
    assert usage["transport_attempts"] == 2
    assert usage["reported_tokens"]["input_tokens"] == 30
    assert usage["reported_tokens"]["output_tokens"] == 3
    assert usage["output_chars"] == len('partial{"p":1}')
    assert not usage["usage_complete"]


def test_sdk_fallback_does_not_discard_first_transport_usage():
    stream = Mock()

    def interrupted():
        yield SimpleNamespace(choices=[], usage=SimpleNamespace(**openai_usage(10, 2)))
        raise requests.ConnectionError("interrupted")

    stream.__iter__ = Mock(side_effect=interrupted)
    factory = Mock()
    factory.return_value.chat.completions.create.return_value = stream
    with patch.dict("sys.modules", {"openai": SimpleNamespace(OpenAI=factory)}):
        sdk = OpenAISDKProvider("test", "https://test.invalid", "test", 5)
    http, _, _ = http_provider([text_event(), {"choices": [], "usage": openai_usage(20, 4)}])
    tuner = tuner_with(sdk)
    with patch("llm.client.HTTPFallbackProvider", return_value=http):
        assert tuner.request_json(system_prompt="s", user_prompt="u")
    usage = tuner.get_usage_summary()
    assert usage["reported_tokens"]["input_tokens"] == 30
    assert usage["reported_tokens"]["output_tokens"] == 6
    assert [r["transport"] for r in usage["attempts"]] == ["OpenAISDKProvider", "HTTPFallbackProvider"]
    assert not usage["usage_complete"]


def test_cancelled_anthropic_start_usage_is_partial_not_a_complete_zero_output_request():
    provider, _, _ = http_provider([
        {"type": "message_start", "message": {"usage": {"input_tokens": 10, "output_tokens": 0}}},
    ], anthropic=True)
    tuner = tuner_with(provider, name="anthropic")
    tuner.request_json(system_prompt="s", user_prompt="u")
    usage = tuner.get_usage_summary()
    assert usage["reported_tokens"]["input_tokens"] == 10
    assert not usage["usage_complete"]


def sample(i, **changes):
    return {"timestamp": i * .01, "setpoint": 100., "input": float(i),
            "pwm": 20., "error": 100. - i, **changes}


def test_compact_history_keeps_best_and_rejected_evidence_beyond_recent_window():
    history = TuningHistory(max_history=2)
    metrics = {"avg_error": 1., "max_error": 2., "steady_state_error": .1,
               "overshoot": 0., "zero_crossings": 0, "setpoint": 100., "status": "STABLE"}
    secondary = {"p": 2., "i": .2, "d": 0.}
    for i in range(1, 6):
        history.add_record(i, {"p": float(i), "i": .1, "d": 0.}, metrics,
                           "long rationale" * 100, "long reasoning" * 100,
                           secondary_pid=secondary, next_action="ROLLBACK" if i == 2 else "ADJUST_PID")
    best = {"round": 1, "pid": {"p": 1., "i": .1, "d": 0.},
            "secondary_pid": secondary, "metrics": metrics}
    prompt = history.to_prompt_text(best_result=best)
    assert "best|1|1,0.1,0|2,0.2,0" in prompt
    assert "last_rejected|2|" in prompt
    assert "recent|4|" in prompt and "recent|5|" in prompt
    assert "long rationale" not in prompt and "long reasoning" not in prompt
    assert "tail_abs_error" in prompt and "0.1|0|0|STABLE" in prompt
    assert len(prompt) < 1000
    assert "long reasoning" in history.history[-1]["thought"]


def test_sampling_preserves_spike_endpoints_target_transition_and_submillisecond_times():
    buffer = AdvancedDataBuffer(100)
    for i in range(100):
        buffer.add(sample(i, input=999. if i == 49 else float(i), setpoint=100. if i < 61 else 200.))
    selected = buffer._prompt_samples()
    timestamps = {row["timestamp"] for row in selected}
    assert {0., .49, .60, .61, .99} <= timestamps
    assert len(selected) <= 20
    text = buffer.to_prompt_data()
    assert "0.49,100,999" in text
    assert "Setpoint" in text and "20/100" in text
    assert buffer.calculate_advanced_metrics()["max_error"] == 899.


def test_many_target_changes_are_retained_even_when_nominal_budget_is_exceeded():
    buffer = AdvancedDataBuffer(80)
    for i in range(80):
        buffer.add(sample(i, target_y=i % 2))
    assert len(buffer._prompt_samples()) == 80


def test_prompt_requests_concise_output_and_deduplicates_guardrails_and_preferences():
    context = {"pid_limits": {"p": {"min": 0., "max": 42., "max_increase_ratio": 2.}},
               "user_preference_summary": "Keep measured overshoot below 0.8%.",
               "user_hard_constraints": ["absolute output <= 250"], "controller_count": 2}
    system = get_system_prompt("hardware", context)
    user = build_user_prompt("samples", "history", "hardware", context)
    assert "不输出 thought_process" in system
    assert "OBSERVE" in system
    assert "max=42" in system
    assert "pid limits" not in user
    assert user.count(context["user_preference_summary"]) == 1
    assert "absolute output <= 250" in user
    assert "analysis_summary、tuning_action、controller_1、controller_2、status" in user
