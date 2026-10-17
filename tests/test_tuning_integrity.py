"""Measured controller results must remain attributable across decisions and failures."""
from copy import deepcopy
from unittest.mock import Mock, patch

import pytest

import benchmark
from core.config import CONFIG, DEFAULT_CONFIG
from core.tuning_engine import run_tuning_engine
from core.tuning_session import create_tuning_session, evaluate_completed_round, finalize_decision
from pid_safety import (
    is_better_metrics, maybe_update_best_result, score_metrics, should_rollback_to_best,
)


@pytest.fixture(autouse=True)
def isolated_config():
    with patch.dict(CONFIG, {
        **DEFAULT_CONFIG, "MAX_TUNING_ROUNDS": 2, "BUFFER_SIZE": 3,
        "GOOD_ENOUGH_AVG_ERROR": .01, "REQUIRED_STABLE_ROUNDS": 2,
        "CSV_EXPORT_PATH": "",
    }, clear=True):
        yield


def metrics(**changes):
    return {"avg_error": .2, "steady_state_error": .1,
            "overshoot": 0., "status": "STABLE", **changes}


@pytest.mark.parametrize("key", ["avg_error", "steady_state_error", "overshoot"])
def test_zero_cost_always_beats_positive_cost(key):
    zero = metrics(**{key: 0.})
    positive = metrics(**{key: .1})
    assert is_better_metrics(zero, positive)
    assert not is_better_metrics(positive, zero)
    assert score_metrics(metrics(avg_error=0., steady_state_error=0.)) == 0.


@pytest.mark.parametrize("bad", [None, "invalid", float("nan"), float("inf"), -1.])
def test_invalid_measurements_cannot_become_best(bad):
    invalid = metrics(overshoot=bad)
    assert score_metrics(invalid) == float("inf")
    assert maybe_update_best_result(None, {"p": 1.}, invalid, 1) is None


def test_rollback_detects_regression_from_zero_error():
    best = metrics(avg_error=0., steady_state_error=0.)
    assert should_rollback_to_best(metrics(avg_error=2.), best)


class MeasuredEnv:
    def __init__(self, outputs, secondary=None, reject=False):
        self.outputs = iter(outputs)
        self.primary = {"p": 1., "i": .1, "d": .05}
        self.secondary = secondary
        self.applied = []
        self.reject = reject
        self.last_apply_issue = ""

    def get_current_pid(self):
        return deepcopy(self.primary), deepcopy(self.secondary)

    def get_setpoint(self):
        return 100.

    def get_prompt_context(self):
        return {}

    def reset_buffer_state(self):
        pass

    def collect_samples(self):
        output = next(self.outputs, None)
        if output is None:
            return []
        return [{"timestamp": i * 200., "setpoint": 100., "input": output,
                 "error": 100. - output, "pwm": 10., **self.primary,
                 **({"p2": self.secondary["p"], "i2": self.secondary["i"],
                     "d2": self.secondary["d"]} if self.secondary else {})}
                for i in range(3)]

    def apply_pid(self, primary, secondary=None):
        if self.reject:
            self.last_apply_issue = "Device rejected update."
            return
        self.primary = dict(primary)
        if secondary is not None:
            self.secondary = dict(secondary)
        self.applied.append(self.get_current_pid())


def test_done_is_advisory_and_new_pid_must_be_observed():
    env = MeasuredEnv([60., 70.])
    tuner = Mock()
    tuner.analyze.return_value = {"p": 2., "i": .1, "d": .05, "status": "DONE"}
    result = run_tuning_engine(env, tuner, "python_sim", emit_console=False)
    assert result["completed_reason"] == "max_rounds_reached"
    assert not result["converged"]
    assert result["rounds_completed"] == 2
    assert result["final_pid"] == env.primary
    assert result["final_pid_verified"]
    assert [r["pid"]["p"] for r in result["experiments"]] == [1., 2.]
    assert result["final_metrics"]["avg_error"] == 30.


def test_last_candidate_is_not_paired_with_previous_response():
    env = MeasuredEnv([60., 70.])
    tuner = Mock()
    tuner.analyze.side_effect = [
        {"p": p, "i": .1, "d": .05, "status": "TUNING"} for p in [2., 3.]
    ]
    result = run_tuning_engine(env, tuner, "python_sim", emit_console=False)
    assert result["final_pid"]["p"] == 3.
    assert not result["final_pid_verified"]
    assert result["final_metrics"] == {}
    assert result["evaluated_pid"]["p"] == 2.
    assert result["evaluated_metrics"]["avg_error"] == 30.


