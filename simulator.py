#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import contextlib
import io
from queue import Queue
import time
import traceback
from typing import Any, Callable

from core.buffer import AdvancedDataBuffer
from core.config import CONFIG, initialize_runtime_config

# Alias used by run_simulation and patchable in tests
ensure_runtime_config = initialize_runtime_config
from core.tuning_session import (
    apply_rollback,
    build_tuning_result,
    create_tuning_session,
    evaluate_completed_round,
    finalize_decision,
    record_rollback_round,
)
from doctor import collect_doctor_checks, print_doctor_report, summarize_doctor_checks
from llm.client import LLMTuner
from pid_safety import (
    adapt_simulink_pid_limits,
    apply_pid_guardrails,
    build_fallback_suggestion,
    get_pid_limits,
)
from sim.model import HeatingSimulator, SETPOINT
from sim.pre_tuning_dialog import collect_pre_tuning_preferences
from sim.prompt_context import (
    build_python_sim_prompt_context,
    default_prompt_context_for_mode,
    _merge_prompt_context,
    refresh_prompt_context_for_mode,
)
from core.tuning_loop import (
    flatten_controller_result,
    publish_decision,
    publish_rollback,
    publish_round_metrics,
)
from sim.runtime import (
    EVENT_LIFECYCLE,
    EVENT_SAMPLE,
    QueueEventSink,
    SimulationController,
    emit_console_message as _console,
    emit_lifecycle as _emit_lifecycle,
    make_llm_tuner_callbacks,
    now_elapsed,
    publish_event,
    wait_while_paused,
)
from sim.simulink_setup import (
    build_simulink_initial_prompt_context,
    create_simulink_bridge,
    load_simulink_runtime_config,
    validate_simulink_runtime_config,
)
from system_id import extract_initial_pid, system_identify

from core.i18n import get_language, set_language, tr


initialize_runtime_config(create_if_missing=False, verbose=False)


def _get_configured_setpoint(default: float = SETPOINT) -> float:
    try:
        return float(CONFIG.get("MATLAB_SETPOINT", default))
    except (TypeError, ValueError):
        return float(default)

def _maybe_silence_stdout(enabled: bool):
    if enabled:
        return contextlib.nullcontext()
    return contextlib.redirect_stdout(io.StringIO())


def _publish_doctor_checks(
    doctor_checks: list[Any] | None,
    event_sink: QueueEventSink | None = None,
    emit_console: bool = True,
) -> None:
    if not doctor_checks:
        return

    summary = summarize_doctor_checks(doctor_checks)
    _console(emit_console, f"[Doctor] {summary}")
    publish_event(
        event_sink, EVENT_LIFECYCLE, phase="doctor", message=summary, elapsed_sec=0.0
    )
    for check in doctor_checks:
        if getattr(check, "status", "PASS") == "PASS":
            continue
        message = f"{check.name}: {check.detail}"
        _console(emit_console, f"[Doctor:{check.status}] {message}")
        publish_event(
            event_sink,
            EVENT_LIFECYCLE,
            phase=f"doctor_{str(check.status).lower()}",
            message=message,
            elapsed_sec=0.0,
        )


