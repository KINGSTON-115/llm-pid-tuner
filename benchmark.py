#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM PID 调参基准脚本。

目标：
1. 用固定随机种子复现实验；
2. 对比 baseline / fallback / real-llm 三种路径；
3. 输出简洁、可比较的指标结果。
"""

from __future__ import annotations

import argparse
import json
import random
import time
from typing import Any, Dict, List

from core.config import CONFIG, initialize_runtime_config
from core.buffer import AdvancedDataBuffer
from core.history import TuningHistory
from llm.client import LLMTuner
from sim.model import HeatingSimulator, SETPOINT
from pid_safety import (
    DEFAULT_CONVERGENCE_RULES,
    apply_pid_guardrails,
    build_fallback_suggestion,
    is_good_enough,
    maybe_update_best_result,
    pid_equals,
    should_rollback_to_best,
)


DEFAULT_CASES = ("baseline", "fallback", "llm")


def create_llm_tuner() -> LLMTuner:
    api_key = CONFIG["LLM_API_KEY"]
    if not api_key or api_key == "your-api-key-here":
        raise RuntimeError("未设置 LLM_API_KEY，无法运行 llm benchmark")
    return LLMTuner(
        CONFIG["LLM_API_KEY"],
        CONFIG["LLM_API_BASE_URL"],
        CONFIG["LLM_MODEL_NAME"],
        CONFIG["LLM_PROVIDER"],
    )


def run_case(
    case_name: str, rounds: int, seed: int, stop_on_done: bool = True
) -> Dict[str, Any]:
    random.seed(seed)

    sim            = HeatingSimulator()
    sim.set_pid(1.0, 0.1, 0.05)  # 重置为初始 PID
    history        = TuningHistory(max_history=5)
    llm            = create_llm_tuner() if case_name == "llm" else None
    fallback_count = 0
    best_result    = None
    records        : List[Dict[str, Any]] = []
    start_time     = time.time()
    print(f"\n[{case_name}] 开始运行，最多 {rounds} 轮...")
    rnd_w          = len(str(rounds))  # 轮次数字宽度，保证对齐

    for round_num in range(1, rounds + 1):
        buffer             = AdvancedDataBuffer(max_size=CONFIG["BUFFER_SIZE"])
        buffer.current_pid = {"p": sim.kp, "i": sim.ki, "d": sim.kd}
        buffer.setpoint    = SETPOINT

        while not buffer.is_full():
            sim.compute_pid()
            sim.update()
            buffer.add(sim.get_data())

        metrics = buffer.calculate_advanced_metrics()
        print(
            f"  [{case_name}] 第 {round_num:{rnd_w}}/{rounds} 轮: "
            f"AvgErr={metrics['avg_error']:>7.3f}  "
            f"Steady={metrics['steady_state_error']:>7.3f}  "
            f"Overshoot={metrics['overshoot']:>6.2f}%  "
            f"Status={metrics['status']:<13}"
        )
        record = {
            "round"             : round_num,
            "avg_error"         : metrics["avg_error"],
            "steady_state_error": metrics["steady_state_error"],
            "overshoot"         : metrics["overshoot"],
            "status"            : metrics["status"],
            "pid"               : {"p": sim.kp, "i": sim.ki, "d": sim.kd},
        }
        records.append(record)

        current_pid = {"p": sim.kp, "i": sim.ki, "d": sim.kd}
        best_result = maybe_update_best_result(
            best_result, current_pid, metrics, round_num
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
            print(f"  [{case_name}] 回滚到第 {best_result['round']} 轮最佳参数")
            if is_good_enough(best_result["metrics"], DEFAULT_CONVERGENCE_RULES):
                break
            continue

        if case_name == "baseline":
            continue

        if case_name == "fallback":
            result = build_fallback_suggestion(buffer.current_pid, metrics)
        else:
            assert llm is not None, "llm case 下 LLMTuner 未初始化"
            print(
                f"  [llm] 第 {round_num:{rnd_w}}/{rounds} 轮: 正在请求 LLM...",
                end="",
                flush=True,
            )
            result = llm.analyze(buffer.to_prompt_data(), history.to_prompt_text())
            if not result:
                result = build_fallback_suggestion(buffer.current_pid, metrics)
                print(" 超时/失败，使用兜底策略")
            else:
                print(f" {result.get('tuning_action', '?')}")

        if result.get("fallback_used"):
            fallback_count += 1

        safe_pid, _ = apply_pid_guardrails(buffer.current_pid, result)
        sim.set_pid(safe_pid["p"], safe_pid["i"], safe_pid["d"])
        history.add_record(
            round_num, safe_pid, metrics, result.get("analysis_summary", "")
        )

        if stop_on_done and result.get("status") == "DONE":
            break

    elapsed = time.time() - start_time
    print(f"[{case_name}] 完成，共 {len(records)} 轮，耗时 {elapsed:.1f}s")
    final_metrics = records[-1]

    return {
        "case"           : case_name,
        "rounds_executed": len(records),
        "fallback_count" : fallback_count,
        "elapsed_sec"    : elapsed,
        "final"          : {
            "avg_error"         : final_metrics["avg_error"],
            "steady_state_error": final_metrics["steady_state_error"],
            "overshoot"         : final_metrics["overshoot"],
            "status"            : final_metrics["status"],
            "pid"               : {"p": sim.kp, "i": sim.ki, "d": sim.kd},
        },
        "history": records,
    }


def print_summary(results: List[Dict[str, Any]]):
    # 列宽定义
    W_CASE    = 10
    W_RND     = 3
    W_AVGERR  = 7
    W_STEADY  = 7
    W_OVER    = 9
    W_STATUS  = 13
    W_PID     = 8
    W_FDBK    = 4
    W_ELAPSED = 7

    header = (
        f"  {'Case':<{W_CASE}}  {'Rnd':>{W_RND}}  {'AvgErr':>{W_AVGERR}}  "
        f"{'Steady':>{W_STEADY}}  {'Overshoot':>{W_OVER}}  {'Status':<{W_STATUS}}  "
        f"{'Kp':>{W_PID}}  {'Ki':>{W_PID}}  {'Kd':>{W_PID}}  "
        f"{'Fdbk':>{W_FDBK}}  {'Elapsed':>{W_ELAPSED}}"
    )
    sep = (
        f"  {'-' * W_CASE}  {'-' * W_RND}  {'-' * W_AVGERR}  "
        f"{'-' * W_STEADY}  {'-' * W_OVER}  {'-' * W_STATUS}  "
        f"{'-' * W_PID}  {'-' * W_PID}  {'-' * W_PID}  "
        f"{'-' * W_FDBK}  {'-' * W_ELAPSED}"
    )
    bar = "=" * len(header)

    print(bar)
    print("  LLM PID Benchmark Summary")
    print(bar)
    print(header)
    print(sep)
    for result in results:
        final         = result["final"]
        pid           = final["pid"]
        overshoot_str = f"{final['overshoot']:.2f}%"
        elapsed_str   = f"{result['elapsed_sec']:.1f}s"
        print(
            f"  {result['case']:<{W_CASE}}  {result['rounds_executed']:>{W_RND}}  "
            f"{final['avg_error']:>{W_AVGERR}.3f}  {final['steady_state_error']:>{W_STEADY}.3f}  "
            f"{overshoot_str:>{W_OVER}}  {final['status']:<{W_STATUS}}  "
            f"{pid['p']:>{W_PID}.4f}  {pid['i']:>{W_PID}.4f}  {pid['d']:>{W_PID}.4f}  "
            f"{result['fallback_count']:>{W_FDBK}}  {elapsed_str:>{W_ELAPSED}}"
        )
    print(bar)


def main():
    # 显式初始化调参运行时配置
    initialize_runtime_config(verbose=False)
    parser = argparse.ArgumentParser(description="LLM PID 调参 benchmark")
    parser.add_argument(
        "--cases",
        nargs   = "+",
        choices = DEFAULT_CASES,
        default = list(DEFAULT_CASES),
        help    = "要运行的 benchmark case",
    )
    parser.add_argument(
        "--rounds", type=int, default=8, help="每个 case 最多运行的轮数"
    )
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument(
        "--no-stop-on-done",
        action = "store_true",
        help   = "即使模型判定 DONE 也继续跑满轮数",
    )
    parser.add_argument("--json-out", type=str, help="将结果写入 JSON 文件")
    args = parser.parse_args()

    results = [
        run_case(
            case_name,
            rounds       = args.rounds,
            seed         = args.seed,
            stop_on_done = not args.no_stop_on_done,
        )
        for case_name in args.cases
    ]

    print_summary(results)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=2, ensure_ascii=False)
        print(f"\n[INFO] 结果已写入 {args.json_out}")


if __name__ == "__main__":
    main()
