#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import importlib
from queue import Queue
import sys
import time
import traceback
from typing import Any

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
from pid_safety import apply_pid_guardrails, build_fallback_suggestion
from sim.model import HeatingSimulator, SETPOINT
from sim.runtime import (
    EVENT_DECISION,
    EVENT_LIFECYCLE,
    EVENT_LOG,
    EVENT_ROLLBACK,
    EVENT_ROUND_METRICS,
    EVENT_SAMPLE,
    QueueEventSink,
    SimulationController,
    now_elapsed,
    publish_event,
    wait_while_paused,
)
from system_id import extract_initial_pid, system_identify

from core.i18n import get_language, set_language, tr


initialize_runtime_config(create_if_missing=False, verbose=False)


def choose_tui_language(default: str | None = None) -> str:
    if default is None:
        default = get_language()
    return default


def _console(enabled: bool, message: str) -> None:
    if enabled:
        print(message)


def _emit_lifecycle(
    event_sink: QueueEventSink | None, start_time: float, phase: str, message: str
) -> None:
    publish_event(
        event_sink,
        EVENT_LIFECYCLE,
        phase=phase,
        message=message,
        elapsed_sec=now_elapsed(start_time),
    )


def _emit_log(
    event_sink: QueueEventSink | None,
    start_time: float,
    label: str,
    message: str,
    *,
    replace_last: bool = False,
    stream_id: int | None = None,
) -> None:
    publish_event(
        event_sink,
        EVENT_LOG,
        label=label,
        message=message,
        replace_last=replace_last,
        stream_id=stream_id,
        elapsed_sec=now_elapsed(start_time),
    )


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
        {"p": sim.kp, "i": sim.ki, "d": sim.kd}, candidate_pid
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
            publish_event(
                event_sink,
                EVENT_SAMPLE,
                timestamp=float(data.get("timestamp", 0.0)),
                setpoint=float(data.get("setpoint", 0.0)),
                input=float(data.get("input", 0.0)),
                pwm=float(data.get("pwm", 0.0)),
                error=float(data.get("error", 0.0)),
                p=float(data.get("p", 0.0)),
                i=float(data.get("i", 0.0)),
                d=float(data.get("d", 0.0)),
            )
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
            publish_event(
                event_sink,
                EVENT_SAMPLE,
                timestamp=float(data.get("timestamp", 0.0)),
                setpoint=float(data.get("setpoint", 0.0)),
                input=float(data.get("input", 0.0)),
                pwm=float(data.get("pwm", 0.0)),
                error=float(data.get("error", 0.0)),
                p=float(data.get("p", 0.0)),
                i=float(data.get("i", 0.0)),
                d=float(data.get("d", 0.0)),
            )
            steps += 1
            if buffer.is_full():
                break

    return steps, True


def _build_python_sim_prompt_context() -> dict[str, Any]:
    return {
        "source": "built_in_python_heating_simulator",
        "plant_family": "single_loop_thermal",
        "controller_output_signal": "PWM",
        "pwm_signal_available": True,
        "tuning_style": "simulation_can_move_faster_than_hardware",
        "per_round_guardrail_hint": "仿真环境，可适度加大调整幅度，每轮 P 值可调整至当前值的3倍以内，I/D 可调整至当前值的4倍以内。",
    }


def _build_simulink_prompt_context(
    model_path: str, pid_block_path: str, output_signal: str, sim_step_time: float
) -> dict[str, Any]:
    return {
        "source": "matlab_simulink",
        "model_path": model_path,
        "pid_block_path": pid_block_path,
        "output_signal": output_signal,
        "sim_step_time_sec": sim_step_time,
        "pwm_signal_available": False,
        "per_round_guardrail_hint": "仿真环境安全，可以大胆调整参数，每轮 P 值可调整至当前值的5倍以内，I/D 可调整至当前值的6倍以内。",
        "pwm_field_note": (
            "The current Simulink bridge fills PWM with a placeholder 0.0. "
            "Do not treat zero PWM samples as real actuator saturation evidence."
        ),
    }


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


