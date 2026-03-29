import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent))

import core.config as core_config
import simulator
from core.buffer import AdvancedDataBuffer
from core.config import CONFIG, DEFAULT_CONFIG, load_config
from core.tuning_session import (
    RoundEvaluation,
    create_tuning_session,
    finalize_decision,
    record_rollback_round,
)
from llm.client import LLMTuner
from llm.prompts import build_user_prompt, get_system_prompt, normalize_tuning_mode
from pid_safety import get_pid_limits
from sim.model import CONTROL_INTERVAL, INITIAL_TEMP, SETPOINT, HeatingSimulator


class ConfigLoadTests(unittest.TestCase):
    def test_defaults_present(self):
        required_keys = [
            "SERIAL_PORT",
            "BAUD_RATE",
            "LLM_API_KEY",
            "LLM_API_BASE_URL",
            "LLM_MODEL_NAME",
            "LLM_PROVIDER",
            "BUFFER_SIZE",
            "MAX_TUNING_ROUNDS",
            "LLM_REQUEST_TIMEOUT",
            "LLM_DEBUG_OUTPUT",
            "GOOD_ENOUGH_AVG_ERROR",
            "GOOD_ENOUGH_STEADY_STATE_ERROR",
            "GOOD_ENOUGH_OVERSHOOT",
            "REQUIRED_STABLE_ROUNDS",
            "MATLAB_MODEL_PATH",
            "MATLAB_PID_BLOCK_PATH",
            "MATLAB_OUTPUT_SIGNAL",
            "MATLAB_SIM_STEP_TIME",
            "MATLAB_SETPOINT",
        ]
        for key in required_keys:
            self.assertIn(key, CONFIG, f"CONFIG missing key {key}")

    def test_default_types(self):
        self.assertIsInstance(CONFIG["BAUD_RATE"], int)
        self.assertIsInstance(CONFIG["BUFFER_SIZE"], int)
        self.assertIsInstance(CONFIG["MAX_TUNING_ROUNDS"], int)
        self.assertIsInstance(CONFIG["LLM_REQUEST_TIMEOUT"], int)
        self.assertIsInstance(CONFIG["LLM_DEBUG_OUTPUT"], bool)
        self.assertIsInstance(CONFIG["GOOD_ENOUGH_AVG_ERROR"], float)

    def test_load_config_does_not_raise(self):
        try:
            load_config(create_if_missing=False, verbose=False)
        except Exception as exc:  # pragma: no cover - failure branch
            self.fail(f"load_config raised: {exc}")

    def test_generated_config_matches_template(self):
        expected = json.loads(
            (Path(__file__).parent.parent / "config.example.json").read_text(
                encoding="utf-8"
            )
        )
        original_config = dict(core_config.CONFIG)
        original_cwd = os.getcwd()
        generated = None

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                os.chdir(temp_dir)
                core_config.CONFIG = dict(DEFAULT_CONFIG)
                core_config.load_config(create_if_missing=True, verbose=False)
                generated = json.loads(
                    Path("config.json").read_text(encoding="utf-8")
                )
                os.chdir(original_cwd)
        finally:
            os.chdir(original_cwd)
            core_config.CONFIG = original_config

        self.assertEqual(generated, expected)


