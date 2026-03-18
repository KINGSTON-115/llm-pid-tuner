import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent))

import simulator
from core.buffer import AdvancedDataBuffer
from core.config import CONFIG, load_config
from llm.client import LLMTuner
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
