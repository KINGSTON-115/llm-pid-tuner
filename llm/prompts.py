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
根据系统的历史调参记录和当前响应数据，运用链式推理（Chain-of-Thought），给出下一轮的最优 PID 参数建议。

## 调参顺序（必须严格遵守）

**第一阶段：单独整定 P（比例项）**
- 将 I 和 D 保持在极小值（I 接近 0、D=0），只调整 P。
- 大步提升 P，直到响应速度满意（上升时间短）且超调量在 5% 以内为止。
- 在此阶段找到「合适的 P 区间」是首要目标，不要同时动 I 和 D。
- 若 P 过大导致超调超过 5%，适当回退 P；若响应仍慢，继续提升 P。

**第二阶段：在 P 稳定后引入 I（积分项）**
- 只有当 P 已经使响应速度达到满意水平后，才开始调整 I。
- I 的作用是消除稳态误差，从小值逐步提升，直到稳态误差趋近于零。
- 若加 I 后产生超调或振荡，说明 I 过大，需减小 I。
- 在此阶段 D 仍保持 0 或极小值，不要同时动 D。

**第三阶段：必要时引入 D（微分项）**
- 只有当 P+I 组合仍存在明显超调或振荡时，才引入 D 来抑制。
- D 从小值开始（如 0.1～1），逐步增大，观察超调是否减小。
- 若 D 过大会导致响应变慢（过度阻尼），需减小 D。
- 若 P+I 已达到理想效果（超调 <5%、稳态误差≈0），D 可保持 0。

## 核心原则
1. **严格按阶段调参**：不得跳跃阶段，不得在 P 未稳定时大幅调整 I 或 D，三个参数同时大幅变动是错误行为。
2. **稳定性第一**：等幅或发散振荡不可接受，超调须严格控制在 5% 以内。
3. **P 要大步探索**：P 阶段响应明显不足时，可一次提升 2-5 倍，快速找到有效增益区间，切勿过于保守。
4. **循证调参**：每次参数变化必须有数据依据；历史中已证明无效的方向不得重复。
5. **大步探索，小步精调**：离目标远时大幅调整，接近目标时切换为小幅微调。
6. **可信信号原则**：仅依赖明确提供的信号；若模式说明指出某字段为占位符，不得将其作为推理依据。

## 推理规范
- 先判断当前处于哪个调参阶段（P 阶段 / I 阶段 / D 阶段）。
- 分析当前轮次的响应速度、超调量、稳态误差。
- 与历史记录对比，识别参数变化的效果规律。
- 给出有逻辑支撑的参数建议，并说明处于哪个阶段、为何这样调整。
- 不得凭空推断被控对象的内部结构或不可见的测量值。

## 输出要求
必须严格输出一个合法的 JSON 对象，不包含任何 Markdown 标记或额外文字。
必填字段：thought_process、analysis_summary、tuning_action、p、i、d、status。
status 只能是 "TUNING" 或 "DONE"。
仅当 P+I（+D）组合已使响应速度达到极限（在不超调前提下无法再加快）且稳态误差趋近于零时，才输出 "DONE"。
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
- **重要：PWM 字段在本模式下为占位符（固定为 0.0），不代表真实控制输出，请完全忽略 PWM 数值，不得将其作为任何推理依据。**
- **重要：每轮调参均从仿真初始状态重新运行，因此系统输出每轮都从初始值开始，这是正常现象，不代表系统重置或故障。**
- **重要：时间序列中的 SimTime(ms) 字段是仿真时间（毫秒），不是真实世界时间。每轮仿真时长固定（如 15000ms = 15秒仿真），上升时间应以仿真毫秒数来评估，例如 SimTime 在 2000ms 内到达设定值即为快速响应。**
- 评估依据：专注于每轮仿真结束时的稳态误差（steady_state_error）、超调量（overshoot）和上升时间（从 SimTime 列估算，单位为仿真毫秒）。avg_error 受上升阶段影响较大，仅作参考。
- 调参目标：在超调 <5% 的前提下，把上升时间（仿真毫秒数）压到最短，同时稳态误差趋近于零。初始参数小时，应大幅提升 P（一次可提升 3-5 倍）快速找到有效增益区间。
- 如上下文提供了模型路径、PID 块路径、输出信号名称、仿真步长等信息，请将其作为背景知识辅助判断。
""".strip(),

    "hardware": """
## 运行模式：真实硬件串口调参
- 当前被控对象为通过串口连接的真实物理设备，存在传感器噪声、通信延迟、执行器限幅、量化误差和热滞后等工程约束。
- **必须采用保守策略**：每次参数变化幅度要小，充分观察系统响应后再决定下一步。
- 严禁激进调参：过大的 P 或 I 可能导致振荡甚至损坏设备。
- 证据不明确时，优先选择最安全的微调方案。
- 对于热力控制系统，超调可能造成不可逆后果，需格外谨慎。
- 调参目标：在保证绝对安全的前提下，逐步缩短响应时间并消除稳态误差。
""".strip(),
}

_MODE_TASK_LINES = {
    "generic": "分析本轮数据，结合历史记录，在超调 <3% 的前提下尽可能缩短上升时间，同时消除稳态误差。",
    "python_sim": "高效调优 Python 热力仿真，在超调 <3% 的前提下把响应速度推到极限，同时稳态误差趋近于零。",
    "simulink": "调优 Simulink 仿真 PID，在超调 <3% 的前提下把上升时间压到最短，稳态误差趋近于零。忽略 PWM 字段。",
    "hardware": "保守调优真实硬件控制回路，优先保障系统稳定性，严防振荡和危险超调，在安全前提下逐步提升响应速度。",
}


def normalize_tuning_mode(mode: str | None) -> str:
    normalized = str(mode or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _MODE_ALIASES.get(normalized, "generic")


def get_system_prompt(mode: str | None = None) -> str:
    resolved_mode = normalize_tuning_mode(mode)
    mode_notes = _MODE_NOTES.get(resolved_mode, _MODE_NOTES["generic"])
    return f"{_BASE_SYSTEM_PROMPT}\n\n{mode_notes}"


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
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        label = key.replace("_", " ")
        lines.append(f"- {label}: {_stringify_context_value(value)}")
    return "\n".join(lines) if len(lines) > 1 else ""


def build_user_prompt(
    prompt_data: str,
    history_text: str,
    tuning_mode: str | None = None,
    prompt_context: Mapping[str, Any] | None = None,
) -> str:
    resolved_mode = normalize_tuning_mode(tuning_mode)
    history_block = history_text.strip() or "暂无历史调参记录，请将本轮视为第一轮。"
    data_block = prompt_data.strip() or "本轮未提供响应数据。"
    context_block = _format_prompt_context(prompt_context)

    sections = [
        history_block,
        data_block,
    ]
    if context_block:
        sections.append(context_block)

    sections.append(
        "\n".join(
            [
                "## 本轮任务",
                f"- 当前模式：{resolved_mode}",
                f"- {_MODE_TASK_LINES.get(resolved_mode, _MODE_TASK_LINES['generic'])}",
                "- 请先对比本轮数据与历史记录的差异，再决定参数调整方向。",
                "- 仅输出 JSON，包含字段：thought_process、analysis_summary、tuning_action、p、i、d、status。",
            ]
        )
    )

    return "\n\n".join(section for section in sections if section)


SYSTEM_PROMPT = get_system_prompt("generic")

__all__ = [
    "SYSTEM_PROMPT",
    "build_user_prompt",
    "get_system_prompt",
    "normalize_tuning_mode",
]
