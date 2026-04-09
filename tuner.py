#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
tuner.py - LLM PID 自动调参系统 (History-Aware + Chain-of-Thought)
===============================================================================

作者: KINGSTON-115, ApexGP

依赖：pyserial, openai (或 requests), numpy (可选，用于高级计算)
"""

from __future__ import annotations

import argparse
from queue import Queue
import sys
import time
import traceback
from typing import Any, Callable

from core.config import CONFIG, initialize_runtime_config
from core.tuning_session import (
    apply_rollback,
    build_tuning_result,
    create_tuning_session,
    evaluate_completed_round,
    finalize_decision,
    record_rollback_round,
)
from hw.bridge import SerialBridge, safe_pause, select_serial_port
from llm.client import LLMTuner
from pid_safety import apply_pid_guardrails, build_fallback_suggestion, get_pid_limits
from core.tuning_loop import (
    flatten_controller_result,
    publish_decision,
    publish_rollback,
    publish_round_metrics,
)
from sim.runtime import (
    EVENT_SAMPLE,
    QueueEventSink,
    SimulationController,
    emit_console_message as _console,
    emit_lifecycle as _emit_lifecycle,
    emit_log as _emit_log,
    make_llm_tuner_callbacks,
    now_elapsed,
    publish_event,
    wait_while_paused,
)


def _build_hardware_prompt_context(
    serial_port: str,
    secondary_pid: dict[str, float] | None = None,
) -> dict[str, Any]:
    context = {
        "source": "serial_hardware",
        "serial_port": serial_port,
        "controller_output_signal": "PWM",
        "pwm_signal_available": True,
        "tuning_style": "conservative_hardware_safe",
        "per_round_guardrail_hint": "Keep P within about 3x the current value, and keep I/D within about 4x. Prefer smaller moves near stability.",
    }
    if secondary_pid is not None:
        context["controller_count"] = 2
        context["controller_structure"] = "dual_controller"
        context["controller_2_label"] = "controller_2"
        context["controller_2_pid"] = dict(secondary_pid)
    return context


def _build_set_command(prefix: str, pid: dict[str, float]) -> str:
    return (
        f"{prefix} P:{pid['p']} "
        f"I:{pid['i']} D:{pid['d']}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the hardware PID tuner against a serial device."
    )
    parser.add_argument(
        "serial_port",
        nargs="?",
        help="Serial port to use, for example COM5.",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Disable the Textual dashboard and use plain console logs.",
    )
    return parser


def resolve_serial_port(serial_port_arg: str | None) -> str | None:
    if serial_port_arg:
        return serial_port_arg

    serial_port = CONFIG["SERIAL_PORT"]
    if serial_port and serial_port.upper() != "AUTO":
        print(f"[INFO] 使用配置端口: {serial_port}")
        use_env = input("是否使用该端口? (Y/n): ").strip().lower()
        if use_env != "n":
            return serial_port

    return select_serial_port()


def choose_tui_language(default: str = "zh") -> str:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return default

    print("Choose interface language / 选择界面语言")
    print("[1] 中文")
    print("[2] English")
    choice = input("Press Enter for 中文 / 回车默认中文: ").strip().lower()
    if choice in {"2", "en", "english"}:
        return "en"
    return default


def _run_hardware_tuning_loop(
    serial_port: str,
    event_sink: QueueEventSink | None = None,
    controller: SimulationController | None = None,
    emit_console: bool = True,
    initial_pid: dict[str, float] | None = None,
) -> dict[str, Any]:
    bridge = SerialBridge(serial_port, CONFIG["BAUD_RATE"], emit_console=False)
    session = create_tuning_session(initial_pid=initial_pid)
    start_time = time.time()
    current_stream_round = [0]
    llm_log_callback, llm_stream_callback = make_llm_tuner_callbacks(
        event_sink, start_time, current_stream_round
    )

    tuner = LLMTuner(
        CONFIG["LLM_API_KEY"],
        CONFIG["LLM_API_BASE_URL"],
        CONFIG["LLM_MODEL_NAME"],
        CONFIG["LLM_PROVIDER"],
        stream_callback=llm_stream_callback,
        log_callback=llm_log_callback,
        emit_console=emit_console,
        timeout=CONFIG.get("LLM_REQUEST_TIMEOUT", 60.0),
        debug_output=CONFIG.get("LLM_DEBUG_OUTPUT", False),
    )

    _emit_lifecycle(
        event_sink,
        start_time,
        "starting",
        f"Opening {serial_port} at {CONFIG['BAUD_RATE']} baud.",
    )

    if not bridge.connect():
        message = f"无法打开串口 {serial_port}: {bridge.last_error or 'unknown error'}"
        session.completed_reason = "error"
        _console(emit_console, f"[ERROR] {message}")
        _emit_lifecycle(event_sink, start_time, "error", message)
        return {
            "elapsed_sec": now_elapsed(start_time),
            **build_tuning_result(
                session,
                final_pid=dict(session.buffer.current_pid),
                stopped=False,
            ),
        }

    _console(emit_console, f"[INFO] 已连接到串口: {serial_port}")
    _emit_lifecycle(
        event_sink,
        start_time,
        "connected",
        f"Connected to {serial_port}.",
    )

    try:
        bridge.send_command("STATUS")
        _emit_log(event_sink, start_time, "cmd", "STATUS")
        _console(emit_console, "[CMD] Sent: STATUS")
        if initial_pid:
            cmd = f"SET P:{initial_pid['p']} I:{initial_pid['i']} D:{initial_pid['d']}"
            bridge.send_command(cmd)
            _emit_log(event_sink, start_time, "cmd", cmd)
            _console(emit_console, f"[CMD] Initial PID: {cmd}")
        time.sleep(1)

        _console(emit_console, "[INFO] 开始采集数据...")
        _emit_lifecycle(
            event_sink,
            start_time,
            "collecting",
            f"Collecting data from {serial_port}.",
        )

        while session.round_num < CONFIG["MAX_TUNING_ROUNDS"]:
            if controller is not None and controller.should_stop:
                session.completed_reason = "stopped_by_user"
                _console(emit_console, "\n[INFO] 用户停止")
                _emit_lifecycle(event_sink, start_time, "stopped", "Hardware tuning stopped by user.")
                break

            if not wait_while_paused(controller):
                session.completed_reason = "stopped_by_user"
                _console(emit_console, "\n[INFO] 用户停止")
                _emit_lifecycle(event_sink, start_time, "stopped", "Hardware tuning stopped by user.")
                break

            line = bridge.read_line()
            if line:
                data = bridge.parse_data(line)
                if data:
                    session.buffer.add(data)
                    publish_event(
                        event_sink,
                        EVENT_SAMPLE,
                        timestamp=float(data.get("timestamp", 0.0)),
                        setpoint=float(data.get("setpoint", 0.0)),
                        input=float(data.get("input", 0.0)),
                        pwm=float(data.get("pwm", 0.0)),
                        error=float(data.get("error", 0.0)),
                        p=float(data.get("p", session.buffer.current_pid["p"])),
                        i=float(data.get("i", session.buffer.current_pid["i"])),
                        d=float(data.get("d", session.buffer.current_pid["d"])),
                        **(
                            {
                                "p2": float(data.get("p2", 0.0)),
                                "i2": float(data.get("i2", 0.0)),
                                "d2": float(data.get("d2", 0.0)),
                            }
                            if "p2" in data
                            else {}
                        ),
                    )
                    _console(
                        emit_console,
                        f"\r[DATA] T={data['input']:.1f} Err={data['error']:.1f} PWM={data['pwm']:.0f}",
                        end="",
                    )

            if not session.buffer.is_full():
                continue

            if emit_console:
                print("\n\n" + "-" * 60)

            evaluation = evaluate_completed_round(
                session,
                dict(session.buffer.current_pid),
            )
            publish_round_metrics(event_sink, evaluation, evaluation.round_index)
            _console(
                emit_console,
                f"[第 {evaluation.round_index} 轮] 分析中... AvgErr={evaluation.metrics['avg_error']:.2f}, Status={evaluation.metrics['status']}",
            )

            if evaluation.best_result_updated and evaluation.best_result is not None:
                best_message = (
                    f"Round {evaluation.round_index} captured a new best PID: "
                    f"P={evaluation.best_result['pid']['p']}, I={evaluation.best_result['pid']['i']}, D={evaluation.best_result['pid']['d']}"
                )
                _console(
                    emit_console,
                    f"[Best] 更新最佳参数 -> "
                    f"P={evaluation.best_result['pid']['p']}, I={evaluation.best_result['pid']['i']}, D={evaluation.best_result['pid']['d']}",
                )
                _emit_log(event_sink, start_time, "best", best_message)

            if evaluation.rollback_pid:
                rollback_message = record_rollback_round(
                    session,
                    evaluation,
                    evaluation.rollback_pid,
                    target_round=int(evaluation.best_result["round"]) if evaluation.best_result else None,
                )
                _console(emit_console, f"[Rollback] {rollback_message}")
                publish_rollback(
                    event_sink,
                    evaluation.round_index,
                    evaluation,
                    evaluation.rollback_pid,
                    rollback_message,
                )

                cmd = _build_set_command("SET", evaluation.rollback_pid)
                bridge.send_command(cmd)
                _emit_log(event_sink, start_time, "cmd", cmd)
                _console(emit_console, f"[CMD] Sent: {cmd}")
                if evaluation.rollback_secondary_pid is not None:
                    cmd2 = _build_set_command("SET2", evaluation.rollback_secondary_pid)
                    bridge.send_command(cmd2)
                    _emit_log(event_sink, start_time, "cmd", cmd2)
                    _console(emit_console, f"[CMD] Sent: {cmd2}")
                apply_rollback(
                    session,
                    evaluation.rollback_pid,
                    rollback_secondary_pid=evaluation.rollback_secondary_pid,
                )

                if evaluation.completed_reason == "rollback_to_best":
                    session.completed_reason = "rollback_to_best"
                    _console(
                        emit_console,
                        "\n[SUCCESS] 已回滚到历史最佳且满足可用标准，提前结束调参。",
                    )
                    _emit_lifecycle(
                        event_sink,
                        start_time,
                        "completed",
                        "Rolled back to the best stable result and finished early.",
                    )
                    break

                time.sleep(1)
                continue

            if evaluation.completed_reason == "stable_rounds_reached":
                session.completed_reason = "stable_rounds_reached"
                _console(
                    emit_console,
                    f"\n[SUCCESS] 系统已连续 {evaluation.stable_rounds} 轮达到可用稳定状态，提前结束调参。",
                )
                _emit_lifecycle(
                    event_sink,
                    start_time,
                    "completed",
                    f"Reached {evaluation.stable_rounds} stable rounds and finished early.",
                )
                break

            if evaluation.completed_reason == "low_error_converged":
                session.completed_reason = "low_error_converged"
                _console(emit_console, "\n[SUCCESS] 调参完成！")
                _emit_lifecycle(
                    event_sink,
                    start_time,
                    "completed",
                    "Hardware tuning converged with low error.",
                )
                break

            prompt_data = session.buffer.to_prompt_data()
            history_text = session.history.to_prompt_text()
            current_stream_round[0] = evaluation.round_index
            _emit_lifecycle(
                event_sink,
                start_time,
                "llm_request",
                f"Requesting PID suggestion for round {evaluation.round_index}.",
            )
            result = tuner.analyze(
                prompt_data,
                history_text,
                tuning_mode="hardware",
                prompt_context=_build_hardware_prompt_context(
                    serial_port,
                    secondary_pid=session.buffer.secondary_pid,
                ),
            )

            if not result:
                _console(emit_console, "[WARN] LLM 本轮不可用，启用保守兜底策略。")
                _emit_lifecycle(
                    event_sink,
                    start_time,
                    "fallback",
                    f"LLM unavailable at round {evaluation.round_index}; using fallback rules.",
                )
                result = build_fallback_suggestion(
                    evaluation.current_pid, evaluation.metrics
                )

            result, _primary_result, secondary_result = flatten_controller_result(
                result,
                evaluation.current_pid,
            )
            decision = finalize_decision(session, evaluation, result)
            publish_decision(event_sink, evaluation.round_index, decision)

            _console(
                emit_console,
                f"\n[Action] {decision.action} -> P={decision.safe_pid['p']}, I={decision.safe_pid['i']}, D={decision.safe_pid['d']}",
            )
            if decision.guardrail_notes:
                _console(emit_console, f"[Guardrail] {'; '.join(decision.guardrail_notes)}")
            if decision.fallback_used:
                _console(emit_console, "[Fallback] 本轮使用规则策略替代 LLM 建议。")

            cmd = _build_set_command("SET", decision.safe_pid)
            bridge.send_command(cmd)
            _emit_log(event_sink, start_time, "cmd", cmd)
            _console(emit_console, f"[CMD] Sent: {cmd}")
            if isinstance(secondary_result, dict):
                secondary_current_pid = (
                    dict(session.buffer.secondary_pid)
                    if session.buffer.secondary_pid is not None
                    else {"p": 0.0, "i": 0.0, "d": 0.0}
                )
                safe_secondary_pid, secondary_guardrail_notes = apply_pid_guardrails(
                    secondary_current_pid,
                    secondary_result,
                    limits=get_pid_limits("hardware"),
                )
                safe_secondary_pid = {
                    "p": float(safe_secondary_pid["p"]),
                    "i": float(safe_secondary_pid["i"]),
                    "d": float(safe_secondary_pid["d"]),
                }
                if secondary_guardrail_notes:
                    _console(
                        emit_console,
                        f"[Guardrail] Controller 2: {'; '.join(secondary_guardrail_notes)}",
                    )
                cmd2 = _build_set_command("SET2", safe_secondary_pid)
                bridge.send_command(cmd2)
                _emit_log(event_sink, start_time, "cmd", cmd2)
                _console(emit_console, f"[CMD] Sent: {cmd2}")
                session.buffer.secondary_pid = dict(safe_secondary_pid)

            if decision.completed_reason == "llm_marked_done":
                session.completed_reason = "llm_marked_done"
                _console(emit_console, "\n[SUCCESS] 调参完成！")
                _emit_lifecycle(
                    event_sink,
                    start_time,
                    "completed",
                    "LLM marked the tuning run as done.",
                )
                break

            if evaluation.metrics["avg_error"] < CONFIG["MIN_ERROR_THRESHOLD"]:
                session.completed_reason = "low_error_converged"
                _console(emit_console, "\n[SUCCESS] 调参完成！")
                _emit_lifecycle(
                    event_sink,
                    start_time,
                    "completed",
                    "Hardware tuning converged with low error.",
                )
                break

            time.sleep(1)

    except KeyboardInterrupt:
        session.completed_reason = "keyboard_interrupt"
        _console(emit_console, "\n[INFO] 用户停止")
        _emit_lifecycle(
            event_sink,
            start_time,
            "stopped",
            "Hardware tuning interrupted by keyboard.",
        )
    finally:
        bridge.disconnect()
        _emit_lifecycle(
            event_sink,
            start_time,
            "finished",
            f"Hardware tuning finished in {now_elapsed(start_time):.1f}s.",
        )

    return {
        "elapsed_sec": now_elapsed(start_time),
        **build_tuning_result(
            session,
            final_pid=dict(session.buffer.current_pid),
            stopped=bool(controller.should_stop) if controller is not None else False,
        ),
    }


def _run_hardware_tuning_with_tui(
    serial_port: str,
    initial_pid: dict[str, float] | None = None,
) -> dict[str, Any]:
    from sim.tui import SimulationTUIApp

    event_queue: Queue[dict[str, Any]] = Queue()
    controller = SimulationController()
    event_sink = QueueEventSink(event_queue)
    result_box: dict[str, Any] = {}
    language = choose_tui_language()

    def make_worker(pid: dict[str, float] | None) -> Callable[[], None]:
        def worker() -> None:
            result = _run_hardware_tuning_loop(
                serial_port,
                event_sink=event_sink,
                controller=app.controller,
                emit_console=False,
                initial_pid=pid,
            )
            result_box["result"] = result
            app._last_result = result

        return worker

    def next_round_factory(last_result: dict[str, Any]) -> Callable[[], None]:
        pid = last_result.get("final_pid")
        return make_worker(pid if isinstance(pid, dict) else None)

    app = SimulationTUIApp(
        event_queue=event_queue,
        controller=controller,
        worker_target=make_worker(initial_pid),
        event_sink=event_sink,
        mode_label="Hardware",
        language=language,
        next_round_factory=next_round_factory,
    )
    app.run()
    return result_box.get("result", {})


def _run_hardware_tuning_plain(
    serial_port: str,
    initial_pid: dict[str, float] | None = None,
) -> dict[str, Any]:
    print("=" * 60)
    print("  LLM PID Tuner PRO - 增强版自动调参系统")
    print("=" * 60)
    print(f"Serial Port: {serial_port}, Model: {CONFIG['LLM_MODEL_NAME']}")
    return _run_hardware_tuning_loop(
        serial_port,
        emit_console=True,
        initial_pid=initial_pid,
    )


def run_hardware_tuner(
    serial_port_arg: str | None = None,
    force_plain: bool = False,
    initial_pid: dict[str, float] | None = None,
) -> dict[str, Any]:
    initialize_runtime_config(create_if_missing=True, verbose=True)
    serial_port = resolve_serial_port(serial_port_arg)
    if not serial_port:
        print("[ERROR] 未指定串口，程序退出。")
        safe_pause()
        return {"completed_reason": "no_serial_port"}

    if not force_plain:
        try:
            return _run_hardware_tuning_with_tui(serial_port, initial_pid=initial_pid)
        except Exception as exc:
            print(f"[WARN] Failed to start the TUI ({exc}); falling back to plain output.")
            if bool(CONFIG.get("LLM_DEBUG_OUTPUT")):
                traceback.print_exc()
    return _run_hardware_tuning_plain(serial_port, initial_pid=initial_pid)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    run_hardware_tuner(args.serial_port, force_plain=args.plain)


if __name__ == "__main__":
    main()