def _run_simulator_warm_start(
    sim: HeatingSimulator,
    event_sink: QueueEventSink | None = None,
    emit_console: bool = True,
) -> dict[str, float] | None:
    probe = HeatingSimulator(random_seed=0)
    probe.set_pid(0.0, 0.0, 0.0)
    time_data: list[float] = []
    temp_data: list[float] = []
    pwm_data: list[float] = []

    sample_count = max(40, min(80, int(CONFIG.get("BUFFER_SIZE", 100))))
    for _ in range(sample_count):
        probe.pwm = 255.0
        probe.update()
        data = probe.get_data()
        time_data.append(float(data["timestamp"]))
        temp_data.append(float(data["input"]))
        pwm_data.append(float(data["pwm"]))

    result = system_identify(time_data, temp_data, pwm_data)
    candidate_pid = extract_initial_pid(result, "PID")
    if not candidate_pid:
        message = tr(
            "因系统辨识未返回可用 PID，跳过热启动。",
            "Warm start skipped because system identification did not return a usable PID.",
        )
        _console(emit_console, tr(f"[热启动] {message}", f"[Warm Start] {message}"))
        publish_event(
            event_sink,
            EVENT_LIFECYCLE,
            phase="warm_start",
            message=message,
            elapsed_sec=0.0,
        )
        return None

    safe_pid, notes = apply_pid_guardrails(
        {"p": sim.kp, "i": sim.ki, "d": sim.kd},
        candidate_pid,
        limits=get_pid_limits("python_sim"),
    )
    sim.set_pid(safe_pid["p"], safe_pid["i"], safe_pid["d"])
    note_text = f" ({'; '.join(notes)})" if notes else ""
    message = tr(
        f"已应用热启动 PID: P={safe_pid['p']:.4f} I={safe_pid['i']:.4f} D={safe_pid['d']:.4f}{note_text}",
        f"Applied warm start PID: P={safe_pid['p']:.4f} I={safe_pid['i']:.4f} D={safe_pid['d']:.4f}{note_text}",
    )
    _console(emit_console, tr(f"[热启动] {message}", f"[Warm Start] {message}"))
    publish_event(
        event_sink,
        EVENT_LIFECYCLE,
        phase="warm_start",
        message=message,
        elapsed_sec=0.0,
    )
    return safe_pid


def _emit_sample_event(
    event_sink: QueueEventSink | None, sim: Any, data: dict[str, Any]
) -> None:
    """Publish EVENT_SAMPLE, including secondary PID fields when the sim
    exposes them (dual-controller Simulink setups)."""
    payload: dict[str, Any] = {
        "timestamp": float(data.get("timestamp", 0.0)),
        "setpoint": float(data.get("setpoint", 0.0)),
        "input": float(data.get("input", 0.0)),
        "pwm": float(data.get("pwm", 0.0)),
        "error": float(data.get("error", 0.0)),
        "p": float(data.get("p", 0.0)),
        "i": float(data.get("i", 0.0)),
        "d": float(data.get("d", 0.0)),
    }
    if getattr(sim, "has_secondary_pid", False):
        payload["p2"] = float(getattr(sim, "secondary_kp", 0.0))
        payload["i2"] = float(getattr(sim, "secondary_ki", 0.0))
        payload["d2"] = float(getattr(sim, "secondary_kd", 0.0))
    publish_event(event_sink, EVENT_SAMPLE, **payload)


def _collect_data(
    sim: Any,
    buffer: AdvancedDataBuffer,
    event_sink: QueueEventSink | None = None,
    controller: SimulationController | None = None,
) -> tuple[int, bool]:
    steps = 0
    max_simulink_run_steps = 200
    simulink_run_count = 0

    while not buffer.is_full():
        if not wait_while_paused(controller):
            return steps, False
        if controller is not None and controller.should_stop:
            return steps, False

        if hasattr(sim, "compute_pid"):
            sim.compute_pid()
            sim.update()
            data = sim.get_data()
            buffer.add(data)
            _emit_sample_event(event_sink, sim, data)
            steps += 1
            continue

        simulink_run_count += 1
        if simulink_run_count > max_simulink_run_steps:
            raise RuntimeError(
                "Simulink data collection timed out before filling the buffer."
            )

        sim.run_step()
        for data in sim.get_data():
            if controller is not None and controller.should_stop:
                return steps, False
            buffer.add(data)
            _emit_sample_event(event_sink, sim, data)
            steps += 1
            if buffer.is_full():
                break

    return steps, True


def _create_python_simulator(
    initial_pid: dict[str, float] | None,
    warm_start: bool,
    setpoint: float,
) -> tuple[HeatingSimulator, bool]:
    sim = HeatingSimulator(setpoint=setpoint)
    effective_warm_start = warm_start
    if initial_pid:
        sim.set_pid(initial_pid["p"], initial_pid["i"], initial_pid["d"])
        effective_warm_start = False
    return sim, effective_warm_start


