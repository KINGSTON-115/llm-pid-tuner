#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/i18n.py 单元测试：语言检测、规范化与 tr()。"""

import os
import sys
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import i18n


def _fake_windll(*, return_value=None, side_effect=None):
    return SimpleNamespace(
        kernel32=SimpleNamespace(
            GetUserDefaultUILanguage=Mock(
                return_value=return_value,
                side_effect=side_effect,
            )
        )
    )


class TestSetLanguageGetLanguageTr(unittest.TestCase):
    def setUp(self):
        self._saved = i18n.get_language()

    def tearDown(self):
        i18n.set_language(self._saved)

    def test_tr_returns_zh_when_zh(self):
        i18n.set_language("zh")
        self.assertEqual(i18n.tr("中文", "English"), "中文")

    def test_tr_returns_en_when_en(self):
        i18n.set_language("en")
        self.assertEqual(i18n.tr("中文", "English"), "English")

    def test_set_language_normalizes_zh_cn(self):
        i18n.set_language("zh_CN")
        self.assertEqual(i18n.get_language(), "zh")

    def test_set_language_normalizes_en_gb(self):
        i18n.set_language("en_GB")
        self.assertEqual(i18n.get_language(), "en")

    def test_set_language_unknown_falls_back_to_en(self):
        i18n.set_language("fr")
        self.assertEqual(i18n.get_language(), "en")

    def test_set_language_empty_string(self):
        i18n.set_language("")
        self.assertEqual(i18n.get_language(), "en")


class TestLocaleFromEnv(unittest.TestCase):
    def test_lc_all_takes_precedence_over_lang(self):
        with patch.dict(
            os.environ,
            {
                "LC_ALL"    : "zh_CN.UTF-8",
                "LC_MESSAGES": "",
                "LC_CTYPE"  : "",
                "LANG"      : "en_US.UTF-8",
            },
            clear=False,
        ):
            self.assertIn("zh", i18n._locale_from_env())


class TestDetectLanguageNonWindows(unittest.TestCase):
    """在非 Windows 下通过环境变量推断语言（避免依赖本机 UI 语言）。"""

    @patch.object(i18n.sys, "platform", "linux")
    def test_lang_zh_cn(self):
        with patch.dict(
            os.environ,
            {
                "LC_ALL"     : "",
                "LC_MESSAGES": "",
                "LC_CTYPE"   : "",
                "LANG"       : "zh_CN.UTF-8",
            },
            clear=False,
        ):
            self.assertEqual(i18n._detect_language(), "zh")

    @patch.object(i18n.sys, "platform", "linux")
    def test_lang_en_us(self):
        with patch.dict(
            os.environ,
            {
                "LC_ALL"     : "",
                "LC_MESSAGES": "",
                "LC_CTYPE"   : "",
                "LANG"       : "en_US.UTF-8",
            },
            clear=False,
        ):
            self.assertEqual(i18n._detect_language(), "en")


class TestDetectLanguageWindows(unittest.TestCase):
    """Windows：模拟 GetUserDefaultUILanguage 返回值。"""

    @patch.object(i18n.sys, "platform", "win32")
    def test_ui_language_chinese(self):
        with patch.dict(
            os.environ,
            {"LANG": ""},
            clear=False,
        ):
            with patch(
                "ctypes.windll",
                new=_fake_windll(return_value=0x0804),
                create=True,
            ):
                self.assertEqual(i18n._detect_language(), "zh")

    @patch.object(i18n.sys, "platform", "win32")
    def test_ui_language_english(self):
        with patch.dict(
            os.environ,
            {"LANG": ""},
            clear=False,
        ):
            with patch(
                "ctypes.windll",
                new=_fake_windll(return_value=0x0409),
                create=True,
            ):
                self.assertEqual(i18n._detect_language(), "en")

    @patch.object(i18n.sys, "platform", "win32")
    def test_ctypes_failure_falls_back_to_lang_env(self):
        with patch(
            "ctypes.windll",
            new=_fake_windll(side_effect=RuntimeError("no dll")),
            create=True,
        ):
            with patch.dict(
                os.environ,
                {
                    "LC_ALL"      : "",
                    "LC_MESSAGES" : "",
                    "LC_CTYPE"    : "",
                    "LANG"        : "zh_CN.UTF-8",
                },
                clear=False,
            ):
                self.assertEqual(i18n._detect_language(), "zh")


if __name__ == "__main__":
    unittest.main()
