"""Event-publish helpers shared by simulator.py and tuner.py tuning loops."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from sim.runtime import (
    EVENT_DECISION,
    EVENT_ROLLBACK,
    EVENT_ROUND_METRICS,
    QueueEventSink,
    publish_event,
)


def publish_round_metrics(
    event_sink: Optional[QueueEventSink], evaluation: Any, round_index: int
) -> None:
    m = evaluation.metrics
    publish_event(
        event_sink, EVENT_ROUND_METRICS,
        round=round_index,
        avg_error=float(m["avg_error"]),
        max_error=float(m["max_error"]),
        steady_state_error=float(m["steady_state_error"]),
        overshoot=float(m["overshoot"]),
        zero_crossings=int(m["zero_crossings"]),
        status=str(m["status"]),
        stable_rounds=evaluation.stable_rounds,
    )


def publish_decision(
    event_sink: Optional[QueueEventSink], round_index: int, decision: Any
) -> None:
    publish_event(
        event_sink, EVENT_DECISION,
        round=round_index,
        action=decision.action,
        analysis_summary=decision.analysis,
        fallback_used=decision.fallback_used,
        guardrail_notes=list(decision.guardrail_notes),
    )


def flatten_controller_result(
    result: Dict[str, Any], current_pid: Dict[str, float]
) -> Tuple[Dict[str, Any], Dict[str, Any] | None, Dict[str, Any] | None]:
    """Flatten a dual-controller LLM result into root-level p/i/d.

    Returns ``(result_out, primary, secondary)``. When ``controller_1`` is a
    dict, ``result_out`` is a new dict with ``primary``'s p/i/d promoted to the
    top level; otherwise ``result_out`` is returned unchanged with
    ``primary=None``.
    """
    primary = result.get("controller_1") if isinstance(result.get("controller_1"), dict) else None
    secondary = result.get("controller_2") if isinstance(result.get("controller_2"), dict) else None
    if primary:
        result = {
            "p": float(primary.get("p", current_pid["p"])),
            "i": float(primary.get("i", current_pid["i"])),
            "d": float(primary.get("d", current_pid["d"])),
            "analysis_summary": result.get("analysis_summary", ""),
            "thought_process": result.get("thought_process", ""),
            "tuning_action": result.get("tuning_action", "ADJUST_PID"),
            "status": result.get("status", "TUNING"),
        }
    return result, primary, secondary


def publish_rollback(
    event_sink: Optional[QueueEventSink],
    round_index: int,
    evaluation: Any,
    rollback_pid: Dict[str, float],
    reason: str,
) -> None:
    target_round = (
        int(evaluation.best_result["round"])
        if evaluation.best_result
        else round_index
    )
    publish_event(
        event_sink, EVENT_ROLLBACK,
        round=round_index,
        target_round=target_round,
        pid=dict(rollback_pid),
        reason=reason,
    )