def _resolve_llm_mode(mode_label: str, llm_mode: str) -> str:
    if llm_mode != "generic":
        return llm_mode

    normalized = mode_label.strip().lower()
    if normalized == "python":
        return "python_sim"
    if normalized == "simulink":
        return "simulink"
    if normalized == "hardware":
        return "hardware"
    return llm_mode


def _run_tuning_loop(
    sim: Any,
    setpoint: float,
    mode_label: str,
    llm_mode: str = "generic",
    prompt_context: dict[str, Any] | None = None,
    event_sink: QueueEventSink | None = None,
    controller: SimulationController | None = None,
    emit_console: bool = True,
    warm_start: bool = True,
    doctor_checks: list[Any] | None = None,
    disable_early_exit: bool = False,
) -> dict[str, Any]:
    llm_mode = _resolve_llm_mode(mode_label, llm_mode)
    base_pid_limits = get_pid_limits(llm_mode)
    if prompt_context is None:
        prompt_context = default_prompt_context_for_mode(sim, llm_mode)

    def _resolve_round_pid_limits(
        context: dict[str, Any] | None,
    ) -> dict[str, dict[str, float]]:
        if llm_mode != "simulink":
            return get_pid_limits(llm_mode)
        context_map = context or {}
        return adapt_simulink_pid_limits(
            base_pid_limits,
            control_domain=str(context_map.get("control_domain", "") or ""),
            controller_1_sample_time=context_map.get("controller_1_sample_time", ""),
            controller_2_sample_time=context_map.get("controller_2_sample_time", ""),
            model_fixed_step=context_map.get("model_fixed_step", ""),
        )

    pid_limits = _resolve_round_pid_limits(prompt_context)

    current_stream_round = [0]
    start_time = time.time()
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
            (lambda: controller.should_stop or controller.is_paused)
            if controller is not None
            else None
        ),
        timeout=CONFIG.get("LLM_REQUEST_TIMEOUT", 60.0),
        debug_output=CONFIG.get("LLM_DEBUG_OUTPUT", False),
    )
    session = create_tuning_session(
        initial_pid={"p": sim.kp, "i": sim.ki, "d": sim.kd}, setpoint=setpoint
    )

    _publish_doctor_checks(
        doctor_checks, event_sink=event_sink, emit_console=emit_console
    )

    if warm_start and isinstance(sim, HeatingSimulator):
        _run_simulator_warm_start(sim, event_sink=event_sink, emit_console=emit_console)

    session.buffer.current_pid = {"p": sim.kp, "i": sim.ki, "d": sim.kd}
    session.buffer.setpoint = setpoint
    _emit_lifecycle(
        event_sink, start_time, "starting", f"{mode_label} simulation started."
    )

    try:
        while session.round_num < CONFIG["MAX_TUNING_ROUNDS"]:
            if controller is not None and controller.should_stop:
                session.completed_reason = "stopped_by_user"
                _console(emit_console, "\n[INFO] Simulation stopped by user.")
                _emit_lifecycle(
                    event_sink, start_time, "stopped", "Simulation stopped by user."
                )
                break

            round_index = session.round_num + 1
            _console(emit_console, f"\n[Round {round_index}] Collecting data...")
            if hasattr(sim, "run_step"):
                session.buffer.reset()
            _emit_lifecycle(
                event_sink,
                start_time,
                "collecting",
                f"Collecting data for round {round_index}.",
            )

            steps, completed = _collect_data(
                sim, session.buffer, event_sink=event_sink, controller=controller
            )
            if not completed:
                session.completed_reason = "stopped_by_user"
                _console(emit_console, "\n[INFO] Simulation stopped by user.")
                _emit_lifecycle(
                    event_sink, start_time, "stopped", "Simulation stopped by user."
                )
                break

            _console(emit_console, f"[Round {round_index}] Collected {steps} samples.")

            evaluation = evaluate_completed_round(
                session, {"p": sim.kp, "i": sim.ki, "d": sim.kd}
            )
            publish_round_metrics(event_sink, evaluation, round_index)

            if evaluation.best_result_updated:
                _emit_lifecycle(
                    event_sink,
                    start_time,
                    "best_result",
                    f"Captured a new best stable result at round {round_index}.",
                )

            if evaluation.rollback_pid and not disable_early_exit:
                rollback_message = record_rollback_round(
                    session,
                    evaluation,
                    evaluation.rollback_pid,
                    target_round=int(evaluation.best_result["round"])
                    if evaluation.best_result
                    else None,
                )
                _console(emit_console, f"[Rollback] {rollback_message}")
                if (
                    evaluation.rollback_secondary_pid is not None
                    and hasattr(sim, "set_pid_pair")
                ):
                    sim.set_pid_pair(
                        dict(evaluation.rollback_pid),
                        dict(evaluation.rollback_secondary_pid),
                    )
                else:
                    sim.set_pid(
                        evaluation.rollback_pid["p"],
                        evaluation.rollback_pid["i"],
                        evaluation.rollback_pid["d"],
                    )
                apply_rollback(
                    session,
                    evaluation.rollback_pid,
                    rollback_secondary_pid=evaluation.rollback_secondary_pid,
                )
                publish_rollback(
                    event_sink,
                    round_index,
                    evaluation,
                    evaluation.rollback_pid,
                    rollback_message,
                )
                if (
                    evaluation.completed_reason == "rollback_to_best"
                    and not disable_early_exit
                ):
                    session.completed_reason = "rollback_to_best"
                    _emit_lifecycle(
                        event_sink,
                        start_time,
                        "completed",
                        "Rolled back to the best stable result and finished early.",
                    )
                    break
                continue

            if (
                evaluation.completed_reason == "low_error_converged"
                and not disable_early_exit
            ):
                session.completed_reason = "low_error_converged"
                _emit_lifecycle(
                    event_sink,
                    start_time,
                    "completed",
                    "Simulation converged with stable low error.",
                )
                break

            if (
                evaluation.completed_reason == "stable_rounds_reached"
                and not disable_early_exit
            ):
                session.completed_reason = "stable_rounds_reached"
                _emit_lifecycle(
                    event_sink,
                    start_time,
                    "completed",
                    f"Reached {evaluation.stable_rounds} stable rounds and finished early.",
                )
                break

            current_stream_round[0] = round_index
            _emit_lifecycle(
                event_sink,
                start_time,
                "llm_request",
                f"Requesting PID suggestion for round {round_index}.",
            )
            prompt_context = refresh_prompt_context_for_mode(
                sim,
                llm_mode,
                prompt_context,
            )
            pid_limits = _resolve_round_pid_limits(prompt_context)
            # Make the dual-controller state visible to the LLM prompt.
            if getattr(sim, "has_secondary_pid", False):
                session.buffer.secondary_pid = {
                    "p": float(getattr(sim, "secondary_kp", 0.0)),
                    "i": float(getattr(sim, "secondary_ki", 0.0)),
                    "d": float(getattr(sim, "secondary_kd", 0.0)),
                }
            result = tuner.analyze(
                session.buffer.to_prompt_data(),
                session.history.to_prompt_text(),
                tuning_mode=llm_mode,
                prompt_context=prompt_context,
            )

            if controller is not None and controller.should_stop:
                session.completed_reason = "stopped_by_user"
                _console(emit_console, "\n[INFO] Simulation stopped by user.")
                _emit_lifecycle(
                    event_sink, start_time, "stopped", "Simulation stopped by user."
                )
                break

            if controller is not None and controller.is_paused:
                if not wait_while_paused(controller):
                    session.completed_reason = "stopped_by_user"
                    break
                continue

            if not result:
                _emit_lifecycle(
                    event_sink,
                    start_time,
                    "fallback",
                    f"LLM unavailable at round {round_index}; using fallback rules.",
                )
                result = build_fallback_suggestion(
                    evaluation.current_pid,
                    evaluation.metrics,
                    limits=pid_limits,
                )

            result, primary_result, secondary_result = flatten_controller_result(
                result, evaluation.current_pid
            )

            decision = finalize_decision(
                session, evaluation, result, limits=pid_limits,
            )
            safe_pid = decision.safe_pid
            if primary_result and hasattr(sim, "set_pid_pair"):
                secondary_notes = sim.set_pid_pair(
                    dict(safe_pid),
                    dict(secondary_result) if secondary_result else None,
                ) or []
                if secondary_notes:
                    decision.guardrail_notes.extend(
                        f"Controller 2: {note}" for note in secondary_notes
                    )
                    session.guardrail_count += 1
            else:
                sim.set_pid(safe_pid["p"], safe_pid["i"], safe_pid["d"])
            publish_decision(event_sink, round_index, decision)
            if decision.guardrail_notes:
                _console(
                    emit_console,
                    f"[Guardrail] {'; '.join(decision.guardrail_notes)}",
                )

            if decision.completed_reason == "llm_marked_done" and not disable_early_exit:
                session.completed_reason = "llm_marked_done"
                _emit_lifecycle(
                    event_sink,
                    start_time,
                    "completed",
                    "LLM marked the tuning run as done.",
                )
                break

    except KeyboardInterrupt:
        session.completed_reason = "keyboard_interrupt"
        _console(emit_console, "\n[INFO] Simulation interrupted by keyboard.")
        _emit_lifecycle(
            event_sink, start_time, "stopped", "Simulation interrupted by keyboard."
        )
    except Exception as exc:
        session.completed_reason = "error"
        _console(emit_console, f"\n[ERROR] Simulation failed: {exc}")
        _emit_lifecycle(event_sink, start_time, "error", f"Simulation failed: {exc}")
        raise
    finally:
        elapsed_sec = now_elapsed(start_time)
        _emit_lifecycle(
            event_sink,
            start_time,
            "finished",
            f"Simulation finished in {elapsed_sec:.1f}s.",
        )
        summary_line = (
            f"\n[Summary] elapsed={elapsed_sec:.1f}s "
            f"final_pid=P={sim.kp:.4f} I={sim.ki:.4f} D={sim.kd:.4f}"
        )
        if getattr(sim, "has_secondary_pid", False):
            summary_line += (
                f" | C2 P={getattr(sim, 'secondary_kp', 0.0):.4f}"
                f" I={getattr(sim, 'secondary_ki', 0.0):.4f}"
                f" D={getattr(sim, 'secondary_kd', 0.0):.4f}"
            )
        _console(emit_console, summary_line)

    return {
        "elapsed_sec": now_elapsed(start_time),
        **build_tuning_result(
            session,
            final_pid={"p": sim.kp, "i": sim.ki, "d": sim.kd},
            stopped=bool(controller.should_stop) if controller is not None else False,
        ),
    }


