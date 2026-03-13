#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
simulator.py - 增强版 PID 调参模拟器 (PRO)
===============================================================================

功能：
1. 使用 tuner.py 中的增强逻辑 (History-Aware, CoT, Advanced Metrics)
2. 运行 HeatingSimulator 物理模型
3. 生成对比报告

===============================================================================
"""

import time
import sys
import random

# 导入增强版调参器组件
try:
    import tuner as _tuner_mod
    from tuner import LLMTuner, AdvancedDataBuffer, TuningHistory, CONFIG
    from pid_safety import (
        apply_pid_guardrails,
        build_fallback_suggestion,
        is_good_enough,
        maybe_update_best_result,
        pid_equals,
        should_rollback_to_best,
    )
except ImportError:
    print("[ERROR] 找不到 tuner.py，请确保文件存在")
    sys.exit(1)


def ensure_runtime_config(
    verbose: bool = False, create_if_missing: bool = True
) -> None:
    """
    确保运行时配置已初始化。

    默认情况下:
    - create_if_missing=True: 若 config.json 不存在则创建默认配置;
    - verbose=False: 避免被其他模块导入时产生多余的 stdout 输出。

    该函数可安全地多次调用。
    """
    _tuner_mod.initialize_runtime_config(
        create_if_missing=create_if_missing, verbose=verbose
    )


# 导入时静默初始化，供 benchmark 等调用方使用
ensure_runtime_config(verbose=False)

# ============================================================================
# 配置（从 config.json / 环境变量读取，优先级：环境变量 > config.json > 默认值）
# ============================================================================

API_BASE_URL = CONFIG["LLM_API_BASE_URL"]
API_KEY      = CONFIG["LLM_API_KEY"]
MODEL_NAME   = CONFIG["LLM_MODEL_NAME"]
LLM_PROVIDER = CONFIG["LLM_PROVIDER"]

BUFFER_SIZE       = CONFIG["BUFFER_SIZE"]
MAX_ROUNDS        = CONFIG["MAX_TUNING_ROUNDS"]
MIN_ERROR         = CONFIG["MIN_ERROR_THRESHOLD"]
CONTROL_INTERVAL  = 0.2  # 仿真步长 (200ms)，固定物理参数，不走配置
GOOD_ENOUGH_RULES = {
    "avg_error_threshold"         : CONFIG["GOOD_ENOUGH_AVG_ERROR"],
    "steady_state_error_threshold": CONFIG["GOOD_ENOUGH_STEADY_STATE_ERROR"],
    "overshoot_threshold"         : CONFIG["GOOD_ENOUGH_OVERSHOOT"],
}
REQUIRED_STABLE_ROUNDS = CONFIG["REQUIRED_STABLE_ROUNDS"]

SETPOINT     = 200.0  # 目标温度
INITIAL_TEMP = 20.0   # 初始温度

# ============================================================================
# 仿真模型 (内置)
# ============================================================================


class HeatingSimulator:
    """加热系统仿真器 (更真实的物理模型)"""

    def __init__(self, kp: float = 1.0, ki: float = 0.1, kd: float = 0.05):
        self.temp       = INITIAL_TEMP
        self.pwm        = 0
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
        self.cooling_coeff = 0.05          # 向环境散热系数 (略微降低以模拟保温)
        self.noise_level   = 0.1           # 传感器噪声

    def set_pid(self, kp: float, ki: float, kd: float):
        """更新 PID 参数"""
        self.kp = kp
        self.ki = ki
        self.kd = kd

    def compute_pid(self):
        """计算 PID 输出"""
        error = self.setpoint - self.temp
        self.integral += error * CONTROL_INTERVAL
        self.integral = max(-500, min(500, self.integral))  # 抗饱和
        derivative = (error - self.prev_error) / CONTROL_INTERVAL

        pid_output = self.kp * error + self.ki * self.integral + self.kd * derivative

        self.pwm = max(0, min(255, pid_output))  # 仿真限制在 0-255
        self.prev_error = error

    def update(self):
        """更新温度状态"""
        # 1. 加热器升温
        target_heater_temp = self.ambient_temp + (self.pwm / 255.0) * self.heater_coeff
        self.heater_temp += (
            (target_heater_temp - self.heater_temp) * 0.1 * CONTROL_INTERVAL
        )

        # 2. 热传递
        heat_in = (self.heater_temp - self.temp) * self.heat_transfer
        heat_out = (self.temp - self.ambient_temp) * self.cooling_coeff

        self.temp += (heat_in - heat_out) * CONTROL_INTERVAL

        # 3. 噪声
        self.temp += random.gauss(0, self.noise_level)
        self.timestamp += int(CONTROL_INTERVAL * 1000)

    def get_data(self):
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


# ============================================================================
# 模拟主程序
# ============================================================================


def run_simulation():
    ensure_runtime_config(verbose=True)  # 直接运行时显示配置加载信息
    print("=" * 60)
    print("  LLM PID Tuner PRO - 仿真测试")
    print("=" * 60)
    print(f"目标: {SETPOINT}, 模型: {MODEL_NAME}")

    # 初始化组件
    sim = HeatingSimulator()
    tuner = LLMTuner(API_KEY, API_BASE_URL, MODEL_NAME, LLM_PROVIDER)
    buffer = AdvancedDataBuffer(max_size=BUFFER_SIZE)
    history = TuningHistory(max_history=5)

    round_num = 0
    stable_rounds = 0
    best_result = None
    start_time = time.time()

    # 设置初始 PID 到 buffer
    buffer.current_pid = {"p": sim.kp, "i": sim.ki, "d": sim.kd}
    buffer.setpoint = SETPOINT

    try:
        while round_num < MAX_ROUNDS:
            # 1. 运行仿真并采集数据
            sim_steps = 0
            print(f"\n[第 {round_num + 1} 轮] 数据采集中...", end="")

            # 采集 BUFFER_SIZE 个数据点
            while not buffer.is_full():
                sim.compute_pid()
                sim.update()
                data = sim.get_data()
                buffer.add(data)
                sim_steps += 1

            print(f" 完成 ({sim_steps} 步)")

            # 2. 计算指标
            metrics = buffer.calculate_advanced_metrics()
            print(
                f"  当前状态: AvgErr={metrics['avg_error']:.2f}, MaxErr={metrics['max_error']:.2f}, "
                f"Overshoot={metrics['overshoot']:.1f}%, Status={metrics['status']}"
            )

            current_pid = {"p": sim.kp, "i": sim.ki, "d": sim.kd}
            previous_best = best_result
            best_result = maybe_update_best_result(
                best_result, current_pid, metrics, round_num + 1
            )
            if best_result is not None and best_result is not previous_best:
                print(
                    f"  [最佳] 记录新的最佳参数: "
                    f"P={best_result['pid']['p']:.4f}, I={best_result['pid']['i']:.4f}, D={best_result['pid']['d']:.4f}"
                )

            if (
                best_result
                and not pid_equals(current_pid, best_result["pid"])
                and should_rollback_to_best(metrics, best_result["metrics"])
            ):
                sim.set_pid(
                    best_result["pid"]["p"],
                    best_result["pid"]["i"],
                    best_result["pid"]["d"],
                )
                buffer.current_pid = dict(best_result["pid"])
                print(
                    f"  [回滚] 当前表现劣于第 {best_result['round']} 轮最佳结果，"
                    f"恢复到 P={sim.kp:.4f}, I={sim.ki:.4f}, D={sim.kd:.4f}"
                )
                if is_good_enough(best_result["metrics"], GOOD_ENOUGH_RULES):
                    print("\n[SUCCESS] 已回滚到历史最佳且满足可用标准，提前结束调参。")
                    break
                round_num += 1
                buffer.reset()
                continue

            stable_rounds = (
                stable_rounds + 1 if is_good_enough(metrics, GOOD_ENOUGH_RULES) else 0
            )

            # 检查是否达标
            if metrics["avg_error"] < MIN_ERROR and metrics["status"] == "STABLE":
                print("\n[SUCCESS] 调参成功！系统已稳定。")
                break
            if stable_rounds >= REQUIRED_STABLE_ROUNDS:
                print(
                    f"\n[SUCCESS] 系统已连续 {stable_rounds} 轮达到可用稳定状态，提前结束调参。"
                )
                break

            round_num += 1

            # 3. 准备 Prompt
            prompt_data = buffer.to_prompt_data()
            history_text = history.to_prompt_text()

            # 4. 调用 LLM
            print("  [LLM] 正在思考...")
            result = tuner.analyze(prompt_data, history_text)

            if not result:
                print("  [WARN] LLM 本轮不可用，启用保守兜底策略。")
                result = build_fallback_suggestion(buffer.current_pid, metrics)

            if result:
                analysis = result.get("analysis_summary", "无分析")
                thought = result.get("thought_process", "无思考过程")
                action = result.get("tuning_action", "UNKNOWN")

                print(f"  [思考] {thought[:100]}...")
                print(f"  [分析] {analysis}")

                # 更新参数
                old_p, old_i, old_d = sim.kp, sim.ki, sim.kd
                safe_pid, guardrail_notes = apply_pid_guardrails(
                    {"p": sim.kp, "i": sim.ki, "d": sim.kd},
                    result,
                )
                sim.set_pid(safe_pid["p"], safe_pid["i"], safe_pid["d"])

                print(
                    f"  [动作] {action}: P {old_p:.4f}->{sim.kp:.4f}, I {old_i:.4f}->{sim.ki:.4f}, D {old_d:.4f}->{sim.kd:.4f}"
                )
                if guardrail_notes:
                    print(f"  [护栏] {'; '.join(guardrail_notes)}")
                if result.get("fallback_used"):
                    print("  [兜底] 本轮使用规则策略替代 LLM 建议。")

                # 记录历史
                history.add_record(
                    round_num,
                    {"p": sim.kp, "i": sim.ki, "d": sim.kd},
                    metrics,
                    analysis,
                )
                buffer.current_pid = {"p": sim.kp, "i": sim.ki, "d": sim.kd}

                if result.get("status") == "DONE":
                    print("\n[LLM] 认为调参已完成。")
                    break

            # 清空缓冲，准备下一轮
            buffer.reset()

            # 注意：不重置仿真器状态 (sim.reset())，因为我们要模拟连续调参过程

    except KeyboardInterrupt:
        print("\n用户中断")

    end_time = time.time()
    print(f"\n测试结束，耗时 {end_time - start_time:.1f} 秒")
    print(f"最终参数: P={sim.kp}, I={sim.ki}, D={sim.kd}")


if __name__ == "__main__":
    run_simulation()