def test_failed_write_reports_device_gains_instead_of_candidate():
    env = MeasuredEnv([60.], reject=True)
    tuner = Mock()
    tuner.analyze.return_value = {"p": 2., "i": .1, "d": .05, "status": "DONE"}
    result = run_tuning_engine(env, tuner, "python_sim", emit_console=False)
    assert result["completed_reason"] == "error"
    assert result["final_pid"] == env.primary
    assert result["final_pid"]["p"] == 1.
    assert result["final_pid_verified"]
    assert not result["converged"]


def test_low_average_error_cannot_bypass_explicit_acceptance_rules():
    env = MeasuredEnv([99.9])
    tuner = Mock()
    tuner.analyze.return_value = {"p": 1., "i": .1, "d": .05}
    result = run_tuning_engine(env, tuner, "python_sim", max_rounds=1, emit_console=False)
    assert result["completed_reason"] == "max_rounds_reached"
    assert not result["converged"]


def test_zero_round_setup_has_no_measured_result_or_model_call():
    env = MeasuredEnv([])
    tuner = Mock()
    result = run_tuning_engine(env, tuner, "python_sim", max_rounds=0, emit_console=False)
    tuner.analyze.assert_not_called()
    assert result["rounds_completed"] == 0
    assert result["final_pid"] == env.primary
    assert not result["final_pid_verified"]
    assert result["final_metrics"] == {}
    assert result["evaluated_pid"] is None


def test_secondary_only_regression_rolls_back_both_loops():
    secondary = {"p": 1., "i": .1, "d": .05}
    env = MeasuredEnv([99.9, 90.], secondary=secondary)
    tuner = Mock()
    tuner.analyze.return_value = {
        "controller_1": dict(env.primary),
        "controller_2": {**secondary, "p": 2.}, "status": "TUNING",
    }
    result = run_tuning_engine(env, tuner, "python_sim", emit_console=False)
    assert result["rollback_count"] == 1
    assert env.secondary == secondary
    assert result["experiments"][-1]["secondary_pid"]["p"] == 2.
    assert result["final_secondary_pid"]["p"] == 1.
    assert result["final_metrics"] == {}
    assert not result["final_pid_verified"]


@pytest.mark.parametrize("change", ["target", "primary", "secondary"])
def test_consecutive_verification_requires_same_target_and_controller_pair(change):
    CONFIG["GOOD_ENOUGH_AVG_ERROR"] = 1.
    state = create_tuning_session()
    state.buffer.secondary_pid = {"p": 1., "i": .1, "d": 0.}
    primary = {"p": 1., "i": .1, "d": 0.}

    def observe(target):
        state.buffer.reset()
        state.buffer.add({"setpoint": target, "input": target - .1})
        return evaluate_completed_round(state, primary)

    assert observe(100.).stable_rounds == 1
    state.round_num += 1
    if change == "primary":
        primary["p"] = 2.
    elif change == "secondary":
        state.buffer.secondary_pid["p"] = 2.
    second = observe(200. if change == "target" else 100.)
    assert second.stable_rounds == 1
    assert second.completed_reason is None
    if change == "target":
        assert second.best_result["metrics"]["setpoint"] == 200.


def test_benchmark_uses_actual_seed_and_keeps_measured_parameter_pairs():
    before = dict(CONFIG)
    first = benchmark.run_case("fallback", 3, 42, stop_on_done=False)
    same = benchmark.run_case("fallback", 3, 42, stop_on_done=False)
    other = benchmark.run_case("fallback", 3, 43, stop_on_done=False)
    assert first["history"] == same["history"]
    assert first["history"] != other["history"]
    assert first["llm_calls"] == 0
    assert first["last_evaluated"]["pid"] == first["history"][-1]["pid"]
    assert first["last_evaluated"]["avg_error"] == first["history"][-1]["avg_error"]
    assert first["history"][0]["pid"] == {"p": 1., "i": .1, "d": .05}
    assert CONFIG == before


def test_benchmark_baseline_holds_parameters_and_uses_no_model():
    with patch.object(benchmark, "create_llm_tuner", side_effect=AssertionError("No API")):
        result = benchmark.run_case("baseline", 3, 42, stop_on_done=False)
    assert len(result["history"]) == 3
    assert all(r["pid"] == result["history"][0]["pid"] for r in result["history"])
    assert result["final"]["verified"]
    assert result["llm_calls"] == 0


@pytest.mark.parametrize("case, rounds", [("unknown", 1), ("baseline", 0)])
def test_invalid_benchmark_fails_before_constructing_model(case, rounds):
    with patch.object(benchmark, "create_llm_tuner", side_effect=AssertionError("No API")):
        with pytest.raises(ValueError):
            benchmark.run_case(case, rounds, 42)


