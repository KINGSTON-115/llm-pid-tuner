#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
matlab_tuner.py - MATLAB/Simulink PID 调参入口

通过 MATLAB Engine API for Python 与用户的 Simulink 模型交互，
复用与 simulator.py / tuner.py 完全相同的 LLM 调参核心逻辑。

用法：
    python matlab_tuner.py

前置条件：
    1. 安装 MATLAB Engine API for Python（见 sim/matlab_bridge.py 说明）
    2. 在 config.json 中填写以下字段：
       - MATLAB_MODEL_PATH      : Simulink .slx 文件完整路径
       - MATLAB_PID_BLOCK_PATH  : PID 模块在模型中的路径
       - MATLAB_OUTPUT_SIGNAL   : To Workspace 变量名
       - MATLAB_SIM_STEP_TIME   : 每轮仿真时长（秒）
       - MATLAB_SETPOINT        : 调参目标值
"""

import time

from core.config import CONFIG, initialize_runtime_config
from core.buffer import AdvancedDataBuffer
from core.history import TuningHistory
from llm.client import LLMTuner
from pid_safety import (
    apply_pid_guardrails,
    build_fallback_suggestion,
    is_good_enough,
    maybe_update_best_result,
    pid_equals,
    should_rollback_to_best,
)


def run_matlab_tuning():
    initialize_runtime_config(verbose=True)

    # 检查必填配置
    model_path = CONFIG.get("MATLAB_MODEL_PATH", "").strip()
    if not model_path:
        print(
            "[ERROR] 未配置 MATLAB_MODEL_PATH。\n"
            "请在 config.json 中填写 Simulink 模型路径后重试。"
        )
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

    # 导入桥接层（延迟导入，避免无 MATLAB 时影响其他模式）
    try:
        from sim.matlab_bridge import MatlabBridge
    except ImportError as e:
        print(f"[ERROR] {e}")
        return

    print("=" * 60)
    print("  LLM PID Tuner PRO - MATLAB/Simulink 仿真模式")
    print("=" * 60)
    print(f"目标: {setpoint}, 模型: {CONFIG['LLM_MODEL_NAME']}")
    print(f"Simulink 模型: {model_path}")

    bridge = MatlabBridge(
        model_path=model_path,
        setpoint=setpoint,
        pid_block_path=pid_block_path,
        output_signal=output_signal,
        sim_step_time=sim_step_time,
    )

    try:
        bridge.connect()
    except Exception as e:
        print(f"[ERROR] MATLAB 连接失败: {e}")
        return

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

    buffer.current_pid = {"p": bridge.kp, "i": bridge.ki, "d": bridge.kd}
    buffer.setpoint    = setpoint

    try:
        while round_num < CONFIG["MAX_TUNING_ROUNDS"]:
            print(f"\n[第 {round_num + 1} 轮] 运行 Simulink 仿真...", end="", flush=True)

            # 1. 运行一步仿真，采集数据填满 buffer
            while not buffer.is_full():
                bridge.run_step()
                for data in bridge.get_data():
                    buffer.add(data)
                    if buffer.is_full():
                        break

            print(" 完成")

            # 2. 计算指标
            metrics = buffer.calculate_advanced_metrics()
            print(
                f"  当前状态: AvgErr={metrics['avg_error']:.2f}, MaxErr={metrics['max_error']:.2f}, "
                f"Overshoot={metrics['overshoot']:.1f}%, Status={metrics['status']}"
            )

            current_pid   = {"p": bridge.kp, "i": bridge.ki, "d": bridge.kd}
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
                bridge.set_pid(
                    best_result["pid"]["p"],
                    best_result["pid"]["i"],
                    best_result["pid"]["d"],
                )
                buffer.current_pid = dict(best_result["pid"])
                print(
                    f"  [回滚] 当前表现劣于第 {best_result['round']} 轮最佳结果，"
                    f"恢复到 P={bridge.kp:.4f}, I={bridge.ki:.4f}, D={bridge.kd:.4f}"
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

                old_p, old_i, old_d = bridge.kp, bridge.ki, bridge.kd
                safe_pid, guardrail_notes = apply_pid_guardrails(
                    {"p": bridge.kp, "i": bridge.ki, "d": bridge.kd},
                    result,
                )
                bridge.set_pid(safe_pid["p"], safe_pid["i"], safe_pid["d"])

                print(
                    f"  [动作] {action}: P {old_p:.4f}->{bridge.kp:.4f}, "
                    f"I {old_i:.4f}->{bridge.ki:.4f}, D {old_d:.4f}->{bridge.kd:.4f}"
                )
                if guardrail_notes:
                    print(f"  [护栏] {'; '.join(guardrail_notes)}")
                if result.get("fallback_used"):
                    print("  [兜底] 本轮使用规则策略替代 LLM 建议。")

                history.add_record(
                    round_num,
                    {"p": bridge.kp, "i": bridge.ki, "d": bridge.kd},
                    metrics,
                    analysis,
                )
                buffer.current_pid = {"p": bridge.kp, "i": bridge.ki, "d": bridge.kd}

                if result.get("status") == "DONE":
                    print("\n[LLM] 认为调参已完成。")
                    break

            buffer.reset()

    except KeyboardInterrupt:
        print("\n用户中断")
    finally:
        bridge.disconnect()

    end_time = time.time()
    print(f"\n调参结束，耗时 {end_time - start_time:.1f} 秒")
    print(f"最终参数: P={bridge.kp}, I={bridge.ki}, D={bridge.kd}")


if __name__ == "__main__":
    run_matlab_tuning()
