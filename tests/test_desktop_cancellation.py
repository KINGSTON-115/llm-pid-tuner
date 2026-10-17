"""A paused analysis must not apply gains; stop must discard its result."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core.adapters import PythonSimEnv
from core.config import CONFIG, DEFAULT_CONFIG
from core.tuning_engine import run_tuning_engine
from llm.providers import AnthropicSDKProvider, OpenAISDKProvider
from sim.model import HeatingSimulator
from sim.runtime import SimulationController


@pytest.mark.parametrize("stop", [True, False])
def test_pause_while_model_is_analyzing_waits_before_applying(stop):
    controller = SimulationController()
    env = PythonSimEnv(HeatingSimulator(), 200, controller)
    entered = threading.Event()
    release = threading.Event()
    returned = threading.Event()

    def analyze(*_args, **_kwargs):
        entered.set()
        release.wait(3)
        returned.set()
        return {"p": 2., "i": .2, "d": .1, "status": "TUNING"}

    tuner = SimpleNamespace(analyze=analyze)
    output = {}
    with patch.dict(CONFIG, {**DEFAULT_CONFIG, "MAX_TUNING_ROUNDS": 2}, clear=True):
        worker = threading.Thread(target=lambda: output.update(run_tuning_engine(env, tuner, "python_sim", controller=controller, emit_console=False)))
        worker.start()
        try:
            assert entered.wait(2)
            controller.pause()
            release.set()
            assert returned.wait(2)
            worker.join(.15)
            assert worker.is_alive()
            assert env.get_current_pid()[0]["p"] == 1.
            if stop:
                controller.request_stop()
            else:
                controller.resume()
            worker.join(2)
            assert not worker.is_alive()
            assert env.get_current_pid()[0]["p"] == (1. if stop else 2.)
        finally:
            release.set()
            controller.request_stop()
            worker.join(3)


@pytest.mark.parametrize("provider,module,name", [(OpenAISDKProvider, "openai", "OpenAI"),
                                                   (AnthropicSDKProvider, "anthropic", "Anthropic")])
def test_sdk_uses_configured_timeout_without_hidden_retry_delays(provider, module, name):
    factory = Mock()
    with patch.dict("sys.modules", {module: SimpleNamespace(**{name: factory})}):
        provider("test-key", "https://example.invalid/v1", "test-model", 7)
    factory.assert_called_once_with(api_key="test-key", base_url="https://example.invalid/v1", timeout=7, max_retries=0)


def test_stopped_openai_stream_is_closed():
    stream = Mock()
    stream.__iter__ = Mock(return_value=iter([SimpleNamespace(choices=[])]))
    client = Mock()
    client.chat.completions.create.return_value = stream
    with patch.dict("sys.modules", {"openai": SimpleNamespace(OpenAI=Mock(return_value=client))}):
        provider = OpenAISDKProvider("test", "https://example.invalid", "test", 7)
    callback = Mock()
    provider.execute_request([], [], "", callback, abort_check=lambda: True)
    callback.assert_not_called()
    stream.close.assert_called_once()
