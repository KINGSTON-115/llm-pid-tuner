"""
tests/test_regression.py - 核心模块回归测试

覆盖：
1. config 加载：load_config() 能读取 config.json，缺失键有合理默认值
2. LLM fallback：无可用 SDK 时 LLMTuner 正常初始化（use_sdk=False），
   _parse_json 能从合法 JSON 字符串中提取参数
3. AdvancedDataBuffer：基础功能（add / is_full / reset / calculate_advanced_metrics）
4. HeatingSimulator：物理行为正确性（温度不爆炸、不为负、有效响应）
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import CONFIG, load_config
from core.buffer import AdvancedDataBuffer
from llm.client import LLMTuner
from sim.model import (
    HeatingSimulator,
    CONTROL_INTERVAL,
    INITIAL_TEMP,
    SETPOINT,
)


# ---------------------------------------------------------------------------
# 1. config 加载
# ---------------------------------------------------------------------------


class ConfigLoadTests(unittest.TestCase):
    def test_defaults_present(self):
        """CONFIG 默认值应包含所有关键键"""
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
            self.assertIn(key, CONFIG, f"CONFIG 缺少键: {key}")

    def test_default_types(self):
        """常用键的默认类型应正确"""
        self.assertIsInstance(CONFIG["BAUD_RATE"], int)
        self.assertIsInstance(CONFIG["BUFFER_SIZE"], int)
        self.assertIsInstance(CONFIG["MAX_TUNING_ROUNDS"], int)
        self.assertIsInstance(CONFIG["LLM_REQUEST_TIMEOUT"], int)
        self.assertIsInstance(CONFIG["LLM_DEBUG_OUTPUT"], bool)
        self.assertIsInstance(CONFIG["GOOD_ENOUGH_AVG_ERROR"], float)

    def test_load_config_does_not_raise(self):
        """load_config(create_if_missing=False) 在任何状态下不应抛出异常"""
        try:
            load_config(create_if_missing=False, verbose=False)
        except Exception as exc:
            self.fail(f"load_config raised: {exc}")


# ---------------------------------------------------------------------------
# 2. LLM fallback（SDK 不可用时仍能正常构造）
# ---------------------------------------------------------------------------


class LLMFallbackTests(unittest.TestCase):
    def _make_tuner_without_sdk(self, provider: str = "openai") -> LLMTuner:
        """构造 LLMTuner 时让 SDK import 强制失败，触发 HTTP 回退。
        sys.modules 中将 openai/anthropic 设为 None 即可使 import 抛出 ImportError。
        """
        with patch.dict("sys.modules", {"openai": None, "anthropic": None}):
            tuner = LLMTuner("fake-key", "https://fake.api/v1", "gpt-mock", provider)
        return tuner

    def test_fallback_when_sdk_missing(self):
        """SDK 不可用时 use_sdk 应为 False，不应抛出异常"""
        tuner = self._make_tuner_without_sdk("openai")
        self.assertFalse(tuner.use_sdk)

    def test_parse_json_extracts_pid(self):
        """_parse_json 应从合法 JSON 字符串中提取 p/i/d"""
        tuner  = self._make_tuner_without_sdk()
        raw    = '{"p": 1.5, "i": 0.2, "d": 0.01, "status": "TUNING", "analysis_summary": "ok"}'
        result = tuner._parse_json(raw)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["p"], 1.5)  # type: ignore[index]
        self.assertAlmostEqual(result["i"], 0.2)  # type: ignore[index]
        self.assertAlmostEqual(result["d"], 0.01)  # type: ignore[index]

    def test_parse_json_rejects_negative_pid(self):
        """_parse_json 应拒绝负数 PID 参数"""
        tuner = self._make_tuner_without_sdk()
        raw = '{"p": -1.0, "i": 0.1, "d": 0.05, "status": "TUNING"}'
        result = tuner._parse_json(raw)
        self.assertIsNotNone(result)
        self.assertNotIn("p", result)  # type: ignore[operator]

    def test_provider_resolution_openai(self):
        """'openai' provider 应解析为 openai transport"""
        tuner = self._make_tuner_without_sdk("openai")
        self.assertEqual(tuner.provider, "openai")

    def test_provider_resolution_anthropic(self):
        """'anthropic' provider 应解析为 anthropic transport"""
        tuner = self._make_tuner_without_sdk("anthropic")
        self.assertEqual(tuner.provider, "anthropic")


# ---------------------------------------------------------------------------
# 3. AdvancedDataBuffer 基础功能
# ---------------------------------------------------------------------------


class BufferTests(unittest.TestCase):
    def _make_data_point(self, temp: float, setpoint: float = 200.0) -> dict:
        return {
            "timestamp": 0,
            "setpoint" : setpoint,
            "input"    : temp,
            "pwm"      : 100.0,
            "error"    : setpoint - temp,
        }

    def test_is_full_after_max_size(self):
        buf = AdvancedDataBuffer(max_size=5)
        for i in range(5):
            buf.add(self._make_data_point(float(i)))
        self.assertTrue(buf.is_full())

    def test_not_full_before_max_size(self):
        buf = AdvancedDataBuffer(max_size=5)
        buf.add(self._make_data_point(100.0))
        self.assertFalse(buf.is_full())

    def test_reset_clears_buffer(self):
        buf = AdvancedDataBuffer(max_size=5)
        for i in range(5):
            buf.add(self._make_data_point(float(i)))
        buf.reset()
        self.assertFalse(buf.is_full())
        self.assertEqual(len(buf.buffer), 0)

    def test_metrics_not_empty_when_full(self):
        buf = AdvancedDataBuffer(max_size=10)
        for i in range(10):
            buf.add(self._make_data_point(100.0 + i, setpoint=200.0))
        metrics = buf.calculate_advanced_metrics()
        self.assertIn("avg_error", metrics)
        self.assertGreater(metrics["avg_error"], 0)

    def test_metrics_empty_when_no_data(self):
        buf = AdvancedDataBuffer(max_size=10)
        self.assertEqual(buf.calculate_advanced_metrics(), {})


# ---------------------------------------------------------------------------
# 4. HeatingSimulator 物理行为
# ---------------------------------------------------------------------------


class SimulatorStepTests(unittest.TestCase):
    def setUp(self):
        # 固定随机种子，使物理行为测试可重复
        import random

        random.seed(0)

    def test_constants_reasonable(self):
        """物理常量应在合理范围内"""
        self.assertGreater(SETPOINT, 0)
        self.assertGreater(INITIAL_TEMP, 0)
        self.assertLess(INITIAL_TEMP, SETPOINT)
        self.assertGreater(CONTROL_INTERVAL, 0)
        self.assertLess(CONTROL_INTERVAL, 1.0)

    def test_temp_increases_with_full_pwm(self):
        """全功率加热 50 步后温度应高于初始值"""
        sim = HeatingSimulator(kp=10.0, ki=0.0, kd=0.0)
        # 强制 pwm 全满，绕过 PID
        sim.pwm = 255.0
        for _ in range(50):
            sim.update()
        self.assertGreater(sim.temp, INITIAL_TEMP + 1.0)

    def test_temp_non_negative_after_many_steps(self):
        """正常 PID 下运行 500 步，温度不应为负"""
        import random

        random.seed(42)
        sim = HeatingSimulator(kp=2.0, ki=0.1, kd=0.05)
        for _ in range(500):
            sim.compute_pid()
            sim.update()
        self.assertGreaterEqual(sim.temp, 0.0)

    def test_temp_does_not_diverge(self):
        """正常 PID 下运行 500 步，温度不应超出物理上限（600°C）"""
        import random

        random.seed(42)
        sim = HeatingSimulator(kp=2.0, ki=0.1, kd=0.05)
        for _ in range(500):
            sim.compute_pid()
            sim.update()
        self.assertLess(sim.temp, 600.0)

    def test_get_data_returns_required_keys(self):
        """get_data() 应返回所有必要字段"""
        sim = HeatingSimulator()
        data = sim.get_data()
        for key in ("timestamp", "setpoint", "input", "pwm", "error", "p", "i", "d"):
            self.assertIn(key, data)

    def test_set_pid_updates_parameters(self):
        """set_pid() 应正确更新 kp/ki/kd"""
        sim = HeatingSimulator()
        sim.set_pid(3.0, 0.5, 0.2)
        self.assertAlmostEqual(sim.kp, 3.0)
        self.assertAlmostEqual(sim.ki, 0.5)
        self.assertAlmostEqual(sim.kd, 0.2)


if __name__ == "__main__":
    unittest.main()
