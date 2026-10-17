#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
llm/prompts.py - 提示词选择与构建
"""

from __future__ import annotations

from typing import Any, Mapping


_MODE_ALIASES = {
    "generic": "generic",
    "default": "generic",
    "general": "generic",
    "python": "python_sim",
    "python_sim": "python_sim",
    "python_simulation": "python_sim",
    "sim": "python_sim",
    "simulation": "python_sim",
    "matlab": "simulink",
    "matlab_sim": "simulink",
    "matlab_simulink": "simulink",
    "simulink": "simulink",
    "hardware": "hardware",
    "serial": "hardware",
    "live": "hardware",
    "device": "hardware",
}

_BASE_SYSTEM_PROMPT = """
你是一位世界顶级的 PID 控制工程专家，精通经典控制理论、系统辨识与迭代调参方法论。

## 你的职责
根据系统的历史调参记录和当前响应数据，进行结构化分析，并给出下一轮的最优 PID 参数建议。

## 多控制器规则
- 默认按单控制器输出 `p/i/d`。
- 若上下文明确给出 `controller_count > 1` 或存在 `controller_2_path`，则必须分别为两组控制器给出建议。
- 双控制器输出时，使用：
  - `controller_1`: {`p`,`i`,`d`}
  - `controller_2`: {`p`,`i`,`d`}
- 同时保留顶层字段：`analysis_summary`、`tuning_action`、`status`。
- 双控制器下，默认不要把 `controller_1` 与 `controller_2` 机械设置为完全相同参数；只有在证据明确显示两回路动态等价时才允许同参，并在 `analysis_summary` 里写明理由。
- 若证据不足以调整第二控制器，可以让 `controller_2` 保持接近当前值，但字段必须存在。

## 循证调参策略
- 优先保留已验证有效的参数，不机械重置 I/D，也不强制每个系统按 P→I→D 顺序重新整定。
- 根据尾部误差、超调、振荡和真实控制量选择需要调整的参数。仅当证据表明积分饱和或阻尼不足时才针对性处理。
- 每轮优先改变证据最充分的一项；有独立模型或实测敏感度支持时才联合调整，避免不可归因的大幅变化。
- local_predictions 是独立仿真预测，不能当作实测结果。比较基准预测、候选预测和历史实测，失配时缩小步长并恢复模型分析。
- 历史已证明恶化的方向不得重复；接近目标时小步精调，证据不足时 OBSERVE。
- 用户约束和运行时护栏优先于默认超调目标（5%）；等幅或发散振荡不可接受。
- 只使用明确提供的信号，忽略占位控制量；真实硬件必须保守调整。

## 输出要求
必须严格输出一个合法的 JSON 对象，不包含任何 Markdown 标记或额外文字。
单控制器时必填字段：analysis_summary、tuning_action、p、i、d、status。
双控制器时必填字段：analysis_summary、tuning_action、controller_1、controller_2、status。
analysis_summary 仅用一句话说明数据依据与调整目的，不输出 thought_process 或展开推导。
tuning_action 可用 ADJUST_PID 或 OBSERVE。证据不足、需要继续观察时用 OBSERVE 并保持当前参数。
OBSERVE 最多跳过下一轮的一次模型调用；若指标恶化则恢复分析。DONE 仅是建议，程序按实测指标验收。
status 只能是 \"TUNING\" 或 \"DONE\"。
""".strip()

_MODE_NOTES = {
    "generic": """
## 运行模式：通用调参
- 适用于任何 PID 控制场景。
- 仅依赖所提供的数据和历史记录进行推理，不假设被控对象的具体物理特性。
""".strip(),
    "python_sim": """
## 运行模式：Python 内置热力仿真
- 当前被控对象为内置的单回路热力学仿真模型，噪声低、响应平滑。
- PWM 字段代表控制器的实际输出，数值真实可信，可作为调参依据。
- 由于是仿真环境，在证据充分的情况下，可以大幅提升参数以加速收敛，无需过于保守。
- 调参目标：在超调 <5% 的前提下，把上升时间压到最短，同时稳态误差趋近于零。
""".strip(),
    "simulink": """
