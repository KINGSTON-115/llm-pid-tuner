#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sim/model.py - 加热系统仿真模型

包含仿真物理常量和 HeatingSimulator 类。
"""

import random

# ============================================================================
# 仿真物理常量（固定，不走配置文件）
# ============================================================================

SETPOINT         = 200.0  # 目标温度 (°C)
INITIAL_TEMP     = 20.0   # 初始温度 (°C)
CONTROL_INTERVAL = 0.2    # 仿真步长 (s)，对应 200ms 控制周期


# ============================================================================
# 仿真模型
# ============================================================================


class HeatingSimulator:
    """加热系统仿真器（二阶热传递物理模型）"""

    def __init__(self, kp: float = 1.0, ki: float = 0.1, kd: float = 0.05):
        self.temp       = INITIAL_TEMP
        self.pwm        = 0.0
        self.setpoint   = SETPOINT
        self.integral   = 0.0
        self.prev_error = 0.0
        self.timestamp  = 0
        self.kp         = kp
        self.ki         = ki
        self.kd         = kd

        # 二阶系统参数
        self.heater_temp   = INITIAL_TEMP  # 加热器温度
        self.ambient_temp  = INITIAL_TEMP  # 环境温度
        self.heater_coeff  = 300.0         # 加热器加热系数
        self.heat_transfer = 0.5           # 加热器到物体的传热系数
        self.cooling_coeff = 0.05          # 向环境散热系数（略微降低以模拟保温）
        self.noise_level   = 0.1           # 传感器噪声

    def set_pid(self, kp: float, ki: float, kd: float) -> None:
        """更新 PID 参数"""
        self.kp = kp
        self.ki = ki
        self.kd = kd

    def compute_pid(self) -> None:
        """计算 PID 输出"""
        error          = self.setpoint - self.temp
        self.integral += error * CONTROL_INTERVAL
        self.integral  = max(-500, min(500, self.integral))  # 抗积分饱和
        derivative     = (error - self.prev_error) / CONTROL_INTERVAL

        pid_output = self.kp * error + self.ki * self.integral + self.kd * derivative

        self.pwm        = max(0, min(255, pid_output))  # 仿真 PWM 限制 0-255
        self.prev_error = error

    def update(self) -> None:
        """更新温度状态（二阶热传递模型）"""
        # 1. 加热器升温
        target_heater_temp  = self.ambient_temp + (self.pwm / 255.0) * self.heater_coeff
        self.heater_temp   += (
            (target_heater_temp - self.heater_temp) * 0.1 * CONTROL_INTERVAL
        )

        # 2. 热传递
        heat_in  = (self.heater_temp - self.temp) * self.heat_transfer
        heat_out = (self.temp - self.ambient_temp) * self.cooling_coeff

        self.temp += (heat_in - heat_out) * CONTROL_INTERVAL

        # 3. 传感器噪声
        self.temp      += random.gauss(0, self.noise_level)
        self.timestamp += int(CONTROL_INTERVAL * 1000)

    def get_data(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "setpoint" : self.setpoint,
            "input"    : self.temp,
            "pwm"      : self.pwm,
            "error"    : self.setpoint - self.temp,
            "p"        : self.kp,
            "i"        : self.ki,
            "d"        : self.kd,
        }
