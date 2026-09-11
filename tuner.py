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
import time
import traceback
import webbrowser
from typing import Any, Dict, List, Optional

from core.config import CONFIG, initialize_runtime_config
from core.console import choose_ui_mode, warn_tui_fallback
from hw.bridge import SerialBridge, safe_pause, select_serial_port
from hw.profiles import DEFAULT_HARDWARE_PROFILE, normalize_hardware_profile
from llm import orcarouter
from llm.client import LLMTuner
from core.tuning_engine import run_tuning_engine
from core.adapters import HardwareEnv
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
    run_tui_tuning_session,
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
    # The two OrcaRouter entry points, side by side.  --orcarouter-login is
    # OAuth 2.0 + PKCE and issues a key; --orcarouter-key accepts one the
    # user already holds.  Neither replaces the other.
    parser.add_argument(
        "--orcarouter-login",
        action="store_true",
        help=(
            "Sign in to OrcaRouter with OAuth 2.0 + PKCE and store the issued "
            "API key. No browser available? Add --orcarouter-flow B to paste "
            "a code instead."
        ),
    )
    parser.add_argument(
        "--orcarouter-key",
        metavar="SK_ORCA_KEY",
        help=(
            "Store an existing OrcaRouter API key (sk-orca-...) and switch the "
            "provider to OrcaRouter - API. Pair it with --orcarouter-login's "
            "sibling entry point rather than replacing it."
        ),
    )
    parser.add_argument(
        "--orcarouter-flow",
        choices=("A", "B"),
        default="A",
        help=(
            "PKCE delivery: A = loopback redirect (default, needs a browser "
            "and a free local port), B = out-of-band code shown on screen."
        ),
    )
    parser.add_argument(
        "--orcarouter-status",
        action="store_true",
        help="Show the stored OrcaRouter credential without printing the key.",
    )
    parser.add_argument(
        "--orcarouter-settings",
        action="store_true",
        help=(
            "Open the local OrcaRouter settings page: paste or clear an API "
            "key, start the PKCE login, and pick a model from the live catalog."
        ),
    )
    parser.add_argument(
        "--orcarouter-logout",
        action="store_true",
        help="Clear the stored OrcaRouter credential.",
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


def choose_hardware_ui_mode(force_plain: bool) -> bool:
    return choose_ui_mode(
        force_plain,
        title="Hardware display mode",
        tui_label="TUI mode",
        plain_label="Plain console mode (--plain, default)",
    )


def _run_hardware_tuning_loop(
    serial_port: str,
    event_sink: Optional[QueueEventSink] = None,
    controller: Optional[SimulationController] = None,
    emit_console: bool = True,
    initial_pid: Optional[Dict[str, float]] = None,
    prompt_context_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    bridge = SerialBridge(serial_port, CONFIG["BAUD_RATE"], emit_console=False)
    bridge.hardware_profile = normalize_hardware_profile(
        CONFIG.get("HARDWARE_PROFILE", DEFAULT_HARDWARE_PROFILE)
    )
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
        if hasattr(bridge, "send_profile_command"):
            status_sent = bridge.send_profile_command("STATUS")
        else:
            status_sent = bridge.send_command("STATUS")
        _emit_log(event_sink, start_time, "cmd", "STATUS")
        if status_sent is False:
            warn = f"[WARN] STATUS send failed: {bridge.last_error or 'unknown write error'}"
            _console(emit_console, warn)
            _emit_log(event_sink, start_time, "warn", warn)
        else:
            _console(emit_console, "[CMD] Sent: STATUS")

        env = HardwareEnv(bridge, initial_pid or {"p": 0.0, "i": 0.0, "d": 0.0}, controller=controller)
        if initial_pid:
            if normalize_hardware_profile(getattr(bridge, "hardware_profile", DEFAULT_HARDWARE_PROFILE)) == "mspm0_datavision":
                _console(emit_console, "[INFO] MSPM0 telemetry-only profile; skipping initial PID write-back.")
            else:
                cmd = f"SET P:{initial_pid['p']} I:{initial_pid['i']} D:{initial_pid['d']}"
                env.apply_pid(initial_pid)
                _emit_log(event_sink, start_time, "cmd", cmd)
                if env.last_apply_issue:
                    warn = f"[WARN] Initial PID send failed: {env.last_apply_issue}"
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

        env.prompt_context = _merge_prompt_context(
            build_hardware_prompt_context(
                serial_port,
                None,
                hardware_profile=getattr(bridge, "hardware_profile", DEFAULT_HARDWARE_PROFILE),
            ),
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
    def run_round(
        pid: Optional[Dict[str, float]],
        event_sink: QueueEventSink,
        controller: SimulationController,
    ) -> Dict[str, Any]:
        return _run_hardware_tuning_loop(
            serial_port,
            event_sink=event_sink,
            controller=controller,
            emit_console=False,
            initial_pid=pid,
            prompt_context_overrides=prompt_context_overrides,
        )

    return run_tui_tuning_session(
        mode_label="Hardware",
        initial_pid=initial_pid,
        run_round=run_round,
    )


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
            warn_tui_fallback(exc)
    try:
        return _run_hardware_tuning_plain(serial_port, **runner_kwargs)
    except Exception as exc:
        print(f"[ERROR] Hardware tuning failed: {exc}")
        if bool(CONFIG.get("LLM_DEBUG_OUTPUT")):
            traceback.print_exc()
        return {"completed_reason": "error", "error": str(exc)}


def _apply_orcarouter_credential(credential) -> None:
    """Persist a credential into the config store every provider already uses."""
    from core.config import save_config

    store = orcarouter.OrcaRouterCredentialStore(CONFIG)
    record = store.save(credential)
    # Selecting the provider is part of the definition: a provider block that
    # nothing points at would leave the tool on its built-in default.
    if record.source == orcarouter.PROVIDER_ID_PKCE:
        CONFIG["LLM_PROVIDER"] = orcarouter.PROVIDER_ID_PKCE
    else:
        CONFIG["LLM_PROVIDER"] = orcarouter.PROVIDER_ID
    if not str(CONFIG.get("LLM_MODEL_NAME", "")).strip() or CONFIG.get(
        "LLM_MODEL_NAME"
    ) == "gpt-4o":
        CONFIG["LLM_MODEL_NAME"] = "orcarouter/auto"
    CONFIG["LLM_API_BASE_URL"] = orcarouter.resolve_origins(CONFIG).api_base
    save_config(verbose=True)
    print(
        f"[OK] OrcaRouter credential stored via {record.source} "
        f"(key={record.masked()}, generation={record.generation})."
    )
    print(
        "[INFO] Provider set to "
        f"{CONFIG['LLM_PROVIDER']}; model {CONFIG['LLM_MODEL_NAME']}. "
        "Run `python tuner.py --orcarouter-status` to review it."
    )


def run_orcarouter_command(args) -> int:
    """The two OrcaRouter entry points, plus status and logout."""
    from core.config import save_config

    initialize_runtime_config(create_if_missing=True, verbose=False)
    store = orcarouter.OrcaRouterCredentialStore(CONFIG)
    origins = orcarouter.resolve_origins(CONFIG)

    if args.orcarouter_status:
        record = store.load()
        if not record.is_set:
            print("[INFO] No OrcaRouter credential stored.")
            return 0
        print("OrcaRouter credential")
        print(f"  api_key     : {record.masked()}")
        print(f"  auth_method : {record.source or 'unknown'}")
        print(f"  scope       : {record.scope or '(not recorded)'}")
        print(f"  state       : {record.state}")
        print(f"  auth origin : {origins.auth_base}")
        print(f"  api origin  : {origins.api_base}")
        return 0

    if args.orcarouter_logout:
        record = store.clear()
        save_config(verbose=True)
        print(f"[OK] Cleared the stored OrcaRouter credential (generation {record.generation}).")
        return 0

    if args.orcarouter_key:
        key = str(args.orcarouter_key)
        credential = orcarouter.ApiKeyCredentialSource(api_key=key).acquire(
            on_hint=lambda message: print(f"[WARN] {message}")
        )
        _apply_orcarouter_credential(credential)
        return 0

    if args.orcarouter_settings:
        from core.config import save_config
        from sim.orcarouter_gui import (
            OrcaRouterCatalogService,
            OrcaRouterSettingsServer,
        )

        service = OrcaRouterCatalogService(CONFIG, origins=origins)
        with OrcaRouterSettingsServer(
            CONFIG, service=service, persist=save_config
        ) as server:
            url = server.base_url
            print(f"[INFO] OrcaRouter settings: {url}")
            print(
                "[INFO] Paste an existing API key, or use Connect with OrcaRouter "
                "to authorize in a browser. Press Ctrl+C when you are done."
            )
            try:
                webbrowser.open(url)
            except Exception:
                pass
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                print("\n[INFO] Closing the OrcaRouter settings page.")
        return 0

    if args.orcarouter_login:
        flow = str(args.orcarouter_flow or "A").upper()
        source = orcarouter.PKCECredentialSource(
            origins=origins,
            flow=flow,
            open_browser=webbrowser.open,
        )
        try:
            credential = source.acquire(on_hint=lambda message: print(f"[INFO] {message}"))
        except orcarouter.OrcaRouterError as exc:
            print(f"[ERROR] OrcaRouter login failed: {exc}")
            return 1
        _apply_orcarouter_credential(credential)
        return 0

    print("Nothing to do. Use --orcarouter-login, --orcarouter-key, "
          "--orcarouter-status or --orcarouter-logout.")
    return 0


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    if (
        args.orcarouter_login
        or args.orcarouter_key
        or args.orcarouter_status
        or args.orcarouter_logout
        or args.orcarouter_settings
    ):
        raise SystemExit(run_orcarouter_command(args))
    result = run_hardware_tuner(args.serial_port, force_plain=args.plain)
    if isinstance(result, dict) and result.get("completed_reason") in {"error", "keyboard_interrupt"}:
        safe_pause("Press Enter to exit...")


if __name__ == "__main__":
    main()
