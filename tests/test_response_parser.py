import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.response_parser import (
    extract_json_candidates,
    parse_structured_text_response,
    parse_json_response,
    sanitize_result,
)


class ExtractJsonCandidatesTests(unittest.TestCase):
    def test_plain_json_is_candidate(self):
        text = '{"p": 1, "i": 2, "d": 3}'
        candidates = extract_json_candidates(text)
        self.assertIn(text, candidates)

    def test_fenced_json_extracted(self):
        text = '```json\n{"p": 1}\n```'
        candidates = extract_json_candidates(text)
        self.assertTrue(any('"p": 1' in c for c in candidates))

    def test_embedded_json_in_prose_extracted(self):
        text = 'Here is the suggestion: {"p": 2.5, "i": 0.1} — good luck.'
        candidates = extract_json_candidates(text)
        self.assertTrue(any('"p": 2.5' in c for c in candidates))

    def test_nested_objects_are_not_promoted_to_independent_suggestions(self):
        text = '{"controller_1": {"p": 1}}'
        candidates = extract_json_candidates(text)
        self.assertIn(text, candidates)
        self.assertEqual(candidates, [text])

    def test_embedded_json_ignores_braces_and_escaped_quotes_in_strings(self):
        text = 'Suggestion: {"analysis_summary":"Use } and \\"{\\" carefully", "p":2} end'
        parsed = parse_json_response(text)
        self.assertEqual(parsed["p"], 2.)
        self.assertEqual(parsed["analysis_summary"], 'Use } and "{" carefully')

    def test_no_json_returns_original_stripped(self):
        text = "   no json here   "
        candidates = extract_json_candidates(text)
        self.assertEqual(candidates, ["no json here"])

    def test_empty_text_returns_empty_list(self):
        self.assertEqual(extract_json_candidates(""), [])


class SanitizeResultTests(unittest.TestCase):
    def test_drops_negative_pid(self):
        result = sanitize_result({"p": -1.0, "i": 2.0, "d": 3.0})
        self.assertNotIn("p", result)
        self.assertEqual(result["i"], 2.0)
        self.assertEqual(result["d"], 3.0)

    def test_drops_nan_and_inf(self):
        result = sanitize_result({"p": float("nan"), "i": float("inf"), "d": 0.5})
        self.assertNotIn("p", result)
        self.assertNotIn("i", result)
        self.assertEqual(result["d"], 0.5)

    def test_coerces_string_numbers(self):
        result = sanitize_result({"p": "1.5", "i": "2", "d": "0"})
        self.assertEqual(result["p"], 1.5)
        self.assertEqual(result["i"], 2.0)
        self.assertEqual(result["d"], 0.0)

    def test_drops_non_numeric_pid(self):
        result = sanitize_result({"p": "abc", "i": None, "d": 1})
        self.assertNotIn("p", result)
        self.assertNotIn("i", result)
        self.assertEqual(result["d"], 1.0)

    def test_boolean_gains_are_not_numeric_measurements(self):
        result = sanitize_result({"p": True, "controller_2": {"i": False, "d": .1}})
        self.assertNotIn("p", result)
        self.assertEqual(result["controller_2"], {"d": .1})

    def test_huge_integer_gain_is_dropped_without_crashing_the_response(self):
        parsed = parse_json_response('{"p":' + '9' * 400 + ',"i":0.1}')
        self.assertNotIn("p", parsed)
        self.assertEqual(parsed["i"], .1)
        # CPython may reject extremely long JSON integers before sanitization.
        self.assertIsNone(parse_json_response('{"p":' + '9' * 5000 + '}'))

    def test_named_parameters_default_to_the_formula_action(self):
        self.assertEqual(sanitize_result({"parameters": {"gain": -2}})["tuning_action"], "ADJUST_PARAMETERS")

    def test_status_normalized_to_done_or_tuning(self):
        self.assertEqual(sanitize_result({"status": "done"})["status"], "DONE")
        self.assertEqual(sanitize_result({"status": "DONE"})["status"], "DONE")
        self.assertEqual(sanitize_result({"status": "other"})["status"], "TUNING")

    def test_fallback_analysis_and_thought_process(self):
        result = sanitize_result({})
        self.assertTrue(result["analysis_summary"])
        self.assertTrue(result["thought_process"])
        self.assertEqual(result["tuning_action"], "ADJUST_PID")

    def test_thought_process_uses_analysis_summary_when_missing(self):
        result = sanitize_result({"analysis_summary": "sum"})
        self.assertEqual(result["thought_process"], "sum")

    def test_controller_mapping_sanitized(self):
        result = sanitize_result(
            {"controller_1": {"p": 1.0, "i": "bad", "d": -1.0}}
        )
        self.assertEqual(result["controller_1"], {"p": 1.0})

    def test_controller_mapping_removed_when_all_invalid(self):
        result = sanitize_result({"controller_2": {"p": -1, "i": "bad"}})
        self.assertNotIn("controller_2", result)