class LLMFallbackTests(unittest.TestCase):
    def _make_tuner_without_sdk(self, provider: str = "openai") -> LLMTuner:
        with patch.dict("sys.modules", {"openai": None, "anthropic": None}):
            return LLMTuner("fake-key", "https://fake.api/v1", "gpt-mock", provider)

    def test_fallback_when_sdk_missing(self):
        tuner = self._make_tuner_without_sdk("openai")
        self.assertFalse(tuner.use_sdk)

    def test_parse_json_extracts_pid(self):
        tuner = self._make_tuner_without_sdk()
        raw = '{"p": 1.5, "i": 0.2, "d": 0.01, "status": "TUNING", "analysis_summary": "ok"}'
        result = tuner._parse_json(raw)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["p"], 1.5)  # type: ignore[index]
        self.assertAlmostEqual(result["i"], 0.2)  # type: ignore[index]
        self.assertAlmostEqual(result["d"], 0.01)  # type: ignore[index]

    def test_parse_json_rejects_negative_pid(self):
        tuner = self._make_tuner_without_sdk()
        raw = '{"p": -1.0, "i": 0.1, "d": 0.05, "status": "TUNING"}'
        result = tuner._parse_json(raw)
        self.assertIsNotNone(result)
        self.assertNotIn("p", result)  # type: ignore[operator]

    def test_provider_resolution_openai(self):
        tuner = self._make_tuner_without_sdk("openai")
        self.assertEqual(tuner.provider, "openai")

    def test_provider_resolution_anthropic(self):
        tuner = self._make_tuner_without_sdk("anthropic")
        self.assertEqual(tuner.provider, "anthropic")

    def test_http_stream_callback_emits_done_once(self):
        done_updates = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return None

            def raise_for_status(self):
                return None

            def iter_lines(self):
                yield b'data: {"choices":[{"delta":{"content":"{\\"status\\":\\"DONE\\"}"}}]}'
                yield b"data: [DONE]"

        class FakeRequests:
            def post(self, *args, **kwargs):
                return FakeResponse()

        tuner = self._make_tuner_without_sdk("openai")
        tuner.requests = FakeRequests()  # type: ignore[assignment]
        tuner.stream_callback = lambda _text, done: done_updates.append(done)

        result = tuner._execute_request(
            [{"role": "user", "content": "hello"}],
            [{"role": "user", "content": "hello"}],
        )

        self.assertEqual(result, '{"status":"DONE"}')
        self.assertEqual(done_updates.count(True), 1)

    def test_sdk_stream_ignores_null_delta_summary_chunk(self):
        class FakeDelta:
            def __init__(self, content):
                self.content = content

        class FakeMessage:
            def __init__(self, content):
                self.content = content

        class FakeChoice:
            def __init__(self, *, delta=None, message=None):
                self.delta = delta
                self.message = message

        class FakeChunk:
            def __init__(self, choice):
                self.choices = [choice]

        chunks = [
            FakeChunk(FakeChoice(delta=FakeDelta('{"status":"'))),
            FakeChunk(FakeChoice(delta=FakeDelta('DONE"}'))),
            FakeChunk(FakeChoice(delta=None, message=FakeMessage('{"status":"DONE"}'))),
        ]

        tuner = self._make_tuner_without_sdk("openai")
        tuner.use_sdk = True
        tuner.client = types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=types.SimpleNamespace(create=lambda **kwargs: iter(chunks))
            )
        )
        tuner._request_via_http = lambda *args, **kwargs: self.fail("unexpected HTTP fallback")  # type: ignore[method-assign]

        result = tuner._execute_request(
            [{"role": "user", "content": "hello"}],
            [{"role": "user", "content": "hello"}],
        )

        self.assertEqual(result, '{"status":"DONE"}')

    def test_sdk_stream_accepts_message_only_chunk(self):
        class FakeChoice:
            def __init__(self, *, message=None):
                self.delta = None
                self.message = message

        class FakeMessage:
            def __init__(self, content):
                self.content = content

        class FakeChunk:
            def __init__(self, content):
                self.choices = [FakeChoice(message=FakeMessage(content))]

        tuner = self._make_tuner_without_sdk("openai")
        tuner.use_sdk = True
        tuner.client = types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=types.SimpleNamespace(
                    create=lambda **kwargs: iter([FakeChunk('{"status":"DONE"}')])
                )
            )
        )
        tuner._request_via_http = lambda *args, **kwargs: self.fail("unexpected HTTP fallback")  # type: ignore[method-assign]

        result = tuner._execute_request(
            [{"role": "user", "content": "hello"}],
            [{"role": "user", "content": "hello"}],
        )

        self.assertEqual(result, '{"status":"DONE"}')


class PromptSelectionTests(unittest.TestCase):
    def test_normalize_tuning_mode_maps_known_aliases(self):
        self.assertEqual(normalize_tuning_mode("python"), "python_sim")
        self.assertEqual(normalize_tuning_mode("simulink"), "simulink")
        self.assertEqual(normalize_tuning_mode("serial"), "hardware")

    def test_simulink_system_prompt_mentions_placeholder_pwm(self):
        prompt = get_system_prompt("simulink")
        self.assertIn("Simulink", prompt)
        self.assertIn("PWM", prompt)
        self.assertIn("0.0", prompt)

    def test_user_prompt_includes_mode_context(self):
        prompt = build_user_prompt(
            "## Current Status\n- Current PID: P=1.0, I=0.1, D=0.05",
            "No tuning history yet.",
            tuning_mode="hardware",
            prompt_context={
                "serial_port": "COM9",
                "pwm_signal_available": True,
            },
        )

        self.assertIn("hardware", prompt)
        self.assertIn("serial port: COM9", prompt)
        self.assertIn("pwm signal available", prompt)