def choose_simulink_ui_mode(force_plain: bool) -> bool:
    if force_plain:
        return False

    print("Simulink 显示模式")
    print("[1] TUI 模式（可能在部分终端出现乱码/刷屏）")
    print("[2] 命令行模式 (--plain 模式，默认更稳定)")

    try:
        choice = input("Choose a mode [2]: ").strip().lower()
    except EOFError:
        return False
    return choice in {"1", "tui"}


def _run_python_simulation_with_tui(
    warm_start: bool = True,
    doctor_checks: list[Any] | None = None,
    initial_pid: dict[str, float] | None = None,
    prompt_context_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from sim.tui import SimulationTUIApp

    event_queue: Queue[dict[str, Any]] = Queue()
    controller = SimulationController()
    event_sink = QueueEventSink(event_queue)
    result_box: dict[str, Any] = {}
    language = get_language()
    setpoint = _get_configured_setpoint()

    def make_worker(pid: dict[str, float] | None) -> Callable[[], None]:
        def worker() -> None:
            sim, effective_warm_start = _create_python_simulator(
                pid,
                warm_start,
                setpoint,
            )
            result = _run_tuning_loop(
                sim,
                setpoint,
                "Python",
                llm_mode="python_sim",
                prompt_context=_merge_prompt_context(
                    build_python_sim_prompt_context(),
                    prompt_context_overrides,
                ),
                event_sink=event_sink,
                controller=app.controller,
                emit_console=False,
                warm_start=effective_warm_start,
                doctor_checks=doctor_checks,
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
        mode_label="Python",
        language=language,
        next_round_factory=next_round_factory,
    )
    app.run()
    return result_box.get("result", {})


def _run_python_simulation_plain(
    warm_start: bool = True,
    doctor_checks: list[Any] | None = None,
    initial_pid: dict[str, float] | None = None,
    prompt_context_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    setpoint = _get_configured_setpoint()
    print("=" * 60)
    print("  LLM PID Tuner PRO - Simulation")
    print("=" * 60)
    print(f"Setpoint: {setpoint}, Model: {CONFIG['LLM_MODEL_NAME']}")
    sim, effective_warm_start = _create_python_simulator(
        initial_pid,
        warm_start,
        setpoint,
    )
    return _run_tuning_loop(
        sim,
        setpoint,
        "Python",
        llm_mode="python_sim",
        prompt_context=_merge_prompt_context(
            build_python_sim_prompt_context(),
            prompt_context_overrides,
        ),
        emit_console=True,
        warm_start=effective_warm_start,
        doctor_checks=doctor_checks,
    )


def _run_simulink_simulation_with_tui(
    doctor_checks: list[Any] | None = None,
    initial_pid: dict[str, float] | None = None,
    prompt_context_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from sim.tui import SimulationTUIApp

    event_queue: Queue[dict[str, Any]] = Queue()
    controller = SimulationController()
    event_sink = QueueEventSink(event_queue)
    result_box: dict[str, Any] = {}
    language = get_language()

    def make_worker(pid: dict[str, float] | None) -> Callable[[], None]:
        def worker() -> None:
            result = _run_simulink_simulation(
                initial_pid=pid,
                doctor_checks=doctor_checks,
                prompt_context_overrides=prompt_context_overrides,
                event_sink=event_sink,
                controller=app.controller,
                emit_console=False,
            )
            result_box["result"] = result
            app._last_result = result or {}

        return worker

    def next_round_factory(last_result: dict[str, Any]) -> Callable[[], None]:
        pid = last_result.get("final_pid")
        return make_worker(pid if isinstance(pid, dict) else None)

    app = SimulationTUIApp(
        event_queue=event_queue,
        controller=controller,
        worker_target=make_worker(initial_pid),
        event_sink=event_sink,
        mode_label="Simulink",
        language=language,
        next_round_factory=next_round_factory,
    )
    app.run()
    return result_box.get("result", {})


def _run_simulink_simulation(
    initial_pid: dict[str, float] | None = None,
    doctor_checks: list[Any] | None = None,
    prompt_context_overrides: dict[str, Any] | None = None,
    event_sink: QueueEventSink | None = None,
    controller: SimulationController | None = None,
    emit_console: bool = True,
) -> dict[str, Any] | None:
    def _emit_terminal_error(message: str) -> None:
        _console(emit_console, f"[ERROR] {message}")
        publish_event(
            event_sink,
            EVENT_LIFECYCLE,
            phase="error",
            message=message,
            elapsed_sec=0.0,
        )

    try:
        settings = load_simulink_runtime_config(CONFIG)
    except ValueError as exc:
        _emit_terminal_error(str(exc))
        return None

    validation_error = validate_simulink_runtime_config(settings)
    if validation_error:
        _emit_terminal_error(validation_error)
        return None

    try:
        sim = create_simulink_bridge(settings)
    except ImportError as exc:
        _emit_terminal_error(str(exc))
        return None

    _console(emit_console, "=" * 60)
    _console(emit_console, "  LLM PID Tuner PRO - Simulink")
    _console(emit_console, "=" * 60)
    _console(
        emit_console,
        f"Setpoint: {settings.setpoint}, Model: {CONFIG['LLM_MODEL_NAME']}",
    )
    _console(emit_console, f"Simulink model: {settings.model_path}")

    try:
        with _maybe_silence_stdout(emit_console):
            sim.connect()
    except Exception as exc:
        _emit_terminal_error(f"Failed to connect to Simulink: {exc}")
        return None

    if initial_pid:
        sim.set_pid(initial_pid["p"], initial_pid["i"], initial_pid["d"])

    try:
        return _run_tuning_loop(
            sim,
            settings.setpoint,
            "Simulink",
            llm_mode="simulink",
            prompt_context=_merge_prompt_context(
                build_simulink_initial_prompt_context(sim, settings),
                prompt_context_overrides,
            ),
            event_sink=event_sink,
            controller=controller,
            emit_console=emit_console,
            doctor_checks=doctor_checks,
            disable_early_exit=True,
        )
    finally:
        with _maybe_silence_stdout(emit_console):
            sim.disconnect()


def run_simulation(force_plain: bool = False) -> dict[str, Any] | None:
    ensure_runtime_config(verbose=True)
    doctor_checks = collect_doctor_checks()
    matlab_model_path = CONFIG.get("MATLAB_MODEL_PATH", "").strip()

    if matlab_model_path:
        use_tui = choose_simulink_ui_mode(force_plain)
        prompt_context_overrides = collect_pre_tuning_preferences("Simulink")
        if use_tui:
            try:
                tui_kwargs: dict[str, Any] = {"doctor_checks": doctor_checks}
                if prompt_context_overrides is not None:
                    tui_kwargs["prompt_context_overrides"] = prompt_context_overrides
                return _run_simulink_simulation_with_tui(**tui_kwargs)
            except Exception as exc:
                print(
                    f"[WARN] Failed to start the TUI ({exc}); falling back to plain output."
                )
                debug_enabled = bool(CONFIG.get("LLM_DEBUG_OUTPUT"))
                if debug_enabled:
                    traceback.print_exc()

        print_doctor_report(doctor_checks)
        plain_kwargs: dict[str, Any] = {"doctor_checks": doctor_checks}
        if prompt_context_overrides is not None:
            plain_kwargs["prompt_context_overrides"] = prompt_context_overrides
        return _run_simulink_simulation(**plain_kwargs)

    prompt_context_overrides = collect_pre_tuning_preferences("Python Simulation")
    if not force_plain:
        try:
            tui_kwargs = {"warm_start": True, "doctor_checks": doctor_checks}
            if prompt_context_overrides is not None:
                tui_kwargs["prompt_context_overrides"] = prompt_context_overrides
            return _run_python_simulation_with_tui(**tui_kwargs)
        except Exception as exc:
            print(
                f"[WARN] Failed to start the TUI ({exc}); falling back to plain output."
            )
            debug_enabled = bool(CONFIG.get("LLM_DEBUG_OUTPUT"))
            if debug_enabled:
                traceback.print_exc()

    print_doctor_report(doctor_checks)
    plain_kwargs = {"warm_start": True, "doctor_checks": doctor_checks}
    if prompt_context_overrides is not None:
        plain_kwargs["prompt_context_overrides"] = prompt_context_overrides
    return _run_python_simulation_plain(**plain_kwargs)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the LLM PID simulator.")
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Disable the Textual dashboard and use plain console logs.",
    )
    parser.add_argument(
        "--lang", choices=["zh", "en"], help="Override display language (zh or en)."
    )
    args = parser.parse_args(argv)

    if args.lang:
        set_language(args.lang)

    run_simulation(force_plain=args.plain)


if __name__ == "__main__":
    main()
