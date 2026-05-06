import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent))

from core.i18n import get_language, set_language
from sim.pre_tuning_dialog import (
    PreTuningDialogError,
    collect_pre_tuning_preferences,
)


class PreTuningDialogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_language = get_language()

    def tearDown(self) -> None:
        set_language(self._original_language)

    def test_collect_preferences_supports_chinese_natural_language_dialog(self):
        set_language("en")
        summary_result = {
            "summary": "优先小超调，最多 2%，调参保守。",
            "goal_priority": "low_overshoot",
            "max_overshoot_percent": 2.0,
            "aggressiveness": "conservative",
            "hard_constraints": ["超调不要超过 2%"],
            "soft_preferences": ["宁可慢一点也别振荡"],
            "known_notes": "对象有一点滞后",
        }

        with patch("sim.pre_tuning_dialog._can_prompt", return_value=True):
            with patch(
                "builtins.input",
                side_effect=["1", "对象有一点滞后", "宁可慢一点也别振荡", ""],
            ):
                with patch(
                    "sim.pre_tuning_dialog._summarize_user_request",
                    return_value=summary_result,
                ):
                    with redirect_stdout(io.StringIO()):
                        context = collect_pre_tuning_preferences("Python Simulation")

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context["user_dialog_language"], "zh")
        self.assertEqual(context["user_goal_priority"], "low_overshoot")
        self.assertEqual(context["user_tuning_aggressiveness"], "conservative")
        self.assertEqual(context["user_max_overshoot_percent"], 2.0)
        self.assertEqual(context["user_known_notes"], "对象有一点滞后")
        self.assertEqual(context["user_hard_constraints"], ["超调不要超过 2%"])
        self.assertEqual(get_language(), "zh")

    def test_collect_preferences_supports_english_natural_language_dialog(self):
        set_language("zh")
        summary_result = {
            "summary": "Prioritize fast response and keep overshoot below 8%.",
            "goal_priority": "fast_response",
            "max_overshoot_percent": 8.0,
            "aggressiveness": "aggressive",
            "hard_constraints": ["Keep overshoot below 8%"],
            "soft_preferences": ["Reach the target quickly"],
            "known_notes": "Plant has visible delay",
        }

        with patch("sim.pre_tuning_dialog._can_prompt", return_value=True):
            with patch(
                "builtins.input",
                side_effect=["2", "Reach the target quickly", "Plant has visible delay", ""],
            ):
                with patch(
                    "sim.pre_tuning_dialog._summarize_user_request",
                    return_value=summary_result,
                ):
                    with redirect_stdout(io.StringIO()):
                        context = collect_pre_tuning_preferences("Simulink")

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context["user_dialog_language"], "en")
        self.assertEqual(context["user_goal_priority"], "fast_response")
        self.assertEqual(context["user_tuning_aggressiveness"], "aggressive")
        self.assertEqual(context["user_max_overshoot_percent"], 8.0)
        self.assertEqual(context["user_known_notes"], "Plant has visible delay")
        self.assertEqual(context["user_soft_preferences"], ["Reach the target quickly"])
        self.assertEqual(get_language(), "en")

    def test_collect_preferences_exits_when_summary_fails(self):
        set_language("en")
        with patch("sim.pre_tuning_dialog._can_prompt", return_value=True):
            with patch("builtins.input", side_effect=["2", "Keep it stable first", "No oscillation", ""]):
                with patch(
                    "sim.pre_tuning_dialog._summarize_user_request",
                    side_effect=PreTuningDialogError("model unavailable"),
                ):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        with self.assertRaises(SystemExit) as raised:
                            collect_pre_tuning_preferences("Python Simulation")

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("model unavailable", output.getvalue())

    def test_collect_preferences_exits_when_summary_returns_empty_result(self):
        set_language("en")
        with patch("sim.pre_tuning_dialog._can_prompt", return_value=True):
            with patch("builtins.input", side_effect=["2", "Keep it stable first", ""]):
                with patch(
                    "sim.pre_tuning_dialog._summarize_user_request",
                    return_value=None,
                ):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        with self.assertRaises(SystemExit) as raised:
                            collect_pre_tuning_preferences("Python Simulation")

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("empty or invalid LLM response", output.getvalue())


if __name__ == "__main__":
    unittest.main()
