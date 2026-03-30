import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import ensure_utf8_console


def _fake_windll():
    return SimpleNamespace(
        kernel32=SimpleNamespace(
            SetConsoleOutputCP=Mock(),
            SetConsoleCP=Mock(),
        )
    )


class _FakeTTYStream:
    def __init__(self, encoding: str = "cp936"):
        self.encoding = encoding
        self.buffer = io.BytesIO()
        self.reconfigure = Mock(side_effect=self._apply_reconfigure)

    def _apply_reconfigure(self, **kwargs):
        self.encoding = kwargs.get("encoding", self.encoding)

    def isatty(self):
        return True


class _FakePipeStream:
    def __init__(self, encoding: str = "cp936"):
        self.encoding = encoding
        self.buffer = io.BytesIO()

    def isatty(self):
        return False


class EnsureUtf8ConsoleTests(unittest.TestCase):
    @patch("core.config.sys.platform", "win32")
    def test_reconfigures_tty_streams_in_place(self):
        stdout = _FakeTTYStream()
        stderr = _FakeTTYStream()

        with patch("ctypes.windll", new=_fake_windll(), create=True):
            with patch("core.config.sys.stdout", stdout):
                with patch("core.config.sys.stderr", stderr):
                    ensure_utf8_console()
                    self.assertIs(sys.modules["core.config"].sys.stdout, stdout)
                    self.assertIs(sys.modules["core.config"].sys.stderr, stderr)

        stdout.reconfigure.assert_called_once_with(
            encoding="utf-8", line_buffering=True
        )
        stderr.reconfigure.assert_called_once_with(
            encoding="utf-8", line_buffering=True
        )
        self.assertEqual(stdout.encoding, "utf-8")
        self.assertEqual(stderr.encoding, "utf-8")

    @patch("core.config.sys.platform", "win32")
    def test_wraps_non_tty_streams_without_reconfigure(self):
        stdout = _FakePipeStream()
        stderr = _FakePipeStream()

        with patch("ctypes.windll", new=_fake_windll(), create=True):
            with patch("core.config.sys.stdout", stdout):
                with patch("core.config.sys.stderr", stderr):
                    ensure_utf8_console()
                    wrapped_stdout = sys.modules["core.config"].sys.stdout
                    wrapped_stderr = sys.modules["core.config"].sys.stderr

        self.assertIsNot(wrapped_stdout, stdout)
        self.assertIsNot(wrapped_stderr, stderr)
        self.assertEqual(getattr(wrapped_stdout, "encoding", "").lower(), "utf-8")
        self.assertEqual(getattr(wrapped_stderr, "encoding", "").lower(), "utf-8")


if __name__ == "__main__":
    unittest.main()