class TuningSessionHistoryTests(unittest.TestCase):
    def test_finalize_decision_records_pid_that_generated_metrics(self):
        state = create_tuning_session(
            initial_pid={"p": 1.0, "i": 0.0, "d": 0.0},
            setpoint=200.0,
        )
        evaluation = RoundEvaluation(
            round_index=1,
            metrics={"avg_error": 148.26, "steady_state_error": 130.41, "overshoot": 0.0, "status": "SLOW_RESPONSE"},
            current_pid={"p": 1.0, "i": 0.0, "d": 0.0},
            stable_rounds=0,
        )

        finalize_decision(
            state,
            evaluation,
            {
                "analysis_summary": "Increase gains.",
                "thought_process": "Round 1 was too slow.",
                "tuning_action": "ADJUST_PID",
                "p": 15.0,
                "i": 1.0,
                "d": 5.0,
                "status": "TUNING",
            },
        )

        record = state.history.history[0]
        self.assertEqual(record["pid"], {"p": 1.0, "i": 0.0, "d": 0.0})
        self.assertEqual(record["metrics"]["avg_error"], 148.26)
        self.assertEqual(state.buffer.current_pid["p"], 3.0)

    def test_record_rollback_round_keeps_failed_pid_in_history(self):
        state = create_tuning_session(
            initial_pid={"p": 25.0, "i": 9.5, "d": 4.5},
            setpoint=200.0,
        )
        evaluation = RoundEvaluation(
            round_index=6,
            metrics={"avg_error": 93.0, "steady_state_error": 8.0, "overshoot": 4.6, "status": "STABLE"},
            current_pid={"p": 25.0, "i": 9.5, "d": 4.5},
            stable_rounds=0,
            best_result={"round": 5, "pid": {"p": 22.0, "i": 8.0, "d": 5.0}, "metrics": {"avg_error": 60.88}},
            rollback_pid={"p": 22.0, "i": 8.0, "d": 5.0},
        )

        summary = record_rollback_round(
            state,
            evaluation,
            {"p": 22.0, "i": 8.0, "d": 5.0},
            target_round=5,
        )

        record = state.history.history[0]
        self.assertEqual(record["pid"], {"p": 25.0, "i": 9.5, "d": 4.5})
        self.assertEqual(record["metrics"]["avg_error"], 93.0)
        self.assertIn("Automatic rollback triggered", record["analysis"])
        self.assertIn("round 5", summary)

    def test_finalize_decision_allows_simulink_five_x_p_step(self):
        state = create_tuning_session(
            initial_pid={"p": 100.0, "i": 0.5, "d": 0.0},
            setpoint=200.0,
        )
        evaluation = RoundEvaluation(
            round_index=1,
            metrics={
                "avg_error": 5.0,
                "steady_state_error": 1.8,
                "overshoot": 0.0,
                "status": "SLOW_RESPONSE",
            },
            current_pid={"p": 100.0, "i": 0.5, "d": 0.0},
            stable_rounds=0,
        )

        decision = finalize_decision(
            state,
            evaluation,
            {
                "analysis_summary": "Increase P aggressively for Simulink.",
                "thought_process": "Current P is too low.",
                "tuning_action": "BOOST_RESPONSE",
                "p": 500.0,
                "i": 0.5,
                "d": 0.0,
                "status": "TUNING",
            },
            limits=get_pid_limits("simulink"),
        )

        self.assertEqual(decision.safe_pid["p"], 500.0)
        self.assertEqual(state.buffer.current_pid["p"], 500.0)