## 运行模式：MATLAB/Simulink 仿真
- 当前被控对象为 MATLAB/Simulink 模型，属于仿真环境，不存在硬件损坏风险，可大胆调参。
- **重要：若上下文说明 PWM 为占位符（如固定为 0.0），请完全忽略 PWM 数值，不得将其作为任何推理依据。**
- **重要：每轮调参均从仿真初始状态重新运行，因此系统输出每轮都从初始值开始，这是正常现象。**
- **重要：时间序列中的 SimTime(ms) 字段是仿真时间（毫秒），不是真实世界时间。**
- 如上下文提供了模型路径、PID 块路径、输出信号、控制信号、仿真步长等信息，请将其作为背景知识辅助判断。
""".strip(),
    "hardware": """
## 运行模式：真实硬件串口调参
- 当前被控对象为通过串口连接的真实物理设备，存在传感器噪声、通信延迟、执行器限幅、量化误差和热滞后等工程约束。
- **必须采用保守策略**：每次参数变化幅度要小，充分观察系统响应后再决定下一步。
- 严禁激进调参：过大的 P 或 I 可能导致振荡甚至损坏设备。
- 证据不明确时，优先选择最安全的微调方案。
- 调参目标：在保证绝对安全的前提下，逐步缩短响应时间并消除稳态误差。
""".strip(),
}

_MODE_TASK_LINES = {
    "generic": "分析本轮数据，结合历史记录，在超调 <5% 的前提下尽可能缩短上升时间，同时消除稳态误差。",
    "python_sim": "高效调优 Python 热力仿真，在超调 <5% 的前提下把响应速度推到极限，同时稳态误差趋近于零。",
    "simulink": "调优 Simulink 仿真 PID，在超调 <5% 的前提下把上升时间压到最短，稳态误差趋近于零。忽略占位 PWM。",
    "hardware": "保守调优真实硬件控制回路，优先保障系统稳定性，严防振荡和危险超调，在安全前提下逐步提升响应速度。",
}


def normalize_tuning_mode(mode: str | None) -> str:
    normalized = str(mode or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _MODE_ALIASES.get(normalized, "generic")


def get_system_prompt(
    mode: str | None = None,
    prompt_context: Mapping[str, Any] | None = None,
) -> str:
    resolved_mode = normalize_tuning_mode(mode)
    if (prompt_context or {}).get("controller_formula"):
        import json
        language = (prompt_context or {}).get("response_language", "zh")
        return (
            "You tune the supplied controller expression graph using measured evidence. "
            "Follow its actual parameter dependencies and stateful discretization; do not assume PID stages or gain signs. "
            "Tune only declared parameter names within min/max and max_step. Missing signals are missing evidence; never invent them. "
            "Treat local_predictions as unverified forecasts, measured history as evidence. Respect user constraints and actuator limits. "
            "Return one JSON object: parameters (mapping of declared names to finite numbers), tuning_action "
            "(ADJUST_PARAMETERS or OBSERVE), status (TUNING or DONE), analysis_summary (one short sentence). "
            "OBSERVE holds every parameter; DONE is advisory and measured convergence is required. No thought_process. "
            "Do not emit p/i/d or controller_1/controller_2 unless they are the declared parameter names inside parameters. "
            + ("Write analysis_summary in Simplified Chinese." if language == "zh" else "Write analysis_summary in English.")
            + "\nController schema:\n" + json.dumps(prompt_context["controller_formula"], ensure_ascii=False, separators=(",", ":"))
        )
    mode_notes = _MODE_NOTES.get(resolved_mode, _MODE_NOTES["generic"])
    guardrail_section = _build_pid_guardrail_section(prompt_context)
    sections = [_BASE_SYSTEM_PROMPT, mode_notes]
    if guardrail_section:
        sections.append(guardrail_section)
    language = (prompt_context or {}).get("response_language")
    if language in ("zh", "en"):
        sections.append(
            "## Response language\nWrite analysis_summary and thought_process in "
            + ("Simplified Chinese" if language == "zh" else "English")
            + ". Keep JSON field names, status, tuning_action and numeric values unchanged."
        )
    return "\n\n".join(sections)


def _build_pid_guardrail_section(
    prompt_context: Mapping[str, Any] | None,
) -> str:
    if not prompt_context:
        return ""

    limits = prompt_context.get("pid_limits")
    if not isinstance(limits, Mapping):
        return ""

    lines = [
        "## PID Guardrails",
        "- The application enforces these PID bounds before sending values to the plant.",
        "- Choose PID values inside these bounds; do not rely on the application to clip unsafe output.",
    ]
    for key in ("p", "i", "d"):
        raw_gain_limits = limits.get(key)
        if not isinstance(raw_gain_limits, Mapping):
            continue
        min_value = raw_gain_limits.get("min")
        max_value = raw_gain_limits.get("max")
        ratio = raw_gain_limits.get("max_increase_ratio")
        lines.append(
            f"- {key.upper()}: min={_stringify_context_value(min_value)}, "
            f"max={_stringify_context_value(max_value)}, "
            f"max increase per round={_stringify_context_value(ratio)}x"
        )

    return "\n".join(lines) if len(lines) > 3 else ""


_PRE_TUNING_DIALOG_PROMPTS = {
    "zh": {
        "system": """
