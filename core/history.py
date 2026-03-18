#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/history.py - 调参历史记录管理器
"""

from collections import deque
from typing import Any, Dict


class TuningHistory:
    """记录调参历史，用于 Prompt 上下文增强"""

    def __init__(self, max_history: int = 5):
        self.history: deque = deque(maxlen=max_history)

    def add_record(
        self,
        round_num: int,
        pid      : Dict[str, float],
        metrics  : Dict[str, Any],
        analysis : str,
        thought  : str = "",
    ) -> None:
        record = {
            "round"   : round_num,
            "pid"     : pid,
            "metrics" : metrics,
            "analysis": analysis,
            "thought" : thought,
        }
        self.history.append(record)

    def to_prompt_text(self) -> str:
        if not self.history:
            return "无历史记录 (这是第一轮)"

        text = "## 调参历史 (最近几轮):\n\n"
        for rec in self.history:
            m     = rec["metrics"]
            pid   = rec["pid"]
            text += f"### Round {rec['round']}\n"
            text += f"- **采用参数**: P={pid['p']:.4f}, I={pid['i']:.4f}, D={pid['d']:.4f}\n"
            text += (
                f"- **表现指标**: AvgErr={m.get('avg_error', 0):.2f}, MaxErr={m.get('max_error', 0):.2f}, "
                f"Overshoot={m.get('overshoot', 0):.1f}%, Status={m.get('status', 'UNKNOWN')}\n"
            )
            if rec.get("thought"):
                text += f"- **AI思考过程**: {rec['thought']}\n"
            if rec.get("analysis"):
                text += f"- **AI分析总结**: {rec['analysis']}\n"
            text += "\n"
        return text
