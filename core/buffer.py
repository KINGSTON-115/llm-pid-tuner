#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/buffer.py - 增强版数据缓冲器与高级指标计算
"""

from collections import deque
from typing import Any, Dict


class AdvancedDataBuffer:
    """增强版数据缓冲器"""

    def __init__(self, max_size: int = 100):
        self.max_size    = max_size
        self.buffer      = deque(maxlen=max_size)
        self.current_pid = {"p": 1.0, "i": 0.1, "d": 0.05}
        self.secondary_pid: Dict[str, float] | None = None
        self.setpoint    = 100.0

    def add(self, data: Dict[str, float]) -> None:
        self.buffer.append(data)
        if "p" in data:
            self.current_pid = {
                "p": data.get("p", 1.0),
                "i": data.get("i", 0.1),
                "d": data.get("d", 0.05),
            }
        if "p2" in data:
            self.secondary_pid = {
                "p": data.get("p2", 1.0),
                "i": data.get("i2", 0.1),
                "d": data.get("d2", 0.05),
            }
        if "setpoint" in data:
            self.setpoint = data["setpoint"]

    def is_full(self) -> bool:
        return len(self.buffer) >= self.max_size

    def reset(self) -> None:
        self.buffer.clear()

    def calculate_advanced_metrics(self) -> Dict[str, Any]:
        """计算高级控制指标"""
        if not self.buffer:
            return {}

        data       = list(self.buffer)
        inputs     = [d.get("input", 0) for d in data]
        errors     = [d.get("setpoint", 0) - d.get("input", 0) for d in data]
        abs_errors = [abs(e) for e in errors]

        # 基础指标
        avg_error     = sum(abs_errors) / len(abs_errors) if abs_errors else 0
        max_error     = max(abs_errors) if abs_errors else 0

        # 高级指标：超调量 (Overshoot)
        max_input = max(inputs)
        overshoot = 0.0
        if max_input > self.setpoint and self.setpoint != 0:
            overshoot = ((max_input - self.setpoint) / self.setpoint) * 100.0

        # 高级指标：稳态误差 - 用最后 20% 数据的平均误差估计
        steady_state_len   = max(1, int(len(data) * 0.2))
        steady_state_error = sum(abs_errors[-steady_state_len:]) / steady_state_len

        # 高级指标：震荡检测 - 计算过零点次数
        zero_crossings = 0
        for i in range(1, len(errors)):
            if (errors[i - 1] > 0 and errors[i] < 0) or (
                errors[i - 1] < 0 and errors[i] > 0
            ):
                zero_crossings += 1

        # 状态判断
        status = "STABLE"
        if zero_crossings > len(data) * 0.3:
            status = "OSCILLATING"
        elif overshoot > 5.0:
            status = "OVERSHOOTING"
        elif avg_error > 10.0 and steady_state_error > 5.0:
            status = "SLOW_RESPONSE"

        return {
            "avg_error"         : avg_error,
            "max_error"         : max_error,
            "overshoot"         : overshoot,
            "steady_state_error": steady_state_error,
            "zero_crossings"    : zero_crossings,
            "status"            : status,
            "setpoint"          : self.setpoint,
        }

    def to_prompt_data(self) -> str:
        metrics = self.calculate_advanced_metrics()

        # 下采样：如果数据太多，每隔几个点取一个
        all_data     = list(self.buffer)
        step         = max(1, len(all_data) // 30)
        sampled_data = all_data[::step]

        lines = []
        lines.append("## Current Status")
        lines.append(f"- 设定值 (Setpoint): {self.setpoint}")
        lines.append(
            f"- 当前 PID: P={self.current_pid['p']}, I={self.current_pid['i']}, D={self.current_pid['d']}"
        )
        if self.secondary_pid is not None:
            lines.append(
                f"- 当前 PID 2 (controller_2): "
                f"P={self.secondary_pid['p']}, "
                f"I={self.secondary_pid['i']}, "
                f"D={self.secondary_pid['d']}"
            )
        lines.append(f"- 平均误差: {metrics.get('avg_error', 0):.2f}")
        lines.append(f"- 最大误差: {metrics.get('max_error', 0):.2f}")
        lines.append(f"- 超调量: {metrics.get('overshoot', 0):.1f}%")
        lines.append(f"- 稳态误差估算: {metrics.get('steady_state_error', 0):.2f}")
        lines.append(
            f"- 震荡检测: 过零点 {metrics.get('zero_crossings', 0)} 次 (状态: {metrics.get('status', 'UNKNOWN')})"
        )
        lines.append("")
        lines.append(f"## 时间序列数据摘要 (采样 {len(sampled_data)} 点):")
        lines.append("SimTime(ms), Input, PWM, Error")

        for d in sampled_data:
            lines.append(
                f"{d.get('timestamp', 0):.0f}, {d.get('input', 0):.2f}, {d.get('pwm', 0):.1f}, {d.get('error', 0):.2f}"
            )

        return "\n".join(lines)