class BufferTests(unittest.TestCase):
    def _make_data_point(self, temp: float, setpoint: float = 200.0) -> dict:
        return {
            "timestamp": 0,
            "setpoint": setpoint,
            "input": temp,
            "pwm": 100.0,
            "error": setpoint - temp,
        }

    def test_is_full_after_max_size(self):
        buf = AdvancedDataBuffer(max_size=5)
        for index in range(5):
            buf.add(self._make_data_point(float(index)))
        self.assertTrue(buf.is_full())

    def test_not_full_before_max_size(self):
        buf = AdvancedDataBuffer(max_size=5)
        buf.add(self._make_data_point(100.0))
        self.assertFalse(buf.is_full())

    def test_reset_clears_buffer(self):
        buf = AdvancedDataBuffer(max_size=5)
        for index in range(5):
            buf.add(self._make_data_point(float(index)))
        buf.reset()
        self.assertFalse(buf.is_full())
        self.assertEqual(len(buf.buffer), 0)

    def test_metrics_not_empty_when_full(self):
        buf = AdvancedDataBuffer(max_size=10)
        for index in range(10):
            buf.add(self._make_data_point(100.0 + index, setpoint=200.0))
        metrics = buf.calculate_advanced_metrics()
        self.assertIn("avg_error", metrics)
        self.assertGreater(metrics["avg_error"], 0)

    def test_metrics_empty_when_no_data(self):
        buf = AdvancedDataBuffer(max_size=10)
        self.assertEqual(buf.calculate_advanced_metrics(), {})


class SimulatorStepTests(unittest.TestCase):
    def test_constants_reasonable(self):
        self.assertGreater(SETPOINT, 0)
        self.assertGreater(INITIAL_TEMP, 0)
        self.assertLess(INITIAL_TEMP, SETPOINT)
        self.assertGreater(CONTROL_INTERVAL, 0)
        self.assertLess(CONTROL_INTERVAL, 1.0)

    def test_temp_increases_with_full_pwm(self):
        sim = HeatingSimulator(kp=10.0, ki=0.0, kd=0.0, random_seed=7)
        sim.pwm = 255.0
        for _ in range(50):
            sim.update()
        self.assertGreater(sim.temp, INITIAL_TEMP + 1.0)

    def test_temp_non_negative_after_many_steps(self):
        sim = HeatingSimulator(kp=2.0, ki=0.1, kd=0.05, random_seed=42)
        for _ in range(500):
            sim.compute_pid()
            sim.update()
        self.assertGreaterEqual(sim.temp, 0.0)

    def test_temp_does_not_diverge(self):
        sim = HeatingSimulator(kp=2.0, ki=0.1, kd=0.05, random_seed=42)
        for _ in range(500):
            sim.compute_pid()
            sim.update()
        self.assertLess(sim.temp, 600.0)

    def test_get_data_returns_required_keys(self):
        sim = HeatingSimulator(random_seed=7)
        data = sim.get_data()
        for key in ("timestamp", "setpoint", "input", "pwm", "error", "p", "i", "d"):
            self.assertIn(key, data)

    def test_set_pid_updates_parameters(self):
        sim = HeatingSimulator(random_seed=7)
        sim.set_pid(3.0, 0.5, 0.2)
        self.assertAlmostEqual(sim.kp, 3.0)
        self.assertAlmostEqual(sim.ki, 0.5)
        self.assertAlmostEqual(sim.kd, 0.2)

    def test_simulator_accepts_custom_setpoint(self):
        sim = HeatingSimulator(setpoint=300.0, random_seed=7)
        data = sim.get_data()
        self.assertEqual(data["setpoint"], 300.0)
        self.assertAlmostEqual(data["error"], 300.0 - sim.temp)

    def test_same_seed_reproduces_same_temperature_trace(self):
        left = HeatingSimulator(random_seed=123)
        right = HeatingSimulator(random_seed=123)

        left_trace = []
        right_trace = []
        for _ in range(60):
            left.compute_pid()
            left.update()
            right.compute_pid()
            right.update()
            left_trace.append(round(left.temp, 6))
            right_trace.append(round(right.temp, 6))

        self.assertEqual(left_trace, right_trace)

    def test_warm_start_updates_initial_pid(self):
        sim = HeatingSimulator(random_seed=7)
        pid = simulator._run_simulator_warm_start(sim, emit_console=False)
        self.assertIsNotNone(pid)
        self.assertGreater(sim.kp, 0.0)
        self.assertGreaterEqual(sim.ki, 0.0)
        self.assertGreaterEqual(sim.kd, 0.0)


if __name__ == "__main__":
    unittest.main()
