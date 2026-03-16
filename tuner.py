#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
tuner.py - LLM PID 自动调参系统 (History-Aware + Chain-of-Thought)
===============================================================================

作者: KINGSTON-115, ApexGP

依赖：pyserial, openai (或 requests), numpy (可选，用于高级计算)
"""

import time
import sys

from core.config import CONFIG, initialize_runtime_config
from core.buffer import AdvancedDataBuffer
from core.history import TuningHistory
from hw.bridge import SerialBridge, select_serial_port, safe_pause
from llm.client import LLMTuner
from pid_safety import (
    apply_pid_guardrails,
    build_fallback_suggestion,
    is_good_enough,
    maybe_update_best_result,
    pid_equals,
    should_rollback_to_best,
)


# ============================================================================
# 主程序
# ============================================================================


def main():
    initialize_runtime_config(create_if_missing=True, verbose=True)

    print("=" * 60)
    print("  LLM PID Tuner PRO - 增强版自动调参系统")
    print("=" * 60)

    # 串口选择逻辑
    serial_port = CONFIG["SERIAL_PORT"]
    if len(sys.argv) > 1:
        if not sys.argv[1].startswith("-"):
            serial_port = sys.argv[1]
    else:
        if serial_port and serial_port.upper() != "AUTO":
            print(f"[INFO] 使用配置端口: {serial_port}")
            use_env = input("是否使用该端口? (Y/n): ").strip().lower()
            if use_env == "n":
                serial_port = select_serial_port()
        else:
            serial_port = select_serial_port()

    if not serial_port:
        print("[ERROR] 未指定串口，程序退出。")
        safe_pause()
        return

    print(f"[INFO] 即将连接到: {serial_port}")

    # 串口初始化
    bridge = SerialBridge(serial_port, CONFIG["BAUD_RATE"])
    if not bridge.connect():
        print(f"[ERROR] 无法打开串口 {serial_port}")
        safe_pause()
        return

    # LLM 初始化
    tuner = LLMTuner(
        CONFIG["LLM_API_KEY"],
        CONFIG["LLM_API_BASE_URL"],
        CONFIG["LLM_MODEL_NAME"],
        CONFIG["LLM_PROVIDER"],
    )

    # 数据与历史
    buffer            = AdvancedDataBuffer(max_size=CONFIG["BUFFER_SIZE"])
    history           = TuningHistory(max_history=5)
    good_enough_rules = {
        "avg_error_threshold"         : CONFIG["GOOD_ENOUGH_AVG_ERROR"],
        "steady_state_error_threshold": CONFIG["GOOD_ENOUGH_STEADY_STATE_ERROR"],
        "overshoot_threshold"         : CONFIG["GOOD_ENOUGH_OVERSHOOT"],
    }

    round_num     = 0
    stable_rounds = 0
    best_result   = None

    try:
        bridge.send_command("STATUS")  # 唤醒/检查状态
        time.sleep(1)

        print("[INFO] 开始采集数据...")

        while round_num < CONFIG["MAX_TUNING_ROUNDS"]:
            line = bridge.read_line()
            if line:
                data = bridge.parse_data(line)
                if data:
                    buffer.add(data)
                    print(
                        f"\r[DATA] T={data['input']:.1f} Err={data['error']:.1f} PWM={data['pwm']:.0f}",
                        end="",
                    )

            if buffer.is_full():
                print("\n\n" + "-" * 60)
                round_num += 1
                metrics    = buffer.calculate_advanced_metrics()
                print(
                    f"[第 {round_num} 轮] 分析中... AvgErr={metrics['avg_error']:.2f}, Status={metrics['status']}"
                )
                previous_best = best_result
                best_result   = maybe_update_best_result(
                    best_result, buffer.current_pid, metrics, round_num
                )
                if best_result is not None and best_result is not previous_best:
                    print(
                        f"[Best] 更新最佳参数 -> "
                        f"P={best_result['pid']['p']}, I={best_result['pid']['i']}, D={best_result['pid']['d']}"
                    )

                if (
                    best_result
                    and not pid_equals(buffer.current_pid, best_result["pid"])
                    and should_rollback_to_best(metrics, best_result["metrics"])
                ):
                    rollback_pid = best_result["pid"]
                    print(
                        f"[Rollback] 当前表现劣于第 {best_result['round']} 轮最佳结果，"
                        f"恢复到 P={rollback_pid['p']}, I={rollback_pid['i']}, D={rollback_pid['d']}"
                    )
                    bridge.send_command(
                        f"SET P:{rollback_pid['p']} I:{rollback_pid['i']} D:{rollback_pid['d']}"
                    )
                    buffer.current_pid = dict(rollback_pid)

                    if is_good_enough(best_result["metrics"], good_enough_rules):
                        print(
                            "\n[SUCCESS] 已回滚到历史最佳且满足可用标准，提前结束调参。"
                        )
                        break

                    buffer.reset()
                    time.sleep(1)
                    continue

                stable_rounds = (
                    stable_rounds + 1
                    if is_good_enough(metrics, good_enough_rules)
                    else 0
                )

                if stable_rounds >= CONFIG["REQUIRED_STABLE_ROUNDS"]:
                    print(
                        f"\n[SUCCESS] 系统已连续 {stable_rounds} 轮达到可用稳定状态，提前结束调参。"
                    )
                    break

                # 准备 Prompt
                prompt_data  = buffer.to_prompt_data()
                history_text = history.to_prompt_text()

                # 调用 LLM
                result = tuner.analyze(prompt_data, history_text)

                if not result:
                    print("[WARN] LLM 本轮不可用，启用保守兜底策略。")
                    result = build_fallback_suggestion(buffer.current_pid, metrics)

                if result:
                    safe_pid, guardrail_notes = apply_pid_guardrails(
                        buffer.current_pid, result
                    )
                    new_p = safe_pid["p"]
                    new_i = safe_pid["i"]
                    new_d = safe_pid["d"]

                    # 记录历史
                    history.add_record(
                        round_num, safe_pid, metrics, result.get("analysis_summary", "")
                    )

                    print(f"[Result] {result.get('analysis_summary')}")
                    print(
                        f"[Action] {result.get('tuning_action')} -> P={new_p}, I={new_i}, D={new_d}"
                    )
                    if guardrail_notes:
                        print(f"[Guardrail] {'; '.join(guardrail_notes)}")
                    if result.get("fallback_used"):
                        print("[Fallback] 本轮使用规则策略替代 LLM 建议。")

                    cmd = f"SET P:{new_p} I:{new_i} D:{new_d}"
                    bridge.send_command(cmd)
                    buffer.current_pid = safe_pid

                    if (
                        result.get("status") == "DONE"
                        or metrics["avg_error"] < CONFIG["MIN_ERROR_THRESHOLD"]
                    ):
                        print("\n[SUCCESS] 调参完成！")
                        break

                buffer.reset()
                time.sleep(1)

    except KeyboardInterrupt:
        print("\n[INFO] 用户停止")
    finally:
        bridge.disconnect()


if __name__ == "__main__":
    main()
