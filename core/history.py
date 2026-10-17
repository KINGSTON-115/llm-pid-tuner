#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/history.py - 调参历史记录管理器
"""

from collections import deque
from copy import deepcopy
from typing import Any, Dict


class TuningHistory:
    """记录调参历史，用于 Prompt 上下文增强"""

    def __init__(self, max_history: int = 5):
        self.history: deque = deque(maxlen=max_history)
        self.last_rejected: Dict[str, Any] | None = None

    def add_record(
        self,
        round_num: int,
        pid      : Dict[str, float],
        metrics  : Dict[str, Any],
        analysis : str,
        thought  : str = "",
        *,
        secondary_pid: Dict[str, float] | None = None,
        next_action: str = "ADJUST",
    ) -> None:
        record = {
            "round"   : round_num,
            "pid"     : dict(pid),
            "metrics" : dict(metrics),
            "analysis": analysis,
            "thought" : thought,
            "secondary_pid": deepcopy(secondary_pid),
            "next_action": next_action,
        }
        self.history.append(record)
        if next_action == "ROLLBACK":
            self.last_rejected = deepcopy(record)

    def to_prompt_text(self, best_result: Dict[str, Any] | None = None) -> str:
        if not self.history and best_result is None:
            return "无历史记录 (这是第一轮)"

        lines = [
            "## Measured tuning history",
            "Each row pairs measured gains with their response; next_action follows that measurement.",
            "role|round|P,I,D|P2,I2,D2|setpoint|avg_error|max_error|tail_abs_error|overshoot%|zero_crossings|status|next_action",
        ]
        included_rounds = {rec["round"] for rec in self.history}
        if best_result is not None and best_result["round"] not in included_rounds:
            lines.append("best|" + self._prompt_row(best_result))
        if self.last_rejected and self.last_rejected["round"] not in included_rounds:
            lines.append("last_rejected|" + self._prompt_row(self.last_rejected))
        for rec in self.history:
            label = "best|" if best_result is not None and best_result["round"] == rec["round"] else "recent|"
            lines.append(label + self._prompt_row(rec))
        return "\n".join(lines)

    @staticmethod
    def _prompt_row(record: Dict[str, Any]) -> str:
        def number(value: Any) -> str:
            return "?" if value is None else f"{float(value):.8g}"

        def pid_text(pid: Dict[str, Any] | None) -> str:
            return ",".join(number(pid.get(k)) for k in ("p", "i", "d")) if pid else "-"

        metrics = record["metrics"]
        return "|".join([
            str(record["round"]), pid_text(record["pid"]), pid_text(record.get("secondary_pid")),
            *(number(metrics.get(k)) for k in (
                "setpoint", "avg_error", "max_error", "steady_state_error", "overshoot", "zero_crossings"
            )),
            str(metrics.get("status", "UNKNOWN")), str(record.get("next_action", "-"))[:64].replace("|", "/").replace("\n", " "),
        ])