def test_observe_skips_at_most_one_call_and_never_rewrites_or_changes_either_pid():
    env = MeasuredEnv([60., 65., 70., 75.], secondary={"p": 2., "i": .2, "d": 0.})
    tuner = Mock()
    tuner.analyze.return_value = {
        "tuning_action": "OBSERVE", "controller_1": {"p": 99., "i": 20., "d": 1.},
        "controller_2": {"p": 99., "i": 20., "d": 1.},
    }
    result = run_tuning_engine(env, tuner, "python_sim", max_rounds=4, emit_console=False)
    assert tuner.analyze.call_count == 2
    assert result["rounds_completed"] == 4
    assert env.applied == []
    assert result["final_pid"]["p"] == 1.
    assert result["final_secondary_pid"]["p"] == 2.
    assert result["final_pid_verified"]
    assert not result["converged"]


@pytest.mark.parametrize("outputs", [[60., 20.], [60., 110.]])
def test_observe_is_cancelled_when_error_or_overshoot_worsens(outputs):
    env = MeasuredEnv(outputs)
    tuner = Mock()
    tuner.analyze.return_value = {"tuning_action": "OBSERVE", **env.primary}
    result = run_tuning_engine(env, tuner, "python_sim", emit_console=False)
    assert tuner.analyze.call_count == 2
    assert not result["converged"]


def test_observe_is_cancelled_when_device_parameters_change():
    class ExternallyChangedEnv(MeasuredEnv):
        def collect_samples(self):
            self.primary["p"] += .1
            return super().collect_samples()

    env = ExternallyChangedEnv([60., 65.])
    tuner = Mock()
    tuner.analyze.return_value = {"tuning_action": "OBSERVE"}
    run_tuning_engine(env, tuner, "python_sim", emit_console=False)
    assert tuner.analyze.call_count == 2


def test_observe_with_invalid_measurements_never_skips_analysis():
    env = MeasuredEnv([float("nan"), float("nan")])
    tuner = Mock()
    tuner.analyze.return_value = {"tuning_action": "OBSERVE"}
    result = run_tuning_engine(env, tuner, "python_sim", emit_console=False)
    assert tuner.analyze.call_count == 2
    assert not result["converged"]


def test_observe_reports_actual_gains_even_when_outside_adjustment_limits():
    state = create_tuning_session(initial_pid={"p": 1.e6, "i": .1, "d": .05})
    for _ in range(10):
        state.buffer.add({"input": 60., "setpoint": 100.})
    evaluation = evaluate_completed_round(state, state.buffer.current_pid)
    decision = finalize_decision(state, evaluation, {"tuning_action": "OBSERVE"})
    assert decision.safe_pid == evaluation.current_pid
    assert state.buffer.current_pid == evaluation.current_pid
    assert not decision.guardrail_notes


def test_session_usage_survives_localization_and_excludes_previous_requests():
    from llm.client import LLMTuner
    from llm.providers import BaseLLMProvider
    from desktop.session import _LocalizedTuner
    from sim.runtime import QueueEventSink, drain_event_queue
    from queue import Queue

    class MeteredProvider(BaseLLMProvider):
        def execute_request(self, **kwargs):
            self.last_usage = {"input_tokens": 10, "output_tokens": 2}
            self.usage_final = True
            kwargs["on_chunk"]('{"tuning_action":"OBSERVE"}')

    provider = MeteredProvider("test", "https://test.invalid", "test", 5)
    with patch.object(LLMTuner, "_initialize_provider", return_value=provider):
        client = LLMTuner("test", "https://test.invalid", "test", emit_console=False)
    client.request_json(system_prompt="before session", user_prompt="test")
    env = MeasuredEnv([60., 65., 70.])
    sink = QueueEventSink(Queue())
    result = run_tuning_engine(env, _LocalizedTuner(client), "python_sim", max_rounds=3,
                               event_sink=sink, emit_console=False)
    usage = result["llm_usage"]
    assert usage["transport_attempts"] == usage["logical_requests"] == 2
    assert usage["reported_tokens"]["input_tokens"] == 20
    assert usage["reported_tokens"]["output_tokens"] == 4
    assert usage["usage_complete"]
    assert len(result["experiments"]) == 3
    events = drain_event_queue(sink.event_queue)
    usage_events = [event for event in events if event["type"] == "llm_usage"]
    assert len(usage_events) == 2
    assert usage_events[-1]["usage"]["reported_tokens"] == usage["reported_tokens"]
    assert "attempts" not in usage_events[-1]["usage"]