你是 PID 调参前的需求整理助手。
请把用户的自然语言偏好整理成一个简洁、可执行的 JSON 对象，供后续调参提示词使用。

规则：
1. 只输出 JSON。
2. 不要臆造用户没有明确表达的硬约束。
3. 如果用户没有给出明确的数值超调限制，`max_overshoot_percent` 输出 null。
4. `goal_priority` 只能是：`balanced`、`fast_response`、`low_overshoot`、`stability_first`。
5. `aggressiveness` 只能是：`conservative`、`normal`、`aggressive`。
6. `summary` 用一句话概括用户要求。
7. `hard_constraints` 和 `soft_preferences` 都必须是字符串数组；没有就输出空数组。
8. `known_notes` 是一句话说明对象已知特性；没有就输出空字符串。

输出 JSON 字段：
- `summary`
- `goal_priority`
- `max_overshoot_percent`
- `aggressiveness`
- `hard_constraints`
- `soft_preferences`
- `known_notes`
""".strip(),
        "user": """
请整理下面这段调参前说明，输出 JSON：

{user_text}
""".strip(),
    },
    "en": {
        "system": """
You are a pre-tuning preference extraction assistant for PID tuning.
Convert the user's free-form preference text into a concise JSON object for the later tuning prompt.

Rules:
1. Output JSON only.
2. Do not invent hard constraints that the user did not state.
3. If the user did not give a numeric overshoot limit, set `max_overshoot_percent` to null.
4. `goal_priority` must be one of: `balanced`, `fast_response`, `low_overshoot`, `stability_first`.
5. `aggressiveness` must be one of: `conservative`, `normal`, `aggressive`.
6. `summary` should be one short sentence.
7. `hard_constraints` and `soft_preferences` must be string arrays; use empty arrays when needed.
8. `known_notes` should be one short sentence about known plant traits; otherwise use an empty string.

Required JSON fields:
- `summary`
- `goal_priority`
- `max_overshoot_percent`
- `aggressiveness`
- `hard_constraints`
- `soft_preferences`
- `known_notes`
""".strip(),
        "user": """
Extract the tuning preferences from the following text and output JSON:

