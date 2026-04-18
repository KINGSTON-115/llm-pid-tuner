#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import random


SETPOINT = 200.0
INITIAL_TEMP = 20.0
CONTROL_INTERVAL = 0.2


class HeatingSimulator:
    """Simple thermal plant used for fast simulator-side PID tuning tests."""

    def __init__(
        self,
        kp: float = 1.0,
        ki: float = 0.1,
        kd: float = 0.05,
        setpoint: float = SETPOINT,
        random_seed: int | None = 0,
    ):
        self.temp = INITIAL_TEMP
        self.pwm = 0.0
        self.base_setpoint = float(setpoint)
        self.setpoint = float(setpoint)
        self.integral = 0.0
        self.prev_error = 0.0
        self.timestamp = 0
        self.kp = kp
        self.ki = ki
        self.kd = kd

        self.heater_temp = INITIAL_TEMP
        self.ambient_temp = INITIAL_TEMP
        self.heater_coeff = 300.0
        self.heat_transfer = 0.5
        self.cooling_coeff = 0.05
        self.noise_level = 0.1
        self.rng = random.Random(random_seed)
        self.step_count = 0

    def set_pid(self, kp: float, ki: float, kd: float) -> None:
        self.kp = kp
        self.ki = ki
        self.kd = kd

    def compute_pid(self) -> None:
        # Dynamic setpoint: step change every 50 steps (10 seconds)
        if self.step_count > 0 and self.step_count % 50 == 0:
            if self.setpoint == self.base_setpoint:
                self.setpoint = self.base_setpoint + 50.0
            else:
                self.setpoint = self.base_setpoint

        error = self.setpoint - self.temp
        self.integral += error * CONTROL_INTERVAL
        self.integral = max(-500.0, min(500.0, self.integral))
        derivative = (error - self.prev_error) / CONTROL_INTERVAL

        pid_output = self.kp * error + self.ki * self.integral + self.kd * derivative
        self.pwm = max(0.0, min(255.0, pid_output))
        self.prev_error = error

    def update(self) -> None:
        target_heater_temp = self.ambient_temp + (self.pwm / 255.0) * self.heater_coeff
        self.heater_temp += (target_heater_temp - self.heater_temp) * 0.1 * CONTROL_INTERVAL

        heat_in = (self.heater_temp - self.temp) * self.heat_transfer
        heat_out = (self.temp - self.ambient_temp) * self.cooling_coeff

        self.temp += (heat_in - heat_out) * CONTROL_INTERVAL
        self.temp += self.rng.gauss(0.0, self.noise_level)
        self.temp = max(0.0, self.temp)
        self.timestamp += int(CONTROL_INTERVAL * 1000)
        self.step_count += 1

    def get_data(self) -> dict[str, float]:
        return {
            "timestamp": float(self.timestamp),
            "setpoint": float(self.setpoint),
            "input": float(self.temp),
            "pwm": float(self.pwm),
            "error": float(self.setpoint - self.temp),
            "p": float(self.kp),
            "i": float(self.ki),
            "d": float(self.kd),
        }
