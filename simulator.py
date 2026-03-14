#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
simulator.py - 增强版 PID 调参模拟器 (PRO)
===============================================================================

功能：
1. 使用 core/llm 子包中的增强逻辑 (History-Aware, CoT, Advanced Metrics)
2. 运行 sim/model.py 中的 HeatingSimulator 物理模型
3. 生成对比报告

===============================================================================
"""

import time

from core.config import CONFIG, initialize_runtime_config
from core.buffer import AdvancedDataBuffer
from core.history import TuningHistory
from llm.client import LLMTuner
from sim.model import HeatingSimulator, SETPOINT
from pid_safety import (
    apply_pid_guardrails,
    build_fallback_suggestion,
    is_good_enough,
    maybe_update_best_result,
    pid_equals,
    should_rollback_to_best,
)


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
    initialize_runtime_config(create_if_missing=create_if_missing, verbose=verbose)


# 导入时静默初始化：仅读取已有配置，不创建 config.json（避免无写权限时失败）
ensure_runtime_config(verbose=False, create_if_missing=False)


# ============================================================================
# 模拟主程序
# ============================================================================


def run_simulation():
    ensure_runtime_config(verbose=True)  # 直接运行时显示配置加载信息
    print("=" * 60)
    print("  LLM PID Tuner PRO - 仿真测试")
    print("=" * 60)
    print(f"目标: {SETPOINT}, 模型: {CONFIG['LLM_MODEL_NAME']}")

    # 初始化组件
    sim   = HeatingSimulator()
    tuner = LLMTuner(
        CONFIG["LLM_API_KEY"],
        CONFIG["LLM_API_BASE_URL"],
        CONFIG["LLM_MODEL_NAME"],
        CONFIG["LLM_PROVIDER"],
    )
    buffer  = AdvancedDataBuffer(max_size=CONFIG["BUFFER_SIZE"])
    history = TuningHistory(max_history=5)

    good_enough_rules = {
        "avg_error_threshold"         : CONFIG["GOOD_ENOUGH_AVG_ERROR"],
        "steady_state_error_threshold": CONFIG["GOOD_ENOUGH_STEADY_STATE_ERROR"],
        "overshoot_threshold"         : CONFIG["GOOD_ENOUGH_OVERSHOOT"],
    }

    round_num     = 0
    stable_rounds = 0
    best_result   = None
    start_time    = time.time()

    # 设置初始 PID 到 buffer
    buffer.current_pid = {"p": sim.kp, "i": sim.ki, "d": sim.kd}
    buffer.setpoint    = SETPOINT

    try:
        while round_num < CONFIG["MAX_TUNING_ROUNDS"]:
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

            current_pid   = {"p": sim.kp, "i": sim.ki, "d": sim.kd}
            previous_best = best_result
            best_result   = maybe_update_best_result(
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
                if is_good_enough(best_result["metrics"], good_enough_rules):
                    print("\n[SUCCESS] 已回滚到历史最佳且满足可用标准，提前结束调参。")
                    break
                round_num += 1
                buffer.reset()
                continue

            stable_rounds = (
                stable_rounds + 1 if is_good_enough(metrics, good_enough_rules) else 0
            )

            # 检查是否达标
            if (
                metrics["avg_error"] < CONFIG["MIN_ERROR_THRESHOLD"]
                and metrics["status"] == "STABLE"
            ):
                print("\n[SUCCESS] 调参成功！系统已稳定。")
                break
            if stable_rounds >= CONFIG["REQUIRED_STABLE_ROUNDS"]:
                print(
                    f"\n[SUCCESS] 系统已连续 {stable_rounds} 轮达到可用稳定状态，提前结束调参。"
                )
                break

            round_num += 1

            # 3. 准备 Prompt
            prompt_data  = buffer.to_prompt_data()
            history_text = history.to_prompt_text()

            # 4. 调用 LLM
            print("  [LLM] 正在思考...")
            result = tuner.analyze(prompt_data, history_text)

            if not result:
                print("  [WARN] LLM 本轮不可用，启用保守兜底策略。")
                result = build_fallback_suggestion(buffer.current_pid, metrics)

            if result:
                analysis = result.get("analysis_summary", "无分析")
                thought  = result.get("thought_process", "无思考过程")
                action   = result.get("tuning_action", "UNKNOWN")

                print(f"  [思考] {thought[:100]}...")
                print(f"  [分析] {analysis}")

                # 更新参数
                old_p, old_i, old_d = sim.kp, sim.ki, sim.kd
                safe_pid, guardrail_notes = apply_pid_guardrails(
                    {"p": sim.kp, "i": sim.ki, "d": sim.kd},
                    result
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

    except KeyboardInterrupt:
        print("\n用户中断")

    end_time = time.time()
    print(f"\n测试结束，耗时 {end_time - start_time:.1f} 秒")
    print(f"最终参数: P={sim.kp}, I={sim.ki}, D={sim.kd}")


if __name__ == "__main__":
    run_simulation()