{user_text}
""".strip(),
    },
}


def get_pre_tuning_dialog_system_prompt(language: str) -> str:
    normalized = "zh" if str(language).strip().lower() == "zh" else "en"
    return _PRE_TUNING_DIALOG_PROMPTS[normalized]["system"]


def build_pre_tuning_dialog_user_prompt(user_text: str, language: str) -> str:
    normalized = "zh" if str(language).strip().lower() == "zh" else "en"
    return _PRE_TUNING_DIALOG_PROMPTS[normalized]["user"].format(
        user_text=user_text.strip()
    )


def _stringify_context_value(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (list, tuple, set)):
        return "、".join(_stringify_context_value(item) for item in value)
    return str(value)


def _format_prompt_context(prompt_context: Mapping[str, Any] | None) -> str:
    if not prompt_context:
        return ""

    lines = ["## 模型上下文信息"]
    for key, value in prompt_context.items():
        if key in {"pid_limits", "pid_limits_are_runtime_enforced", "user_preference_summary"}:
            continue  # Already included in the system/preference section.
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, tuple, set)) and not value:
            continue
        label = key.replace("_", " ")
        lines.append(f"- {label}: {_stringify_context_value(value)}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _requires_dual_controller_output(prompt_context: Mapping[str, Any] | None) -> bool:
    if not prompt_context:
        return False

    controller_count = prompt_context.get("controller_count")
    try:
        if int(controller_count) > 1:
            return True
    except (TypeError, ValueError):
        pass

    controller_2_path = str(prompt_context.get("controller_2_path", "") or "").strip()
    return bool(controller_2_path)


def _build_user_preference_section(
    prompt_context: Mapping[str, Any] | None,
) -> str:
    if not prompt_context:
        return ""

    summary = str(prompt_context.get("user_preference_summary", "") or "").strip()
    if not summary:
        return ""

    return "\n".join(
        [
            "## User Preferences",
            "- Treat explicit user preferences as stronger constraints than the default tuning heuristic.",
            f"- {summary}",
        ]
    )


def _build_controller_strategy_section(is_dual_controller: bool) -> str:
    if is_dual_controller:
        return "\n".join(
            [
                "## 双控制器调参策略（与单控制器不同）",
                "- 必须分别分析 controller_1 与 controller_2 的职责和调参方向，不能把两者当作同一个控制器。",
                "- 默认不要输出完全相同的两组 PID；若确实同参，必须在 analysis_summary 中明确说明证据。",
                "- 若第二控制器证据不足，优先让 controller_2 保持接近其当前参数，不要直接复制 controller_1。",
                "- 优先确保两环职责分离：主环关注目标跟踪，副环关注快速抑制扰动/振荡。",
            ]
        )

    return "\n".join(
        [
            "## 单控制器调参策略",
            "- 只输出一组 p/i/d，并围绕单回路响应持续迭代。",
            "- 不输出 controller_1/controller_2 字段。",
        ]
    )


def build_user_prompt(
    prompt_data: str,
    history_text: str,
    tuning_mode: str | None = None,
    prompt_context: Mapping[str, Any] | None = None,
) -> str:
    resolved_mode = normalize_tuning_mode(tuning_mode)
    if (prompt_context or {}).get("controller_formula"):
        import json
        context = {key: value for key, value in prompt_context.items()
                   if key not in {"pid_limits", "pid_limits_are_runtime_enforced", "response_language", "controller_formula"}}
        return "\n\n".join(["Measured history:\n" + history_text,
                              "Current measurements:\n" + prompt_data,
                              "Controller and constraints:\n" + json.dumps(context, ensure_ascii=False, separators=(",", ":")),
                              "Select a measured-evidence-based parameter update or OBSERVE. Return JSON only."])
    dual_controller = _requires_dual_controller_output(prompt_context)
    history_block = history_text.strip() or "暂无历史调参记录，请将本轮视为第一轮。"
    data_block = prompt_data.strip() or "本轮未提供响应数据。"
    context_block = _format_prompt_context(prompt_context)
    output_fields = (
        "analysis_summary、tuning_action、controller_1、controller_2、status"
        if dual_controller
        else "analysis_summary、tuning_action、p、i、d、status"
    )

    sections = [history_block, data_block]
    if context_block:
        sections.append(context_block)
    preference_block = _build_user_preference_section(prompt_context)
    if preference_block:
        sections.append(preference_block)

    sections.append(
        "\n".join(
            [
                "## 本轮任务",
                f"- 当前模式：{resolved_mode}",
                f"- {_MODE_TASK_LINES.get(resolved_mode, _MODE_TASK_LINES['generic'])}",
                "- 请先对比本轮数据与历史记录的差异，再决定参数调整方向。",
                f"- 仅输出 JSON，包含字段：{output_fields}。",
            ]
        )
    )
    sections.append(_build_controller_strategy_section(dual_controller))

    return "\n\n".join(section for section in sections if section)


SYSTEM_PROMPT = get_system_prompt("generic")

__all__ = [
    "SYSTEM_PROMPT",
    "build_pre_tuning_dialog_user_prompt",
    "build_user_prompt",
    "get_pre_tuning_dialog_system_prompt",
    "get_system_prompt",
    "normalize_tuning_mode",
]
