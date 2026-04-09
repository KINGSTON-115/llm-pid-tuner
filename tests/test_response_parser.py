import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.response_parser import (
    extract_json_candidates,
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

    def test_nested_braces_balanced(self):
        text = '{"controller_1": {"p": 1}}'
        candidates = extract_json_candidates(text)
        self.assertIn(text, candidates)
        self.assertTrue(any('{"p": 1}' in c for c in candidates))

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


if __name__ == "__main__":
    unittest.main()