class ParseJsonResponseTests(unittest.TestCase):
    def test_parses_plain_json(self):
        parsed = parse_json_response('{"p": 1.0, "i": 0.5, "d": 0.1, "status": "tuning"}')
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["p"], 1.0)
        self.assertEqual(parsed["status"], "TUNING")

    def test_parses_fenced_json(self):
        parsed = parse_json_response('prelude ```json\n{"p": 2}\n```')
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["p"], 2.0)

    def test_returns_none_on_unparseable(self):
        self.assertIsNone(parse_json_response("no json at all"))

    def test_returns_none_on_non_dict_json(self):
        self.assertIsNone(parse_json_response("[1, 2, 3]"))
        self.assertIsNone(parse_json_response('[{"p":99,"i":9,"d":1}]'))
        self.assertIsNone(parse_json_response('Response: [{"p":99,"i":9,"d":1}]'))

    def test_old_gains_in_plain_prose_or_malformed_json_are_not_suggestions(self):
        self.assertIsNone(parse_json_response('Previously P=99, I=9, D=1; continue observing.'))
        self.assertIsNone(parse_json_response('{"analysis_summary":"Previously P=99, I=9, D=1", broken}'))

    def test_plain_pid_triplet_remains_supported(self):
        self.assertEqual(parse_json_response('P=2, I=.2, D=.01')["p"], 2.)

    def test_parses_structured_single_controller_text(self):
        parsed = parse_json_response(
            """
            [Thought] Response is still a bit slow.
            [Analysis] Stable with low overshoot.
            [Action] Increase I slightly.
            [PID] P=0.12, I=5.8, D=0.05
            [Status] TUNING
            """
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["p"], 0.12)
        self.assertEqual(parsed["i"], 5.8)
        self.assertEqual(parsed["status"], "TUNING")

    def test_parses_structured_dual_controller_text(self):
        parsed = parse_structured_text_response(
            """
            [思考] 主环继续优化，副环保持稳定。
            [分析] Round 11 is near the best region.
            [调参] 回退主环增益。
            [控制器 1] P=0.12, I=5.8, D=0.05
            [控制器 2] P=1.0, I=0.1, D=0.05
            [状态] TUNING
            """
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["controller_1"]["p"], 0.12)
        self.assertEqual(parsed["controller_2"]["i"], 0.1)
        self.assertEqual(parsed["status"], "TUNING")

    def test_structured_text_without_pid_returns_none(self):
        self.assertIsNone(
            parse_structured_text_response(
                """
                [思考] 只有分析，没有参数。
                [分析] Continue observing.
                """
            )
        )

    def test_reasoning_and_analysis_cannot_supply_a_write(self):
        for section in ("Thought", "思考", "Analysis", "分析"):
            with self.subTest(section=section):
                parsed = parse_structured_text_response(
                    f"[{section}] Previous gains were P=99, I=9, D=1.\n[Action] Continue observing.\n[Status] TUNING"
                )
                self.assertIsNone(parsed)

    def test_explicit_pid_section_takes_precedence_over_old_gains_in_reasoning(self):
        parsed = parse_structured_text_response(
            "[Thought] Previously P=99, I=9, D=1\n[PID] P=2, I=.2, D=.01\n[Status] TUNING"
        )
        self.assertEqual(parsed["p"], 2.)


if __name__ == "__main__":
    unittest.main()
