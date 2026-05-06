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
from typing import Any, Callable, Dict, List, Optional

from core.config import CONFIG, initialize_runtime_config
from hw.bridge import SerialBridge, safe_pause, select_serial_port
from llm.client import LLMTuner
from core.tuning_engine import run_tuning_engine
from core.adapters import HardwareEnv
from core.i18n import get_language
from sim.pre_tuning_dialog import collect_pre_tuning_preferences
from sim.prompt_context import build_hardware_prompt_context, _merge_prompt_context
from sim.runtime import (
    QueueEventSink,
    SimulationController,
    emit_console_message as _console,
    emit_lifecycle as _emit_lifecycle,
    emit_log as _emit_log,
    make_llm_tuner_callbacks,
    now_elapsed,
)


def _build_set_command(prefix: str, pid: Dict[str, float]) -> str:
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


def resolve_serial_port(serial_port_arg: Optional[str]) -> str | None:
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


def choose_hardware_ui_mode(force_plain: bool) -> bool:
    if force_plain:
        return False

    print("Hardware display mode")
    print("[1] TUI mode")
    print("[2] Plain console mode (--plain, default)")

    try:
        choice = input("Choose a mode [2]: ").strip().lower()
    except EOFError:
        return False
    return choice in {"1", "tui"}


def _run_hardware_tuning_loop(
    serial_port: str,
    event_sink: Optional[QueueEventSink] = None,
    controller: Optional[SimulationController] = None,
    emit_console: bool = True,
    initial_pid: Optional[Dict[str, float]] = None,
    prompt_context_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    bridge = SerialBridge(serial_port, CONFIG["BAUD_RATE"], emit_console=False)
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
        abort_check=(
            (lambda: bool(getattr(controller, "should_stop", False)))
            if controller is not None
            else None
        ),
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
        _console(emit_console, f"[ERROR] {message}")
        _emit_lifecycle(event_sink, start_time, "error", message)
        return {
            "elapsed_sec": now_elapsed(start_time),
            "round_num": 0,
            "completed_reason": "error",
            "history": [],
            "best_result": None,
            "final_pid": initial_pid or {"p": 0, "i": 0, "d": 0},
        }

    _console(emit_console, f"[INFO] 已连接到串口: {serial_port}")
    _emit_lifecycle(
        event_sink,
        start_time,
        "connected",
        f"Connected to {serial_port}.",
    )

    try:
        _console(emit_console, "[CMD] Sending: STATUS")
        status_sent = bridge.send_command("STATUS")
        _emit_log(event_sink, start_time, "cmd", "STATUS")
        if status_sent is False:
            warn = f"[WARN] STATUS send failed: {bridge.last_error or 'unknown write error'}"
            _console(emit_console, warn)
            _emit_log(event_sink, start_time, "warn", warn)
        else:
            _console(emit_console, "[CMD] Sent: STATUS")
        if initial_pid:
            cmd = f"SET P:{initial_pid['p']} I:{initial_pid['i']} D:{initial_pid['d']}"
            cmd_sent = bridge.send_command(cmd)
            _emit_log(event_sink, start_time, "cmd", cmd)
            if cmd_sent is False:
                warn = f"[WARN] Initial PID send failed: {bridge.last_error or 'unknown write error'}"
                _console(emit_console, warn)
                _emit_log(event_sink, start_time, "warn", warn)
            else:
                _console(emit_console, f"[CMD] Initial PID: {cmd}")
        time.sleep(1)

        _console(emit_console, "[INFO] 开始采集数据...")
        _emit_lifecycle(
            event_sink,
            start_time,
            "collecting",
            f"Collecting data from {serial_port}.",
        )

        env = HardwareEnv(bridge, initial_pid or {"p": 0.0, "i": 0.0, "d": 0.0}, controller=controller)
        env.prompt_context = _merge_prompt_context(
            build_hardware_prompt_context(serial_port, None),
            prompt_context_overrides,
        )

        return run_tuning_engine(
            env=env,
            tuner=tuner,
            llm_mode="generic",
            event_sink=event_sink,
            controller=controller,
            emit_console=emit_console,
            disable_early_exit=False,
            start_time=start_time,
            current_stream_round=current_stream_round,
        )

    except KeyboardInterrupt:
        _console(emit_console, "\n[INFO] 用户中断 (Ctrl+C)。")
        _emit_lifecycle(
            event_sink, start_time, "stopped", "Hardware tuning interrupted by keyboard."
        )
        return {
            "elapsed_sec": now_elapsed(start_time),
            "round_num": 0,
            "completed_reason": "keyboard_interrupt",
            "history": [],
            "best_result": None,
            "final_pid": initial_pid or {"p": 0, "i": 0, "d": 0},
        }
    except Exception as exc:
        _console(emit_console, f"\n[ERROR] 调参过程发生异常: {exc}")
        _emit_lifecycle(
            event_sink, start_time, "error", f"Hardware tuning failed: {exc}"
        )
        raise
    finally:
        bridge.disconnect()


def _run_hardware_tuning_with_tui(
    serial_port: str,
    initial_pid: Optional[Dict[str, float]] = None,
    prompt_context_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from sim.tui import SimulationTUIApp

    event_queue: Queue[Dict[str, Any]] = Queue()
    controller = SimulationController()
    event_sink = QueueEventSink(event_queue)
    result_box: Dict[str, Any] = {}
    language = get_language()

    def make_worker(pid: Optional[Dict[str, float]]) -> Callable[[], None]:
        def worker() -> None:
            result = _run_hardware_tuning_loop(
                serial_port,
                event_sink=event_sink,
                controller=app.controller,
                emit_console=False,
                initial_pid=pid,
                prompt_context_overrides=prompt_context_overrides,
            )
            result_box["result"] = result
            app._last_result = result

        return worker

    def next_round_factory(last_result: Dict[str, Any]) -> Callable[[], None]:
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
    initial_pid: Optional[Dict[str, float]] = None,
    prompt_context_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    print("=" * 60)
    print("  LLM PID Tuner PRO - 增强版自动调参系统")
    print("=" * 60)
    print(f"Serial Port: {serial_port}, Model: {CONFIG['LLM_MODEL_NAME']}")
    return _run_hardware_tuning_loop(
        serial_port,
        emit_console=True,
        initial_pid=initial_pid,
        prompt_context_overrides=prompt_context_overrides,
    )


def run_hardware_tuner(
    serial_port_arg: Optional[str] = None,
    force_plain: bool = False,
    initial_pid: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    initialize_runtime_config(create_if_missing=True, verbose=True)
    serial_port = resolve_serial_port(serial_port_arg)
    if not serial_port:
        print("[ERROR] 未指定串口，程序退出。")
        safe_pause()
        return {"completed_reason": "no_serial_port"}

    use_tui = choose_hardware_ui_mode(force_plain)
    prompt_context_overrides = collect_pre_tuning_preferences("Hardware")
    runner_kwargs: Dict[str, Any] = {"initial_pid": initial_pid}
    if prompt_context_overrides is not None:
        runner_kwargs["prompt_context_overrides"] = prompt_context_overrides

    if use_tui:
        try:
            return _run_hardware_tuning_with_tui(serial_port, **runner_kwargs)
        except Exception as exc:
            print(f"[WARN] Failed to start the TUI ({exc}); falling back to plain output.")
            if bool(CONFIG.get("LLM_DEBUG_OUTPUT")):
                traceback.print_exc()
    try:
        return _run_hardware_tuning_plain(serial_port, **runner_kwargs)
    except Exception as exc:
        print(f"[ERROR] Hardware tuning failed: {exc}")
        if bool(CONFIG.get("LLM_DEBUG_OUTPUT")):
            traceback.print_exc()
        return {"completed_reason": "error", "error": str(exc)}


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    result = run_hardware_tuner(args.serial_port, force_plain=args.plain)
    if isinstance(result, dict) and result.get("completed_reason") in {"error", "keyboard_interrupt"}:
        safe_pause("Press Enter to exit...")


if __name__ == "__main__":
    main()
