#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PID 参数安全护栏。

设计原则：
1. 不干预正常的小步调参；
2. 只限制明显危险的异常跳变；
3. 在 LLM 失败时提供保守、可解释的兜底策略。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple


PID_KEYS = ("p", "i", "d")

DEFAULT_PID_LIMITS: Dict[str, Dict[str, float]] = {
    "p": {"min": 0.0, "max": 100.0, "max_increase_ratio": 3.0},
    "i": {"min": 0.0, "max": 30.0, "max_increase_ratio": 4.0},
    "d": {"min": 0.0, "max": 20.0, "max_increase_ratio": 4.0},
}

DEFAULT_CONVERGENCE_RULES: Dict[str, float] = {
    "avg_error_threshold"         : 1.2,
    "steady_state_error_threshold": 0.3,
    "overshoot_threshold"         : 2.0,
}

DEFAULT_ROLLBACK_RULES: Dict[str, float] = {
    "avg_error_ratio"          : 1.5,
    "avg_error_margin"         : 0.5,
    "steady_state_error_ratio" : 1.8,
    "steady_state_error_margin": 0.25,
    "overshoot_margin"         : 1.0,
}


def _to_float(value: Any, fallback: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(numeric):
        return fallback
    return numeric


def apply_pid_guardrails(
    current_pid: Dict[str, float],
    candidate_pid: Dict[str, Any],
    limits: Dict[str, Dict[str, float]] | None = None,
) -> Tuple[Dict[str, float], List[str]]:
    """将候选 PID 参数裁剪到安全范围内。"""
    limits = limits or DEFAULT_PID_LIMITS
    sanitized: Dict[str, float] = {}
    notes: List[str] = []

    for key in PID_KEYS:
        current_value = max(0.0, _to_float(current_pid.get(key, 0.0), 0.0))
        raw_value = _to_float(candidate_pid.get(key, current_value), current_value)

        cfg = limits.get(key, DEFAULT_PID_LIMITS[key])
        bounded_value = max(cfg["min"], min(cfg["max"], raw_value))

        max_increase_ratio = max(1.0, cfg.get("max_increase_ratio", 1.0))
        if current_value > 0:
            max_step_value = min(cfg["max"], current_value * max_increase_ratio)
            if bounded_value > max_step_value:
                notes.append(
                    f"{key.upper()} 增幅过大，已从 {bounded_value:.4f} 限制到 {max_step_value:.4f}"
                )
                bounded_value = max_step_value
        elif bounded_value > cfg["max"]:
            notes.append(f"{key.upper()} 超出上限，已裁剪到 {cfg['max']:.4f}")

        if bounded_value != raw_value and not any(note.startswith(key.upper()) for note in notes):
            notes.append(f"{key.upper()} 已从 {raw_value:.4f} 调整到 {bounded_value:.4f}")

        sanitized[key] = bounded_value

    return sanitized, notes


def build_fallback_suggestion(current_pid: Dict[str, float], metrics: Dict[str, float]) -> Dict[str, Any]:
    """当 LLM 不可用时，用保守规则生成一个兜底建议。"""
    proposal = {
        "p": current_pid.get("p", 1.0),
        "i": current_pid.get("i", 0.1),
        "d": current_pid.get("d", 0.05),
    }

    status = str(metrics.get("status", "UNKNOWN")).upper()
    overshoot = float(metrics.get("overshoot", 0.0) or 0.0)
    steady_state_error = float(metrics.get("steady_state_error", 0.0) or 0.0)
    avg_error = float(metrics.get("avg_error", 0.0) or 0.0)

    if status == "OSCILLATING":
        proposal["p"] *= 0.80
        proposal["i"] *= 0.85
        proposal["d"] *= 1.20
        action = "DAMP_OSCILLATION"
        summary = "检测到震荡，保守降低 P/I 并增加 D。"
    elif status == "OVERSHOOTING" or overshoot > 5.0:
        proposal["p"] *= 0.85
        proposal["i"] *= 0.90
        proposal["d"] *= 1.15
        action = "REDUCE_OVERSHOOT"
        summary = "检测到超调，优先降低 P/I 并增加阻尼。"
    elif status == "SLOW_RESPONSE":
        proposal["p"] *= 1.25
        if steady_state_error > max(1.0, avg_error * 0.5):
            proposal["i"] *= 1.20
        action = "BOOST_RESPONSE"
        summary = "响应偏慢，适度增加 P，并在稳态误差偏大时增加 I。"
    elif steady_state_error > 1.0:
        proposal["i"] *= 1.15
        action = "REDUCE_STEADY_ERROR"
        summary = "系统基本稳定但仍有稳态误差，微增 I。"
    else:
        proposal["p"] *= 1.05
        proposal["i"] *= 1.05
        action = "FINE_TUNE"
        summary = "进入细调阶段，做小步修正。"

    safe_pid, notes = apply_pid_guardrails(current_pid, proposal)

    return {
        "analysis_summary": f"LLM 不可用，已启用保守兜底：{summary}",
        "thought_process" : "基于控制基础规则生成保守建议，仅用于兜底，不替代正常 LLM 调参。",
        "tuning_action"   : action,
        "p"               : safe_pid["p"],
        "i"               : safe_pid["i"],
        "d"               : safe_pid["d"],
        "status"          : "TUNING",
        "guardrail_notes" : notes,
        "fallback_used"   : True,
    }


def pid_equals(left: Dict[str, float], right: Dict[str, float], tolerance: float = 1e-9) -> bool:
    return all(abs(float(left.get(key, 0.0)) - float(right.get(key, 0.0))) <= tolerance for key in PID_KEYS)


def score_metrics(metrics: Dict[str, float]) -> float:
    """将控制表现压缩成一个可比较的分数，越低越好。"""
    avg_error = float(metrics.get("avg_error", 1e9) or 1e9)
    steady_state_error = float(metrics.get("steady_state_error", 1e9) or 1e9)
    overshoot = float(metrics.get("overshoot", 1e9) or 1e9)
    status = str(metrics.get("status", "UNKNOWN")).upper()

    status_penalty = 0.0
    if status == "OVERSHOOTING":
        status_penalty = 8.0
    elif status == "OSCILLATING":
        status_penalty = 12.0
    elif status != "STABLE":
        status_penalty = 20.0

    return avg_error + steady_state_error * 1.2 + overshoot * 0.6 + status_penalty


def is_better_metrics(candidate: Dict[str, float], baseline: Dict[str, float], epsilon: float = 1e-6) -> bool:
    return score_metrics(candidate) + epsilon < score_metrics(baseline)


def maybe_update_best_result(
    best_result: Dict[str, Any] | None,
    pid: Dict[str, float],
    metrics: Dict[str, float],
    round_num: int,
) -> Dict[str, Any] | None:
    """只记录稳定状态下的最佳 PID，避免回滚到坏参数。"""
    if str(metrics.get("status", "UNKNOWN")).upper() != "STABLE":
        return best_result

    candidate = {
        "round": round_num,
        "pid": {key: float(pid.get(key, 0.0)) for key in PID_KEYS},
        "metrics": dict(metrics),
    }

    if best_result is None or is_better_metrics(candidate["metrics"], best_result["metrics"]):
        return candidate
    return best_result


def is_good_enough(metrics: Dict[str, float], rules: Dict[str, float] | None = None) -> bool:
    """判断系统是否已经达到“用户可接受”的稳定状态。"""
    rules = rules or DEFAULT_CONVERGENCE_RULES
    status = str(metrics.get("status", "UNKNOWN")).upper()
    avg_error = float(metrics.get("avg_error", float("inf")) or float("inf"))
    steady_state_error = float(metrics.get("steady_state_error", float("inf")) or float("inf"))
    overshoot = float(metrics.get("overshoot", float("inf")) or float("inf"))

    return (
        status == "STABLE"
        and avg_error <= rules["avg_error_threshold"]
        and steady_state_error <= rules["steady_state_error_threshold"]
        and overshoot <= rules["overshoot_threshold"]
    )


def should_rollback_to_best(
    current_metrics: Dict[str, float],
    best_metrics: Dict[str, float],
    rules: Dict[str, float] | None = None,
) -> bool:
    """当前表现明显劣化时，建议回滚到历史最佳稳定参数。"""
    rules = rules or DEFAULT_ROLLBACK_RULES

    if not is_better_metrics(best_metrics, current_metrics):
        return False

    current_status = str(current_metrics.get("status", "UNKNOWN")).upper()
    best_status = str(best_metrics.get("status", "UNKNOWN")).upper()
    if best_status == "STABLE" and current_status != "STABLE":
        return True

    current_avg = float(current_metrics.get("avg_error", 1e9) or 1e9)
    best_avg = float(best_metrics.get("avg_error", 1e9) or 1e9)
    current_steady = float(current_metrics.get("steady_state_error", 1e9) or 1e9)
    best_steady = float(best_metrics.get("steady_state_error", 1e9) or 1e9)
    current_overshoot = float(current_metrics.get("overshoot", 1e9) or 1e9)
    best_overshoot = float(best_metrics.get("overshoot", 1e9) or 1e9)

    avg_regression = current_avg > max(best_avg * rules["avg_error_ratio"], best_avg + rules["avg_error_margin"])
    steady_regression = current_steady > max(
        best_steady * rules["steady_state_error_ratio"],
        best_steady + rules["steady_state_error_margin"],
    )
    overshoot_regression = current_overshoot > best_overshoot + rules["overshoot_margin"]

    return avg_regression or steady_regression or overshoot_regression
