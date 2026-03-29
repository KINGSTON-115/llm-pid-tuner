from __future__ import annotations

from dataclasses import field
from typing import Any

from core.buffer import AdvancedDataBuffer
from core.compat import slotted_dataclass
from core.config import CONFIG
from core.history import TuningHistory
from pid_safety import (
    apply_pid_guardrails,
    build_fallback_suggestion,
    is_good_enough,
    maybe_update_best_result,
    pid_equals,
    should_rollback_to_best,
)


@slotted_dataclass
class TuningSessionState:
    buffer: AdvancedDataBuffer
    history: TuningHistory
    good_enough_rules: dict[str, float]
    round_num: int = 0
    last_round: int = 0
    stable_rounds: int = 0
    best_result: dict[str, Any] | None = None
    last_metrics: dict[str, Any] = field(default_factory=dict)
    completed_reason: str = "max_rounds_reached"
    fallback_count: int = 0
    guardrail_count: int = 0
    rollback_count: int = 0


@slotted_dataclass
class RoundEvaluation:
    round_index: int
    metrics: dict[str, Any]
    current_pid: dict[str, float]
    stable_rounds: int
    best_result: dict[str, Any] | None = None
    best_result_updated: bool = False
    rollback_pid: dict[str, float] | None = None
    completed_reason: str | None = None


@slotted_dataclass
class DecisionOutcome:
    safe_pid: dict[str, float]
    action: str
    analysis: str
    thought: str
    guardrail_notes: list[str]
    fallback_used: bool
    status: str
    completed_reason: str | None = None


def create_tuning_session(
    *,
    initial_pid: dict[str, float] | None = None,
    setpoint: float | None = None,
    max_history: int = 5,
) -> TuningSessionState:
    buffer = AdvancedDataBuffer(max_size=CONFIG["BUFFER_SIZE"])
    if initial_pid is not None:
        buffer.current_pid = dict(initial_pid)
    if setpoint is not None:
        buffer.setpoint = float(setpoint)

    return TuningSessionState(
        buffer=buffer,
        history=TuningHistory(max_history=max_history),
        good_enough_rules={
            "avg_error_threshold": CONFIG["GOOD_ENOUGH_AVG_ERROR"],
            "steady_state_error_threshold": CONFIG["GOOD_ENOUGH_STEADY_STATE_ERROR"],
            "overshoot_threshold": CONFIG["GOOD_ENOUGH_OVERSHOOT"],
        },
    )


def evaluate_completed_round(
    state: TuningSessionState, current_pid: dict[str, float]
) -> RoundEvaluation:
    metrics = state.buffer.calculate_advanced_metrics()
    round_index = state.round_num + 1
    # 检测重试：同一轮因 pause 被中断后重新进入，last_round 已等于 round_index
    is_retry = state.last_round == round_index
    state.last_round = round_index
    state.last_metrics = dict(metrics)

    if is_retry:
        # 重试时不累加 stable_rounds，保留上次结果，避免同一批数据重复计分
        pass
    elif is_good_enough(metrics, state.good_enough_rules):
        state.stable_rounds += 1
    else:
        state.stable_rounds = 0

    previous_best = state.best_result
    state.best_result = maybe_update_best_result(
        state.best_result, current_pid, metrics, round_index
    )
    best_result_updated = (
        state.best_result is not None and state.best_result is not previous_best
    )

    rollback_pid: dict[str, float] | None = None
    completed_reason: str | None = None
    if (
        state.best_result
        and not pid_equals(current_pid, state.best_result["pid"])
        and should_rollback_to_best(metrics, state.best_result["metrics"])
    ):
        rollback_pid = dict(state.best_result["pid"])
        if is_good_enough(state.best_result["metrics"], state.good_enough_rules):
            completed_reason = "rollback_to_best"
    elif (
        metrics["avg_error"] < CONFIG["MIN_ERROR_THRESHOLD"]
        and metrics["status"] == "STABLE"
    ):
        completed_reason = "low_error_converged"
    elif state.stable_rounds >= CONFIG["REQUIRED_STABLE_ROUNDS"]:
        completed_reason = "stable_rounds_reached"

    return RoundEvaluation(
        round_index=round_index,
        metrics=metrics,
        current_pid=dict(current_pid),
        stable_rounds=state.stable_rounds,
        best_result=state.best_result,
        best_result_updated=best_result_updated,
        rollback_pid=rollback_pid,
        completed_reason=completed_reason,
    )


def apply_rollback(state: TuningSessionState, rollback_pid: dict[str, float]) -> None:
    state.rollback_count += 1
    state.round_num += 1
    state.buffer.current_pid = dict(rollback_pid)
    state.buffer.reset()


def record_rollback_round(
    state: TuningSessionState,
    evaluation: RoundEvaluation,
    rollback_pid: dict[str, float],
    *,
    target_round: int | None = None,
) -> str:
    target_label = (
        f"round {target_round}" if target_round is not None else "the best stable round"
    )
    analysis = (
        "Automatic rollback triggered because this round regressed against "
        f"{target_label}. Reverted to "
        f"P={rollback_pid['p']:.4f}, I={rollback_pid['i']:.4f}, D={rollback_pid['d']:.4f}."
    )
    thought = (
        "This round was evaluated with "
        f"P={evaluation.current_pid['p']:.4f}, I={evaluation.current_pid['i']:.4f}, D={evaluation.current_pid['d']:.4f}. "
        "Its response was worse than the current best stable result, so the attempt was rejected."
    )
    state.history.add_record(
        evaluation.round_index,
        evaluation.current_pid,
        evaluation.metrics,
        analysis,
        thought,
    )
    return analysis


def finalize_decision(
    state: TuningSessionState,
    evaluation: RoundEvaluation,
    result: dict[str, Any] | None,
) -> DecisionOutcome:
    if not result:
        result = build_fallback_suggestion(evaluation.current_pid, evaluation.metrics)

    safe_pid, guardrail_notes = apply_pid_guardrails(evaluation.current_pid, result)
    analysis = str(result.get("analysis_summary", "No analysis summary was provided."))
    thought = str(result.get("thought_process", ""))
    action = str(result.get("tuning_action", "UNKNOWN"))
    fallback_used = bool(result.get("fallback_used"))
    status = str(result.get("status", "TUNING")).upper()

    state.history.add_record(
        evaluation.round_index,
        evaluation.current_pid,
        evaluation.metrics,
        analysis,
        thought,
    )
    state.buffer.current_pid = dict(safe_pid)
    if fallback_used:
        state.fallback_count += 1
    if guardrail_notes:
        state.guardrail_count += 1
    state.round_num += 1
    state.buffer.reset()

    completed_reason = "llm_marked_done" if status == "DONE" else None
    return DecisionOutcome(
        safe_pid=safe_pid,
        action=action,
        analysis=analysis,
        thought=thought,
        guardrail_notes=list(guardrail_notes),
        fallback_used=fallback_used,
        status=status,
        completed_reason=completed_reason,
    )


def build_tuning_result(
    state: TuningSessionState, *, final_pid: dict[str, float], stopped: bool
) -> dict[str, Any]:
    return {
        "provider": CONFIG["LLM_PROVIDER"],
        "model": CONFIG["LLM_MODEL_NAME"],
        "rounds_completed": state.last_round,
        "final_pid": dict(final_pid),
        "final_metrics": dict(state.last_metrics),
        "stopped": stopped,
        "fallback_count": state.fallback_count,
        "guardrail_count": state.guardrail_count,
        "rollback_count": state.rollback_count,
        "completed_reason": state.completed_reason,
    }
