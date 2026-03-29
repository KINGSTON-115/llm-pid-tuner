#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/i18n.py - 系统语言检测与多语言支持
"""

import locale
import os
import sys


def _detect_language() -> str:
    # 优先检测 Windows 操作系统的原生语言设置，
    # 避免受到终端模拟器 (如 Git Bash) 强制设置的 LANG=en_US.UTF-8 环境变量干扰。
    if sys.platform == "win32":
        try:
            import ctypes

            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            if lang_id & 0x00FF == 0x04:  # Chinese primary language ID
                return "zh"
            elif lang_id & 0x00FF == 0x09:  # English primary language ID
                return "en"
        except Exception:
            pass

    lang_env = os.environ.get("LANG", "").lower()
    if "zh" in lang_env:
        return "zh"
    if "en" in lang_env:
        return "en"

    try:
        if sys.platform != "win32":
            loc = locale.getlocale()[0]
            if loc and ("zh" in loc.lower() or "chinese" in loc.lower()):
                return "zh"
    except Exception:
        pass

    return "en"


CURRENT_LANG = _detect_language()


def set_language(lang: str) -> None:
    global CURRENT_LANG
    CURRENT_LANG = lang


def get_language() -> str:
    return CURRENT_LANG


def tr(zh_text: str, en_text: str) -> str:
    return zh_text if CURRENT_LANG == "zh" else en_text
