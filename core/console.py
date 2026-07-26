#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small console/terminal helpers shared by the entry points."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from core.config import CONFIG


def can_prompt() -> bool:
    """True when both stdin and stdout are interactive terminals."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def append_console_log(text: str, *, end: str = "\n") -> None:
    """Best-effort append to logs/console_log.txt (mirrors console output)."""
    try:
        Path("logs").mkdir(parents=True, exist_ok=True)
        with open("logs/console_log.txt", "a", encoding="utf-8") as handle:
            handle.write(text + end)
    except Exception:
        pass


def choose_ui_mode(
    force_plain: bool, *, title: str, tui_label: str, plain_label: str
) -> bool:
    """Ask the user to pick TUI or plain output; plain is the default."""
    if force_plain:
        return False

    print(title)
    print(f"[1] {tui_label}")
    print(f"[2] {plain_label}")

    try:
        choice = input("Choose a mode [2]: ").strip().lower()
    except EOFError:
        return False
    return choice in {"1", "tui"}


def warn_tui_fallback(exc: BaseException) -> None:
    """Report a TUI startup failure before falling back to plain output."""
    print(f"[WARN] Failed to start the TUI ({exc}); falling back to plain output.")
    if bool(CONFIG.get("LLM_DEBUG_OUTPUT")):
        traceback.print_exc()
