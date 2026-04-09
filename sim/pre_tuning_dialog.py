from __future__ import annotations

import sys
from typing import Any

from core.config import CONFIG
from core.i18n import get_language, set_language
from llm.client import LLMTuner
from llm.prompts import (
    build_pre_tuning_dialog_user_prompt,
    get_pre_tuning_dialog_system_prompt,
)


_LANGUAGE_OPTIONS = {
    "1": ("zh", {"zh": "中文", "en": "Chinese"}),
    "2": ("en", {"zh": "英文", "en": "English"}),
}

_TEXT = {
    "zh": {
        "language_prompt": "语言 / Language [1]: ",
        "language_1": "[1] 中文",
        "language_2": "[2] English",
        "title": "预调参对话",
        "intro": "请直接说出你的调参偏好、不能接受的情况、以及对象的已知特性。空行结束，直接回车跳过。",
        "input_prompt": "> ",
        "thinking": "[Guide] 正在整理你的偏好...",
        "summary": "[Guide] 已整理偏好：{summary}",
        "empty_skip": "[Guide] 未提供额外偏好，继续使用默认调参策略。",
        "fallback_summary": "用户要求：{user_text}",
        "priority_note": "显式用户偏好优先级高于默认调参启发式。",
    },
    "en": {
        "language_prompt": "Language / 语言 [2]: ",
        "language_1": "[1] 中文",
        "language_2": "[2] English",
        "title": "Pre-Tuning Conversation",
        "intro": "Describe your tuning preferences, unacceptable behavior, and known plant traits. Submit an empty line to finish, or press Enter immediately to skip.",
        "input_prompt": "> ",
        "thinking": "[Guide] Summarizing your preferences...",
        "summary": "[Guide] Preference summary: {summary}",
        "empty_skip": "[Guide] No extra preference provided. Continuing with the default tuning strategy.",
        "fallback_summary": "User request: {user_text}",
        "priority_note": "Treat explicit user preferences as stronger constraints than the default tuning heuristic.",
    },
}


def _can_prompt() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _text(lang: str, key: str, **kwargs: Any) -> str:
    template = _TEXT[lang][key]
    return template.format(**kwargs) if kwargs else template


def _resolve_choice(
    raw_value: str,
    *,
    options: dict[str, tuple[str, dict[str, str]]],
    default_key: str,
) -> tuple[str, dict[str, str]]:
    normalized = raw_value.strip()
    if not normalized:
        return options[default_key]

    if normalized in options:
        return options[normalized]

    lowered = normalized.lower()
    for value, labels in options.values():
        if lowered == value.lower():
            return value, labels
        if lowered in {labels["zh"].lower(), labels["en"].lower()}:
            return value, labels

    return options[default_key]


def _prompt_language() -> str:
    default_key = "1" if get_language() == "zh" else "2"
    print(_TEXT["zh"]["language_1"])
    print(_TEXT["en"]["language_2"])
    try:
        raw_value = input(_TEXT[get_language()]["language_prompt"])
    except EOFError:
        raw_value = ""
    selected_language, _labels = _resolve_choice(
        raw_value,
        options=_LANGUAGE_OPTIONS,
        default_key=default_key,
    )
    set_language(selected_language)
    return selected_language


def _collect_user_request(language: str) -> str:
    print("=" * 60)
    print(f"  {_text(language, 'title')}")
    print("=" * 60)
    print(_text(language, "intro"))

    lines: list[str] = []
    while True:
        try:
            raw_line = input(_text(language, "input_prompt"))
        except EOFError:
            break
        if not raw_line.strip():
            break
        lines.append(raw_line.strip())
    return "\n".join(lines).strip()


def _normalize_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _fallback_prompt_context(language: str, user_text: str) -> dict[str, Any]:
    summary = _text(language, "fallback_summary", user_text=user_text)
    return {
        "user_dialog_language": language,
        "user_preference_raw_request": user_text,
        "user_preference_summary": summary,
        "user_preference_priority_note": _text(language, "priority_note"),
        "user_goal_priority": "balanced",
        "user_tuning_aggressiveness": "normal",
        "user_hard_constraints": [],
        "user_soft_preferences": [],
    }


def _build_prompt_context_from_result(
    *,
    language: str,
    user_text: str,
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    if not result:
        return _fallback_prompt_context(language, user_text)

    summary = str(result.get("summary", "") or "").strip()
    if not summary:
        return _fallback_prompt_context(language, user_text)

    max_overshoot = result.get("max_overshoot_percent")
    try:
        max_overshoot_value = (
            None if max_overshoot is None else float(max_overshoot)
        )
    except (TypeError, ValueError):
        max_overshoot_value = None

    context: dict[str, Any] = {
        "user_dialog_language": language,
        "user_preference_raw_request": user_text,
        "user_preference_summary": summary,
        "user_preference_priority_note": _text(language, "priority_note"),
        "user_goal_priority": str(result.get("goal_priority", "balanced") or "balanced"),
        "user_tuning_aggressiveness": str(
            result.get("aggressiveness", "normal") or "normal"
        ),
        "user_hard_constraints": _normalize_string_list(
            result.get("hard_constraints", [])
        ),
        "user_soft_preferences": _normalize_string_list(
            result.get("soft_preferences", [])
        ),
    }
    if max_overshoot_value is not None:
        context["user_max_overshoot_percent"] = max_overshoot_value

    known_notes = str(result.get("known_notes", "") or "").strip()
    if known_notes:
        context["user_known_notes"] = known_notes
    return context


def _summarize_user_request(language: str, user_text: str) -> dict[str, Any] | None:
    tuner = LLMTuner(
        api_key=CONFIG["LLM_API_KEY"],
        base_url=CONFIG["LLM_API_BASE_URL"],
        model=CONFIG["LLM_MODEL_NAME"],
        provider=CONFIG["LLM_PROVIDER"],
        stream_callback=None,
        log_callback=None,
        emit_console=False,
        timeout=CONFIG.get("LLM_REQUEST_TIMEOUT", 60),
        debug_output=CONFIG.get("LLM_DEBUG_OUTPUT", False),
    )
    return tuner.request_json(
        system_prompt=get_pre_tuning_dialog_system_prompt(language),
        user_prompt=build_pre_tuning_dialog_user_prompt(user_text, language),
    )


def collect_pre_tuning_preferences(mode_label: str) -> dict[str, Any] | None:
    if not _can_prompt():
        return None

    language = _prompt_language()
    user_text = _collect_user_request(language)
    if not user_text:
        print(_text(language, "empty_skip"))
        return None

    print(_text(language, "thinking"))
    result = _summarize_user_request(language, user_text)
    context = _build_prompt_context_from_result(
        language=language,
        user_text=user_text,
        result=result,
    )
    print(_text(language, "summary", summary=context["user_preference_summary"]))
    return context


__all__ = [
    "collect_pre_tuning_preferences",
]
