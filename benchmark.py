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
from typing import Any, Dict, List

from core.config import CONFIG, initialize_runtime_config
from core.i18n import tr
from core.adapters import PythonSimEnv
from core.tuning_engine import run_tuning_engine
from llm.client import LLMTuner
from llm.usage import usage_summary
from sim.model import HeatingSimulator, SETPOINT
from sim.prompt_context import build_python_sim_prompt_context
from sim.runtime import EVENT_ROUND_METRICS


DEFAULT_CASES = ("baseline", "fallback", "llm")


def create_llm_tuner() -> LLMTuner:
    api_key = CONFIG["LLM_API_KEY"]
    if not api_key or api_key == "your-api-key-here":
        raise RuntimeError(
            tr(
                "未设置 LLM_API_KEY，无法运行 llm benchmark",
                "LLM_API_KEY is not set, cannot run llm benchmark",
            )
        )
    return LLMTuner(
        CONFIG["LLM_API_KEY"],
        CONFIG["LLM_API_BASE_URL"],
        CONFIG["LLM_MODEL_NAME"],
        CONFIG["LLM_PROVIDER"],
        stream_callback=lambda _chunk, _done: None,
        emit_console=False,
        timeout=CONFIG.get("LLM_REQUEST_TIMEOUT", 60.0),
        debug_output=CONFIG.get("LLM_DEBUG_OUTPUT", False),
    )


class _OfflineStrategy:
    def __init__(self, env: PythonSimEnv, hold: bool):
        self.env = env
        self.hold = hold

    def analyze(self, *_args: Any, **_kwargs: Any) -> Dict[str, Any] | None:
        if not self.hold:
            # Exercise exactly the production fallback path and its limits.
            return None
        primary, _ = self.env.get_current_pid()
        return {**primary, "tuning_action": "HOLD", "status": "TUNING",
                "analysis_summary": "Fixed-parameter baseline."}

    def get_usage_summary(self, start_attempt: int = 0) -> Dict[str, Any]:
        return usage_summary([])


class _ProgressSink:
    def __init__(self, case_name: str):
        self.case_name = case_name

    def publish(self, event_type: str, **payload: Any) -> None:
        if event_type == EVENT_ROUND_METRICS:
            print(
                f"  [{self.case_name}] Round {payload['round']}: "
                f"AvgErr={payload['avg_error']:.3f} "
                f"Steady={payload['steady_state_error']:.3f} "
                f"Overshoot={payload['overshoot']:.2f}% "
                f"Status={payload['status']}"
            )


def run_case(
    case_name: str, rounds: int, seed: int, stop_on_done: bool = True
) -> Dict[str, Any]:
    if case_name not in DEFAULT_CASES:
        raise ValueError(f"Unknown benchmark case: {case_name}")
    if rounds < 1:
        raise ValueError("rounds must be positive")
    sim = HeatingSimulator(random_seed=seed)
    # Use an explicit, identical single-step task for all strategies.
    sim.set_setpoint(SETPOINT)
    sim.set_pid(1.0, 0.1, 0.05)
    env = PythonSimEnv(sim, SETPOINT)
    env.prompt_context = build_python_sim_prompt_context()
    strategy = create_llm_tuner() if case_name == "llm" else _OfflineStrategy(
        env, hold=case_name == "baseline"
    )
    result = run_tuning_engine(
        env, strategy, "python_sim", event_sink=_ProgressSink(case_name),
        emit_console=False, disable_early_exit=not stop_on_done, max_rounds=rounds,
    )
    records = [
        {"round": record["round"], "pid": record["pid"], **record["metrics"]}
        for record in result["experiments"]
    ]
    return {
        **result,
        "case": case_name,
        "seed": seed,
        "scenario": "fixed_setpoint_continuous_state",
        "rounds_executed": result["rounds_completed"],
        "llm_calls": result["decision_requests"] if case_name == "llm" else 0,
        "final": {
            **{key: result["final_metrics"].get(key) for key in (
                "avg_error", "steady_state_error", "overshoot", "status"
            )},
            "pid": result["final_pid"],
            "verified": result["final_pid_verified"],
        },
        "last_evaluated": {
            **result["evaluated_metrics"], "pid": result["evaluated_pid"],
        },
        "history": records,
    }


def print_summary(results: List[Dict[str, Any]]):
    # 列宽定义
    W_CASE = 10
    W_RND = 3
    W_AVGERR = 7
    W_STEADY = 7
    W_OVER = 9
    W_STATUS = 13
    W_PID = 8
    W_FDBK = 4
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
    print("  LLM PID Benchmark: last evaluated response and PID")
    print(bar)
    print(header)
    print(sep)
    for result in results:
        final = result["last_evaluated"]
        pid = final["pid"]
        overshoot_str = f"{final['overshoot']:.2f}%"
        elapsed_str = f"{result['elapsed_sec']:.1f}s"
        print(
            f"  {result['case']:<{W_CASE}}  {result['rounds_executed']:>{W_RND}}  "
            f"{final['avg_error']:>{W_AVGERR}.3f}  {final['steady_state_error']:>{W_STEADY}.3f}  "
            f"{overshoot_str:>{W_OVER}}  {final['status']:<{W_STATUS}}  "
            f"{pid['p']:>{W_PID}.4f}  {pid['i']:>{W_PID}.4f}  {pid['d']:>{W_PID}.4f}  "
            f"{result['fallback_count']:>{W_FDBK}}  {elapsed_str:>{W_ELAPSED}}"
        )
        usage = result["llm_usage"]
        tokens = usage["reported_tokens"]
        print(f"    Reported tokens: input={tokens['input_tokens']}, output={tokens['output_tokens']}; "
              f"attempts={usage['transport_attempts']}, complete={usage['usage_complete']}")
    print(bar)


def main():
    # 显式初始化调参运行时配置
    initialize_runtime_config(verbose=False)
    parser = argparse.ArgumentParser(description="LLM PID 调参 benchmark")
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=DEFAULT_CASES,
        default=list(DEFAULT_CASES),
        help="要运行的 benchmark case",
    )
    parser.add_argument(
        "--rounds", type=int, default=8, help="每个 case 最多运行的轮数"
    )
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument(
        "--no-stop-on-done",
        action="store_true",
        help="关闭程序提前停止和回滚，跑满指定轮数；模型 DONE 不作为验收条件",
    )
    parser.add_argument("--json-out", type=str, help="将结果写入 JSON 文件")
    parser.add_argument(
        "--lang", choices=["zh", "en"], help="Override display language (zh or en)."
    )
    args = parser.parse_args()

    if args.lang:
        from core.i18n import set_language

        set_language(args.lang)

    results = [
        run_case(
            case_name,
            rounds=args.rounds,
            seed=args.seed,
            stop_on_done=not args.no_stop_on_done,
        )
        for case_name in args.cases
    ]

    print_summary(results)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=2, ensure_ascii=False)
        print(
            tr(
                f"\n[INFO] 结果已写入 {args.json_out}",
                f"\n[INFO] Results written to {args.json_out}",
            )
        )


if __name__ == "__main__":
    main()