def _default_prompt_context_for_mode(sim: Any, llm_mode: str) -> dict[str, Any] | None:
    if llm_mode == "python_sim":
        return _build_python_sim_prompt_context()

    if llm_mode != "simulink":
        return None

    model_path = str(getattr(sim, "model_path", "") or "")
    pid_block_path = str(getattr(sim, "pid_block_path", "") or "")
    output_signal = str(getattr(sim, "output_signal", "") or "")
    sim_step_time = getattr(sim, "sim_step_time", 0.0)
    try:
        sim_step_time_value = float(sim_step_time)
    except (TypeError, ValueError):
        sim_step_time_value = 0.0

    if not model_path and not pid_block_path and not output_signal:
        return None

    return _build_simulink_prompt_context(
        model_path, pid_block_path, output_signal, sim_step_time_value
    )


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
    if prompt_context is None:
        prompt_context = _default_prompt_context_for_mode(sim, llm_mode)

    current_stream_round = [0]

    def llm_log_callback(label: str, message: str) -> None:
        _emit_log(
            event_sink,
            start_time,
            label,
            message,
            stream_id=current_stream_round[0] or None,
        )

    def llm_stream_callback(text: str, done: bool) -> None:
        _emit_log(
            event_sink,
            start_time,
            "llm_stream",
            text,
            replace_last=True,
            stream_id=current_stream_round[0] or None,
        )

    start_time = time.time()

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
            # Simulink 每轮独立仿真，需在采集前清空上轮缓冲数据
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
            publish_event(
                event_sink,
                EVENT_ROUND_METRICS,
                round=round_index,
                avg_error=float(evaluation.metrics["avg_error"]),
                max_error=float(evaluation.metrics["max_error"]),
                steady_state_error=float(evaluation.metrics["steady_state_error"]),
                overshoot=float(evaluation.metrics["overshoot"]),
                zero_crossings=int(evaluation.metrics["zero_crossings"]),
                status=str(evaluation.metrics["status"]),
                stable_rounds=evaluation.stable_rounds,
            )

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
                sim.set_pid(
                    evaluation.rollback_pid["p"],
                    evaluation.rollback_pid["i"],
                    evaluation.rollback_pid["d"],
                )
                apply_rollback(session, evaluation.rollback_pid)
                publish_event(
                    event_sink,
                    EVENT_ROLLBACK,
                    round=round_index,
                    target_round=int(evaluation.best_result["round"])
                    if evaluation.best_result
                    else round_index,
                    pid=dict(evaluation.rollback_pid),
                    reason=rollback_message,
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
            result = tuner.analyze(
                session.buffer.to_prompt_data(),
                session.history.to_prompt_text(),
                tuning_mode=llm_mode,
                prompt_context=prompt_context,
            )

            # LLM 被 stop 中断
            if controller is not None and controller.should_stop:
                session.completed_reason = "stopped_by_user"
                _console(emit_console, "\n[INFO] Simulation stopped by user.")
                _emit_lifecycle(
                    event_sink, start_time, "stopped", "Simulation stopped by user."
                )
                break

            # LLM 被 pause 中断 → 等待恢复后重做本轮
            if controller is not None and controller.is_paused:
                if not wait_while_paused(controller):
                    session.completed_reason = "stopped_by_user"
                    break
                continue  # buffer 仍满 → _collect_data 立即返回 → 重新请求 LLM

            if not result:
                _emit_lifecycle(
                    event_sink,
                    start_time,
                    "fallback",
                    f"LLM unavailable at round {round_index}; using fallback rules.",
                )
                result = build_fallback_suggestion(
                    evaluation.current_pid, evaluation.metrics
                )

            decision = finalize_decision(session, evaluation, result)
            sim.set_pid(
                decision.safe_pid["p"], decision.safe_pid["i"], decision.safe_pid["d"]
            )
            publish_event(
                event_sink,
                EVENT_DECISION,
                round=round_index,
                action=decision.action,
                analysis_summary=decision.analysis,
                fallback_used=decision.fallback_used,
                guardrail_notes=list(decision.guardrail_notes),
            )

            if decision.completed_reason == "llm_marked_done":
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
        _console(
            emit_console,
            (
                f"\n[Summary] elapsed={elapsed_sec:.1f}s "
                f"final_pid=P={sim.kp:.4f} I={sim.ki:.4f} D={sim.kd:.4f}"
            ),
        )

    return {
        "elapsed_sec": now_elapsed(start_time),
        **build_tuning_result(
            session,
            final_pid={"p": sim.kp, "i": sim.ki, "d": sim.kd},
            stopped=bool(controller.should_stop) if controller is not None else False,
        ),
    }


def determine_tui_mode(
    force_plain: bool, matlab_model_path: str
) -> tuple[bool, str | None]:
    if force_plain:
        return False, None

    if matlab_model_path:
        return (
            False,
            "Simulink mode does not support the TUI yet; falling back to plain output.",
        )

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return (
            False,
            "The TUI requires an interactive terminal; falling back to plain output.",
        )

    try:
        # Import probing is more reliable than find_spec() in frozen executables.
        importlib.import_module("sim.tui")
    except ImportError as exc:
        return (
            False,
            f"TUI dependencies are missing: {exc}. Falling back to plain output.",
        )

    return True, None


def _run_python_simulation_with_tui(
    warm_start: bool = True, doctor_checks: list[Any] | None = None
) -> dict[str, Any]:
    from sim.tui import SimulationTUIApp

    event_queue: Queue[dict[str, Any]] = Queue()
    controller = SimulationController()
    event_sink = QueueEventSink(event_queue)
    sim = HeatingSimulator()
    result_box: dict[str, Any] = {}
    language = choose_tui_language()

    def worker() -> None:
        result_box["result"] = _run_tuning_loop(
            sim,
            SETPOINT,
            "Python",
            llm_mode="python_sim",
            prompt_context=_build_python_sim_prompt_context(),
            event_sink=event_sink,
            controller=controller,
            emit_console=False,
            warm_start=warm_start,
            doctor_checks=doctor_checks,
        )

    SimulationTUIApp(
        event_queue=event_queue,
        controller=controller,
        worker_target=worker,
        event_sink=event_sink,
        mode_label="Python",
        language=language,
    ).run()
    return result_box.get("result", {})


def _run_python_simulation_plain(
    warm_start: bool = True, doctor_checks: list[Any] | None = None
) -> dict[str, Any]:
    print("=" * 60)
    print("  LLM PID Tuner PRO - Simulation")
    print("=" * 60)
    print(f"Setpoint: {SETPOINT}, Model: {CONFIG['LLM_MODEL_NAME']}")
    sim = HeatingSimulator()
    return _run_tuning_loop(
        sim,
        SETPOINT,
        "Python",
        llm_mode="python_sim",
        prompt_context=_build_python_sim_prompt_context(),
        emit_console=True,
        warm_start=warm_start,
        doctor_checks=doctor_checks,
    )


def _run_simulink_simulation() -> dict[str, Any] | None:
    try:
        from sim.simulink_bridge import SimulinkBridge
    except ImportError as exc:
        print(f"[ERROR] {exc}")
        return None

    matlab_model_path = CONFIG.get("MATLAB_MODEL_PATH", "").strip()
    pid_block_path = CONFIG.get("MATLAB_PID_BLOCK_PATH", "").strip()
    output_signal = CONFIG.get("MATLAB_OUTPUT_SIGNAL", "").strip()

    try:
        sim_step_time = float(CONFIG.get("MATLAB_SIM_STEP_TIME", 10.0))
        setpoint = float(CONFIG.get("MATLAB_SETPOINT", 200.0))
    except (TypeError, ValueError) as exc:
        print(f"[ERROR] Invalid Simulink numeric configuration: {exc}")
        return None

    if not pid_block_path:
        print("[ERROR] MATLAB_PID_BLOCK_PATH is required for Simulink mode.")
        return None
    if not output_signal:
        print("[ERROR] MATLAB_OUTPUT_SIGNAL is required for Simulink mode.")
        return None

    print("=" * 60)
    print("  LLM PID Tuner PRO - Simulink")
    print("=" * 60)
    print(f"Setpoint: {setpoint}, Model: {CONFIG['LLM_MODEL_NAME']}")
    print(f"Simulink model: {matlab_model_path}")

    sim = SimulinkBridge(
        model_path=matlab_model_path,
        setpoint=setpoint,
        pid_block_path=pid_block_path,
        output_signal=output_signal,
        sim_step_time=sim_step_time,
    )

    try:
        sim.connect()
    except Exception as exc:
        print(f"[ERROR] Failed to connect to Simulink: {exc}")
        return None

    try:
        return _run_tuning_loop(
            sim,
            setpoint,
            "Simulink",
            llm_mode="simulink",
            prompt_context=_build_simulink_prompt_context(
                matlab_model_path, pid_block_path, output_signal, sim_step_time
            ),
            emit_console=True,
            disable_early_exit=True,
        )
    finally:
        sim.disconnect()


def run_simulation(force_plain: bool = False) -> dict[str, Any] | None:
    ensure_runtime_config(verbose=True)
    doctor_checks = collect_doctor_checks()
    matlab_model_path = CONFIG.get("MATLAB_MODEL_PATH", "").strip()
    use_tui, fallback_message = determine_tui_mode(force_plain, matlab_model_path)

    if fallback_message:
        print(f"[WARN] {fallback_message}")

    if matlab_model_path:
        print_doctor_report(doctor_checks)
        return _run_simulink_simulation()

    if use_tui:
        try:
            return _run_python_simulation_with_tui(
                warm_start=True, doctor_checks=doctor_checks
            )
        except Exception as exc:
            print(
                f"[WARN] Failed to start the TUI ({exc}); falling back to plain output."
            )
            debug_enabled = bool(CONFIG.get("LLM_DEBUG_OUTPUT"))
            if debug_enabled:
                traceback.print_exc()

    print_doctor_report(doctor_checks)
    return _run_python_simulation_plain(warm_start=True, doctor_checks=doctor_checks)


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
