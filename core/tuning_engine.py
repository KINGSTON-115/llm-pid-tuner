import time
from pathlib import Path
from typing import Any, Dict, Optional
from core.env import BaseTuningEnvironment
from llm.client import LLMTuner
from core.tuning_session import create_tuning_session, evaluate_completed_round, finalize_decision, record_rollback_round, apply_rollback, build_tuning_result
from core.tuning_loop import publish_round_metrics, publish_decision, flatten_controller_result, publish_rollback
from core.config import CONFIG
from sim.runtime import EVENT_SAMPLE, QueueEventSink, publish_event
from pid_safety import (
    adapt_simulink_pid_limits,
    apply_pid_guardrails,
    build_fallback_suggestion,
    get_pid_limits,
)

def _console(emit_console: bool, message: str, end: str = "\n") -> None:
    if emit_console:
        print(message, end=end, flush=True)
        # Also append to a log file
        try:
            Path("logs").mkdir(parents=True, exist_ok=True)
            with open("logs/console_log.txt", "a", encoding="utf-8") as f:
                f.write(message + end)
        except Exception:
            pass

def _emit_lifecycle(event_sink: Optional[QueueEventSink], start_time: float, phase: str, detail: str = "") -> None:
    publish_event(event_sink, "lifecycle", timestamp=time.time() - start_time, phase=phase, detail=detail)

def _emit_log(event_sink: Optional[QueueEventSink], start_time: float, level: str, message: str) -> None:
    publish_event(event_sink, "log", timestamp=time.time() - start_time, level=level, message=message)

def _emit_sample_event(event_sink: Optional[QueueEventSink], data: Dict[str, float]) -> None:
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

