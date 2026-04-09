import sys
import types
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

from sim.prompt_context import (
    _first_nonempty_text,
    build_python_sim_prompt_context,
    build_simulink_prompt_context,
    default_prompt_context_for_mode,
    refresh_prompt_context_for_mode,
)


class FirstNonemptyTextTests(unittest.TestCase):
    def test_returns_first_nonempty(self):
        self.assertEqual(_first_nonempty_text("", "  ", "hello", "world"), "hello")

    def test_strips_whitespace(self):
        self.assertEqual(_first_nonempty_text("  hi  "), "hi")

    def test_none_treated_as_empty(self):
        self.assertEqual(_first_nonempty_text(None, None, "ok"), "ok")

    def test_all_empty_returns_empty_string(self):
        self.assertEqual(_first_nonempty_text("", None, "   "), "")

    def test_no_args_returns_empty_string(self):
        self.assertEqual(_first_nonempty_text(), "")


class BuildPythonSimContextTests(unittest.TestCase):
    def test_has_expected_keys(self):
        ctx = build_python_sim_prompt_context()
        self.assertEqual(ctx["source"], "built_in_python_heating_simulator")
        self.assertTrue(ctx["pwm_signal_available"])
        self.assertIn("per_round_guardrail_hint", ctx)


class BuildSimulinkContextTests(unittest.TestCase):
    def test_discrete_domain_uses_discrete_hint(self):
        ctx = build_simulink_prompt_context(
            "m.slx", "m/PID", "out", 0.01, control_domain="discrete"
        )
        self.assertEqual(ctx["control_domain"], "discrete")
        self.assertIn("discrete_tuning_hint", ctx)
        self.assertIn("Discrete-time", ctx["per_round_guardrail_hint"])

    def test_continuous_domain_uses_wider_guardrail(self):
        ctx = build_simulink_prompt_context("m.slx", "m/PID", "out", 0.01)
        self.assertIn("5x", ctx["per_round_guardrail_hint"])

    def test_pwm_placeholder_note_when_no_control_signal(self):
        ctx = build_simulink_prompt_context("m.slx", "m/PID", "out", 0.01)
        self.assertFalse(ctx["pwm_signal_available"])
        self.assertIn("placeholder", ctx["pwm_field_note"])

    def test_dual_controller_when_count_gt_1(self):
        ctx = build_simulink_prompt_context(
            "m.slx",
            "m/PID",
            "out",
            0.01,
            controller_count=2,
            controller_2_path="m/PID2",
        )
        self.assertEqual(ctx["controller_structure"], "dual_controller")
        self.assertEqual(ctx["controller_2_path"], "m/PID2")

    def test_output_signal_candidates_copied(self):
        ctx = build_simulink_prompt_context(
            "m.slx", "m/PID", "out", 0.01, output_signal_candidates=["a", "b"]
        )
        self.assertEqual(ctx["output_signal_candidates"], ["a", "b"])


class DefaultPromptContextForModeTests(unittest.TestCase):
    def test_python_sim_mode(self):
        ctx = default_prompt_context_for_mode(None, "python_sim")
        self.assertEqual(ctx["source"], "built_in_python_heating_simulator")

    def test_unknown_mode_returns_none(self):
        self.assertIsNone(default_prompt_context_for_mode(None, "hardware"))

    def test_simulink_with_empty_sim_returns_none(self):
        sim = types.SimpleNamespace(
            model_path="", pid_block_path="", output_signal="", sim_step_time=0.0
        )
        self.assertIsNone(default_prompt_context_for_mode(sim, "simulink"))

    def test_simulink_with_valid_sim_returns_context(self):
        sim = types.SimpleNamespace(
            model_path="m.slx",
            pid_block_path="m/PID",
            output_signal="out",
            sim_step_time=0.01,
            control_domain="",
            model_solver_type="",
            model_solver_name="",
            model_fixed_step="",
            controller_1_sample_time="",
            controller_2_sample_time="",
        )
        ctx = default_prompt_context_for_mode(sim, "simulink")
        self.assertEqual(ctx["source"], "matlab_simulink")
        self.assertEqual(ctx["model_path"], "m.slx")


class RefreshPromptContextForModeTests(unittest.TestCase):
    def test_returns_default_when_context_is_none(self):
        ctx = refresh_prompt_context_for_mode(None, "python_sim", None)
        self.assertEqual(ctx["source"], "built_in_python_heating_simulator")

    def test_non_simulink_returns_shallow_copy(self):
        original = {"source": "x", "foo": 1}
        refreshed = refresh_prompt_context_for_mode(None, "python_sim", original)
        self.assertEqual(refreshed, original)
        self.assertIsNot(refreshed, original)

    def test_simulink_refresh_uses_sim_attrs(self):
        sim = types.SimpleNamespace(
            model_path="new.slx",
            pid_block_path="new/PID",
            output_signal="temp",
            sim_step_time=0.02,
            resolved_control_signal="ctrl",
            control_signal="ctrl",
            resolved_output_signal="temp_out",
            secondary_pid_block_path="",
            setpoint_block="",
            control_domain="",
            model_solver_type="",
            model_solver_name="",
            model_fixed_step="",
            controller_1_sample_time="",
            controller_2_sample_time="",
            has_control_signal=True,
        )
        old_ctx = {"source": "matlab_simulink", "model_path": "old.slx"}
        refreshed = refresh_prompt_context_for_mode(sim, "simulink", old_ctx)
        self.assertEqual(refreshed["model_path"], "new.slx")
        self.assertTrue(refreshed["pwm_signal_available"])


if __name__ == "__main__":
    unittest.main()
