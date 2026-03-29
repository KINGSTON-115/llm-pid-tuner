#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/i18n.py - 系统语言检测与多语言支持
"""

import locale
import os
import sys


def _locale_from_env() -> str:
    """
    POSIX 环境下 locale 优先级：LC_ALL > LC_MESSAGES / LC_CTYPE > LANG。
    取第一个非空变量用于语言推断（与仅读 LANG 相比更可靠）。
    """
    for key in ("LC_ALL", "LC_MESSAGES", "LC_CTYPE", "LANG"):
        val = os.environ.get(key, "").strip()
        if val:
            return val.lower()
    return ""


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

    lang_env = _locale_from_env()
    if "zh" in lang_env:
        return "zh"
    if "en" in lang_env:
        return "en"

    try:
        if sys.platform != "win32":
            for cat in (locale.LC_MESSAGES, locale.LC_CTYPE):
                try:
                    loc = locale.getlocale(cat)[0]
                except (TypeError, ValueError):
                    loc = None
                if loc and ("zh" in loc.lower() or "chinese" in loc.lower()):
                    return "zh"
            loc = locale.getlocale()[0]
            if loc and ("zh" in loc.lower() or "chinese" in loc.lower()):
                return "zh"
    except Exception:
        pass

    return "en"


def _normalize_language(lang: str) -> str:
    """
    将用户输入规范为 zh 或 en。
    支持 zh_CN、zh-TW、en_US 等常见 locale 写法；未知值回退为 en。
    """
    if not isinstance(lang, str):
        return "en"
    s = lang.strip().lower().replace("-", "_")
    if not s:
        return "en"
    if "." in s:
        s = s.split(".", 1)[0]
    primary = s.split("_")[0]
    if primary.startswith("zh") or primary.startswith("chinese"):
        return "zh"
    if primary.startswith("en"):
        return "en"
    return "en"


CURRENT_LANG = _detect_language()


def set_language(lang: str) -> None:
    global CURRENT_LANG
    CURRENT_LANG = _normalize_language(lang)


def get_language() -> str:
    return CURRENT_LANG


def tr(zh_text: str, en_text: str) -> str:
    return zh_text if CURRENT_LANG == "zh" else en_text
