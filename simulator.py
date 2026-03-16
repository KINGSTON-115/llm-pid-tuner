#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
simulator.py - 增强版 PID 调参模拟器 (PRO)
===============================================================================

功能：
1. 使用 core/llm 子包中的增强逻辑 (History-Aware, CoT, Advanced Metrics)
2. Python 仿真模式：运行 sim/model.py 中的 HeatingSimulator 物理模型
3. MATLAB 仿真模式：通过 sim/matlab_bridge.py 驱动 Simulink 模型
   （当 config.json 中 MATLAB_MODEL_PATH 非空时自动启用）

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
# 通用调参主循环
# ============================================================================


def _collect_data(sim, buffer: AdvancedDataBuffer) -> int:
    """
    向 buffer 填充数据直到满，返回采集步数。

    支持两种 adapter：
    - HeatingSimulator：每步调用 compute_pid() + update() + get_data() 返回单条
    - MatlabBridge：每步调用 run_step() + get_data() 返回多条
    """
    steps = 0
    while not buffer.is_full():
        if hasattr(sim, "compute_pid"):
            # Python 仿真模式
            sim.compute_pid()
            sim.update()
            buffer.add(sim.get_data())
            steps += 1
        else:
            # MATLAB 模式
            sim.run_step()
            for data in sim.get_data():
                buffer.add(data)
                steps += 1
                if buffer.is_full():
                    break
    return steps


def _run_tuning_loop(sim, setpoint: float, mode_label: str) -> None:
    """通用调参主循环，对 HeatingSimulator 和 MatlabBridge 均适用。"""
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

    buffer.current_pid = {"p": sim.kp, "i": sim.ki, "d": sim.kd}
    buffer.setpoint    = setpoint

    try:
        while round_num < CONFIG["MAX_TUNING_ROUNDS"]:
            # 1. 采集数据
            print(f"\n[第 {round_num + 1} 轮] 数据采集中...", end="", flush=True)
            steps = _collect_data(sim, buffer)
            print(f" 完成 ({steps} 步)")

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

                old_p, old_i, old_d = sim.kp, sim.ki, sim.kd
                safe_pid, guardrail_notes = apply_pid_guardrails(
                    {"p": sim.kp, "i": sim.ki, "d": sim.kd},
                    result,
                )
                sim.set_pid(safe_pid["p"], safe_pid["i"], safe_pid["d"])

                print(
                    f"  [动作] {action}: P {old_p:.4f}->{sim.kp:.4f}, "
                    f"I {old_i:.4f}->{sim.ki:.4f}, D {old_d:.4f}->{sim.kd:.4f}"
                )
                if guardrail_notes:
                    print(f"  [护栏] {'; '.join(guardrail_notes)}")
                if result.get("fallback_used"):
                    print("  [兜底] 本轮使用规则策略替代 LLM 建议。")

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

            buffer.reset()

    except KeyboardInterrupt:
        print("\n用户中断")

    end_time = time.time()
    print(f"\n测试结束，耗时 {end_time - start_time:.1f} 秒")
    print(f"最终参数: P={sim.kp}, I={sim.ki}, D={sim.kd}")


# ============================================================================
# 模拟主程序
# ============================================================================


def run_simulation():
    ensure_runtime_config(verbose=True)

    matlab_model_path = CONFIG.get("MATLAB_MODEL_PATH", "").strip()

    if matlab_model_path:
        # ---- MATLAB/Simulink 仿真模式 ----
        try:
            from sim.matlab_bridge import MatlabBridge
        except ImportError as e:
            print(f"[ERROR] {e}")
            return

        pid_block_path = CONFIG.get("MATLAB_PID_BLOCK_PATH", "").strip()
        output_signal  = CONFIG.get("MATLAB_OUTPUT_SIGNAL", "").strip()
        sim_step_time  = float(CONFIG.get("MATLAB_SIM_STEP_TIME", 10.0))
        setpoint       = float(CONFIG.get("MATLAB_SETPOINT", 200.0))

        if not pid_block_path:
            print("[ERROR] 未配置 MATLAB_PID_BLOCK_PATH，请在 config.json 中填写 PID 模块路径。")
            return
        if not output_signal:
            print("[ERROR] 未配置 MATLAB_OUTPUT_SIGNAL，请在 config.json 中填写输出信号变量名。")
            return

        print("=" * 60)
        print("  LLM PID Tuner PRO - MATLAB/Simulink 仿真模式")
        print("=" * 60)
        print(f"目标: {setpoint}, 模型: {CONFIG['LLM_MODEL_NAME']}")
        print(f"Simulink 模型: {matlab_model_path}")

        sim = MatlabBridge(
            model_path=matlab_model_path,
            setpoint=setpoint,
            pid_block_path=pid_block_path,
            output_signal=output_signal,
            sim_step_time=sim_step_time,
        )
        try:
            sim.connect()
        except Exception as e:
            print(f"[ERROR] MATLAB 连接失败: {e}")
            return

        try:
            _run_tuning_loop(sim, setpoint, "MATLAB")
        finally:
            sim.disconnect()

    else:
        # ---- Python 热系统仿真模式 ----
        print("=" * 60)
        print("  LLM PID Tuner PRO - 仿真测试")
        print("=" * 60)
        print(f"目标: {SETPOINT}, 模型: {CONFIG['LLM_MODEL_NAME']}")

        sim = HeatingSimulator()
        _run_tuning_loop(sim, SETPOINT, "Python")


if __name__ == "__main__":
    run_simulation()