def run_tuning_engine(
    env: BaseTuningEnvironment,
    tuner: LLMTuner,
    llm_mode: str,
    event_sink: Optional[QueueEventSink] = None,
    controller: Any = None,
    emit_console: bool = True,
    disable_early_exit: bool = False,
    start_time: float = 0.0,
    current_stream_round: Optional[list] = None,
) -> Dict[str, Any]:
    if current_stream_round is None:
        current_stream_round = [0]
    if start_time == 0.0:
        start_time = time.time()

    primary_pid, secondary_pid = env.get_current_pid()
    session = create_tuning_session(initial_pid=primary_pid, setpoint=env.get_setpoint())
    session.buffer.current_pid = dict(primary_pid)
    session.buffer.secondary_pid = dict(secondary_pid) if secondary_pid else None
    
    prompt_context = env.get_prompt_context()
    
    base_pid_limits = get_pid_limits(llm_mode)
    if llm_mode == "simulink":
        pid_limits = adapt_simulink_pid_limits(
            base_pid_limits,
            control_domain=str(prompt_context.get("control_domain", "") or ""),
            controller_1_sample_time=prompt_context.get("controller_1_sample_time", ""),
            controller_2_sample_time=prompt_context.get("controller_2_sample_time", ""),
            model_fixed_step=prompt_context.get("model_fixed_step", ""),
        )
    else:
        pid_limits = base_pid_limits

    try:
        while session.round_num < CONFIG["MAX_TUNING_ROUNDS"]:
            if controller is not None and getattr(controller, "should_stop", False):
                session.completed_reason = "stopped_by_user"
                _console(emit_console, "\n[INFO] Tuning stopped by user.")
                _emit_lifecycle(event_sink, start_time, "stopped", "Tuning stopped by user.")
                break

            if controller is not None and hasattr(controller, "wait_while_paused"):
                if not controller.wait_while_paused():
                    session.completed_reason = "stopped_by_user"
                    _console(emit_console, "\n[INFO] Tuning stopped by user.")
                    _emit_lifecycle(event_sink, start_time, "stopped", "Tuning stopped by user.")
                    break

            round_index = session.round_num + 1
            _console(emit_console, f"\n[Round {round_index}] Collecting data...")
            env.reset_buffer_state()
            _emit_lifecycle(event_sink, start_time, "collecting", f"Collecting data for round {round_index}.")
            
            session.buffer.reset()
            samples = env.collect_samples()
            collect_warning = str(getattr(env, "last_collect_warning", "") or "").strip()
            if collect_warning:
                _console(emit_console, f"[WARN] {collect_warning}")
                _emit_lifecycle(event_sink, start_time, "warning", collect_warning)
            if not samples:
                if controller is not None and getattr(controller, "should_stop", False):
                    session.completed_reason = "stopped_by_user"
                    _console(emit_console, "\n[INFO] Tuning stopped by user.")
                    _emit_lifecycle(event_sink, start_time, "stopped", "Tuning stopped by user.")
                    break

                collect_issue = str(getattr(env, "last_collect_issue", "") or "").strip()
                if collect_issue:
                    session.completed_reason = "error"
                    _console(emit_console, f"\n[ERROR] {collect_issue}")
                    _emit_lifecycle(event_sink, start_time, "error", collect_issue)
                    break

                session.completed_reason = "stopped_by_user"
                _console(emit_console, "\n[INFO] Tuning stopped by user.")
                _emit_lifecycle(event_sink, start_time, "stopped", "Tuning stopped by user.")
                break
                
            for data in samples:
                session.buffer.add(data)
                _emit_sample_event(event_sink, data)

            _console(emit_console, f"[Round {round_index}] Collected {len(samples)} samples.")
            
            evaluation = evaluate_completed_round(session, dict(session.buffer.current_pid))
            publish_round_metrics(event_sink, evaluation, round_index)
            
            if evaluation.best_result_updated and evaluation.best_result is not None:
                best_message = (
                    f"Round {round_index} captured a new best PID: "
                    f"P={evaluation.best_result['pid']['p']}, I={evaluation.best_result['pid']['i']}, D={evaluation.best_result['pid']['d']}"
                )
                _console(emit_console, f"[Best] Updated best -> P={evaluation.best_result['pid']['p']}, I={evaluation.best_result['pid']['i']}, D={evaluation.best_result['pid']['d']}")
                _emit_log(event_sink, start_time, "best", best_message)

            if evaluation.rollback_pid and not disable_early_exit:
                rollback_message = record_rollback_round(
                    session,
                    evaluation,
                    evaluation.rollback_pid,
                    target_round=int(evaluation.best_result["round"]) if evaluation.best_result else None,
                )
                _console(emit_console, f"\n[WARN] {rollback_message}")
                _emit_lifecycle(event_sink, start_time, "rollback", rollback_message)
                
                apply_rollback(session, evaluation.rollback_pid, rollback_secondary_pid=evaluation.rollback_secondary_pid)
                publish_rollback(event_sink, round_index, evaluation, evaluation.rollback_pid, rollback_message)
                env.apply_pid(evaluation.rollback_pid, evaluation.rollback_secondary_pid)
                actual_primary, actual_secondary = env.get_current_pid()
                session.buffer.current_pid = dict(actual_primary)
                session.buffer.secondary_pid = (
                    dict(actual_secondary) if actual_secondary is not None else None
                )
                rollback_apply_issue = str(getattr(env, "last_apply_issue", "") or "").strip()
                if rollback_apply_issue:
                    session.completed_reason = "error"
                    _console(emit_console, f"\n[ERROR] {rollback_apply_issue}")
                    _emit_lifecycle(event_sink, start_time, "error", rollback_apply_issue)
                    break
                _console(emit_console, f"[CMD] Applied rollback PID.")
                continue

            if evaluation.completed_reason == "stable_rounds_reached" and not disable_early_exit:
                session.completed_reason = "stable_rounds_reached"
                _console(emit_console, f"\n[SUCCESS] Reached {evaluation.stable_rounds} stable rounds. Stopping early.")
                _emit_lifecycle(event_sink, start_time, "completed", f"Reached {evaluation.stable_rounds} stable rounds.")
                break

            if evaluation.completed_reason == "low_error_converged" and not disable_early_exit:
                session.completed_reason = "low_error_converged"
                _console(emit_console, "\n[SUCCESS] Converged with low error.")
                _emit_lifecycle(event_sink, start_time, "completed", "Converged with low error.")
                break

            prompt_data = session.buffer.to_prompt_data()
            history_text = session.history.to_prompt_text()
            current_stream_round[0] = evaluation.round_index
            _emit_lifecycle(event_sink, start_time, "llm_request", f"Requesting PID for round {evaluation.round_index}.")
            
            if llm_mode == "generic" and hasattr(env, "bridge"):
                from sim.prompt_context import (
                    build_hardware_prompt_context,
                    _merge_prompt_context,
                )
                bridge = env.bridge
                # Determine secondary pid presence via buffer state
                sec_pid = session.buffer.secondary_pid
                hardware_context = build_hardware_prompt_context(
                    getattr(bridge, "serial_port", "unknown"),
                    sec_pid
                )
                prompt_context = _merge_prompt_context(prompt_context, hardware_context)
                
            if llm_mode == "simulink" and hasattr(env, "bridge"):
                from sim.prompt_context import build_simulink_prompt_context
                bridge = env.bridge
                prompt_context = build_simulink_prompt_context(
                        model_path=getattr(bridge, "model_path", ""),
                        pid_block_path=getattr(bridge, "pid_block_path", ""),
                        output_signal=getattr(bridge, "output_signal", ""),
                        sim_step_time=getattr(bridge, "sim_step_time", 1.0),
                        control_signal=(
                            getattr(bridge, "resolved_control_signal", "")
                            or getattr(bridge, "control_signal", "")
                        ),
                        output_signal_candidates=getattr(bridge, "output_signal_candidates", []),
                        pwm_signal_available=getattr(bridge, "has_control_signal", False),
                    )
                
                # Apply dynamic guardrail hint based on config
                global_ratio_limit = float(CONFIG.get("PID_MAX_INCREASE_RATIO", 0.0))
                if global_ratio_limit > 1.0:
                    prompt_context["per_round_guardrail_hint"] = (
                        f"Simulation environment. Each round may raise P/I/D by up to {global_ratio_limit}x the current value. "
                        "You MUST strictly follow this limit."
                    )
                
                prompt_context.update({
                    "setpoint_block": getattr(bridge, "setpoint_block", ""),
                    "resolved_output_signal": getattr(bridge, "resolved_output_signal", ""),
                    "resolved_control_signal": getattr(bridge, "resolved_control_signal", ""),
                    "output_signal_candidates": getattr(bridge, "output_signal_candidates", []),
                    "controller_2_path": getattr(bridge, "secondary_pid_block_path", ""),
                    "control_domain": getattr(bridge, "control_domain", ""),
                    "model_solver_type": getattr(bridge, "model_solver_type", ""),
                    "model_solver_name": getattr(bridge, "model_solver_name", ""),
                    "model_fixed_step": getattr(bridge, "model_fixed_step", ""),
                    "controller_1_sample_time": getattr(bridge, "controller_1_sample_time", ""),
                    "controller_2_sample_time": getattr(bridge, "controller_2_sample_time", ""),
                    "controller_count": 2 if getattr(bridge, "secondary_pid_block_path", "") else 1,
                })
            
            result = tuner.analyze(
                prompt_data,
                history_text,
                tuning_mode=llm_mode,
                prompt_context=prompt_context,
            )

            if controller is not None and getattr(controller, "should_stop", False):
                session.completed_reason = "stopped_by_user"
                _console(emit_console, "\n[INFO] Tuning stopped by user.")
                _emit_lifecycle(event_sink, start_time, "stopped", "Tuning stopped by user.")
                break

            if not result:
                _console(emit_console, "[WARN] LLM unavailable, using fallback.")
                _emit_lifecycle(event_sink, start_time, "fallback", f"LLM unavailable at round {evaluation.round_index}; using fallback.")
                result = build_fallback_suggestion(evaluation.current_pid, evaluation.metrics)

            result, primary_result, secondary_result = flatten_controller_result(result, evaluation.current_pid)
            decision = finalize_decision(session, evaluation, result, limits=pid_limits)
            publish_decision(event_sink, evaluation.round_index, decision)

            _console(emit_console, f"\n[Action] {decision.action} -> P={decision.safe_pid['p']}, I={decision.safe_pid['i']}, D={decision.safe_pid['d']}")
            if decision.completed_reason == "llm_marked_done" and not disable_early_exit:
                session.completed_reason = "llm_marked_done"
                _console(emit_console, "\n[SUCCESS] LLM marked the tuning run as done.")
                _emit_lifecycle(
                    event_sink,
                    start_time,
                    "completed",
                    "LLM marked the tuning run as done.",
                )
                break

            safe_primary = decision.safe_pid
            safe_secondary = None
            if isinstance(secondary_result, dict):
                sec_curr = dict(session.buffer.secondary_pid) if session.buffer.secondary_pid is not None else {"p": 0.0, "i": 0.0, "d": 0.0}
                # Use the same limits as primary controller for secondary controller
                safe_secondary, sec_notes = apply_pid_guardrails(sec_curr, secondary_result, limits=pid_limits)
                if sec_notes:
                    _console(emit_console, f"[Guardrail] Controller 2: {'; '.join(sec_notes)}")

            env.apply_pid(safe_primary, safe_secondary)
            actual_primary, actual_secondary = env.get_current_pid()
            session.buffer.current_pid = dict(actual_primary)
            session.buffer.secondary_pid = (
                dict(actual_secondary) if actual_secondary is not None else None
            )
            apply_issue = str(getattr(env, "last_apply_issue", "") or "").strip()
            if apply_issue:
                session.completed_reason = "error"
                _console(emit_console, f"\n[ERROR] {apply_issue}")
                _emit_lifecycle(event_sink, start_time, "error", apply_issue)
                break
            _console(emit_console, f"[CMD] Applied new PID parameters.")
            
        if (
            session.round_num >= CONFIG["MAX_TUNING_ROUNDS"]
            and session.completed_reason == "max_rounds_reached"
        ):
            session.completed_reason = "max_rounds_reached"
            _console(emit_console, "\n[INFO] Reached maximum tuning rounds.")
            _emit_lifecycle(event_sink, start_time, "completed", "Reached maximum tuning rounds.")

    finally:
        pass

    return {
        "elapsed_sec": time.time() - start_time,
        **build_tuning_result(
            session,
            final_pid=dict(session.buffer.current_pid),
            stopped=session.completed_reason == "stopped_by_user",
        )
    }
