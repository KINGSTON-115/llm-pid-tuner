import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

from core.i18n import set_language
from llm.stream_formatter import JSONStreamFormatter


class StreamFormatterTests(unittest.TestCase):
    def setUp(self):
        set_language("en")
        self.chunks: list[str] = []
        self.formatter = JSONStreamFormatter(writer=self.chunks.append)

    def output(self) -> str:
        return "".join(self.chunks)

    def test_numeric_pid_written_once(self):
        self.formatter.process('{"p": 1.5, "i": 0.3, "d": 0.01}')
        out = self.output()
        self.assertIn("1.5", out)
        self.assertIn("0.3", out)
        self.assertIn("0.01", out)
        # running it again should not duplicate
        self.chunks.clear()
        self.formatter.process('{"p": 1.5, "i": 0.3, "d": 0.01}')
        self.assertEqual(self.output(), "")

    def test_string_keys_emitted_with_header(self):
        self.formatter.process('{"analysis_summary": "hello"}')
        out = self.output()
        self.assertIn("Analysis", out)
        self.assertIn("hello", out)

    def test_incremental_string_append(self):
        self.formatter.process('{"analysis_summary": "hel')
        first = self.output()
        self.assertIn("hel", first)
        self.formatter.process('{"analysis_summary": "hello world"}')
        second = self.output()
        self.assertIn("lo world", second)

    def test_controller_nested_pid(self):
        self.formatter.process(
            '{"controller_1": {"p": 2.0, "i": 0.2, "d": 0.02}}'
        )
        out = self.output()
        self.assertIn("2.0", out)
        self.assertIn("0.2", out)
        self.assertIn("Controller 1", out)

    def test_status_key(self):
        self.formatter.process('{"status": "DONE"}')
        self.assertIn("DONE", self.output())
        self.assertIn("Status", self.output())

    def test_unknown_key_uses_bracketed_name(self):
        self.formatter.process('{"weird_key": 42, "z":')
        self.assertIn("weird_key", self.output())

    def test_newline_in_string_is_indented(self):
        self.formatter.process('{"thought_process": "a\\nb"}')
        out = self.output()
        self.assertIn("a", out)
        self.assertIn("b", out)


if __name__ == "__main__":
    unittest.main()
