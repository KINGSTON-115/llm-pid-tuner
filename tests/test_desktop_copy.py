"""Language contracts and visual contrast for the native workbench."""
from string import Formatter
from unittest.mock import Mock

import pytest

from core.i18n import get_language, set_language
from desktop.i18n import TEXT, RUNTIME, RUNTIME_TEMPLATES, runtime, t
from desktop.session import RunOptions, _LocalizedTuner
from desktop.theme import PALETTES
from llm.prompts import get_system_prompt


@pytest.fixture(autouse=True)
def restore_language():
    previous = get_language()
    yield
    set_language(previous)


def fields(text):
    return {name for _, name, _, _ in Formatter().parse(text) if name is not None}


def test_all_ui_copy_has_matching_placeholders_in_both_languages():
    for key, pair in TEXT.items():
        assert len(pair) == 2 and all(pair), key
        assert fields(pair[0]) == fields(pair[1]), key
        values = {name: 1 for name in fields(pair[0])}
        for language, expected in zip(("zh", "en"), pair):
            set_language(language)
            assert t(key, **values) == expected.format(**values)


def test_existing_runtime_messages_translate_in_both_directions():
    for pair in RUNTIME + RUNTIME_TEMPLATES:
        assert fields(pair[0]) == fields(pair[1])
        values = {name: "2" for name in fields(pair[0])}
        for language, expected in zip(("zh", "en"), pair):
            set_language(language)
            for source in pair:
                assert runtime(source.format(**values)) == expected.format(**values)


def test_model_content_and_unknown_external_errors_stay_verbatim():
    set_language("en")
    raw = "provider says: 请求失败 (code=custom_error)"
    assert runtime(raw) == raw
    set_language("zh")
    assert runtime(raw) == raw


def test_offline_explanation_translates_recursively():
    set_language("en")
    assert runtime("离线规则 · 进入细调阶段，做小步修正。") == "Offline rules · Fine-tuning with small adjustments."
    set_language("zh")
    assert runtime("Offline rules · Fine-tuning with small adjustments.") == "离线规则 · 进入细调阶段，做小步修正。"


def test_validation_errors_follow_current_language():
    set_language("en")
    with pytest.raises(ValueError, match="finite numbers"):
        RunOptions(p=float("nan")).validate()
    set_language("zh")
    with pytest.raises(ValueError, match="有限数值"):
        RunOptions(p=float("nan")).validate()


def test_next_model_request_uses_live_language_without_mutating_context():
    client = Mock()
    tuner = _LocalizedTuner(client)
    context = {"controller_count": 2, "pid_limits": {"p": {"min": 0, "max": 5}}}
    for language, expected in (("en", "English"), ("zh", "Simplified Chinese")):
        set_language(language)
        tuner.analyze("samples", "history", tuning_mode="python_sim", prompt_context=context)
        passed = client.analyze.call_args.kwargs["prompt_context"]
        assert passed["response_language"] == language and passed["controller_count"] == 2
        prompt = get_system_prompt("python_sim", passed)
        assert "Write analysis_summary and thought_process in " + expected in prompt
        assert "Keep JSON field names, status, tuning_action and numeric values unchanged." in prompt
        assert "## PID Guardrails" in prompt
    assert "response_language" not in context
    assert "## Response language" not in get_system_prompt("python_sim", context)


def luminance(color):
    rgb = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
    return sum(v * w for v, w in zip(linear, (.2126, .7152, .0722)))


def contrast(a, b):
    light, dark = sorted((luminance(a), luminance(b)), reverse=True)
    return (light + .05) / (dark + .05)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_readable_text_and_chart_colors(theme):
    c = PALETTES[theme]
    for foreground, background in ((c.text, c.surface), (c.text, c.background),
                                  (c.muted, c.surface), (c.muted, c.raised),
                                  (c.accent_text, c.accent), (c.accent, c.accent_soft),
                                  (c.sidebar_text, c.sidebar), (c.danger, c.surface)):
        assert contrast(foreground, background) >= 4.5, (theme, foreground, background)
    for trace in (c.accent, c.output, c.target):
        assert contrast(trace, c.surface) >= 3, (theme, trace)
