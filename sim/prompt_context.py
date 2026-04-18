from __future__ import annotations

from typing import Any, Dict, List, Optional

def build_hardware_prompt_context(
    serial_port: str,
    secondary_pid: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
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


def build_python_sim_prompt_context() -> Dict[str, Any]:
    return {
        "source": "built_in_python_heating_simulator",
        "plant_family": "single_loop_thermal",
        "controller_output_signal": "PWM",
        "pwm_signal_available": True,
        "tuning_style": "simulation_can_move_faster_than_hardware",
        "per_round_guardrail_hint": (
            "Simulation environment. Each round may raise P by up to 3x the current "
            "value, and I/D by up to 4x the current value."
        ),
    }


def _merge_prompt_context(
    base_context: Optional[Dict[str, Any]],
    extra_context: Optional[Dict[str, Any]],
) -> Dict[str, Any] | None:
    if not base_context and not extra_context:
        return None

    merged: Dict[str, Any] = {}
    if base_context:
        merged.update(base_context)
    if extra_context:
        for key, value in extra_context.items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            merged[key] = value
    return merged


def build_simulink_prompt_context(
    model_path: str,
    pid_block_path: str,
    output_signal: str,
    sim_step_time: float,
    *,
    control_signal: str = "",
    output_signal_candidates: Optional[List[str]] = None,
    setpoint_block: str = "",
    resolved_output_signal: str = "",
    resolved_control_signal: str = "",
    pwm_signal_available: Optional[bool] = None,
    controller_2_path: str = "",
    controller_count: int = 1,
    control_domain: str = "",
    model_solver_type: str = "",
    model_solver_name: str = "",
    model_fixed_step: str = "",
    controller_1_sample_time: str = "",
    controller_2_sample_time: str = "",
) -> Dict[str, Any]:
    effective_pwm_signal_available = (
        bool(control_signal) if pwm_signal_available is None else pwm_signal_available
    )
    normalized_domain = str(control_domain or "").strip().lower()
    is_discrete_domain = normalized_domain in {"discrete", "discrete_like"}
    context = {
        "source": "matlab_simulink",
        "model_path": model_path,
        "pid_block_path": pid_block_path,
        "output_signal": output_signal,
        "sim_step_time_sec": sim_step_time,
        "pwm_signal_available": effective_pwm_signal_available,
        "per_round_guardrail_hint": (
            "Discrete-time Simulink context. Keep I/D changes moderate and scale aggressiveness "
            "with controller sample time."
            if is_discrete_domain
            else "Simulation environment with wider guardrails. Each round may raise P by "
            "up to 5x the current value, and I/D by up to 6x the current value. "
            "However, if PID_MAX_INCREASE_RATIO is configured, you MUST strictly follow that limit."
        ),
        "controller_count": controller_count,
    }
    if control_domain:
        context["control_domain"] = control_domain
    if model_solver_type:
        context["model_solver_type"] = model_solver_type
    if model_solver_name:
        context["model_solver_name"] = model_solver_name
    if model_fixed_step:
        context["model_fixed_step"] = model_fixed_step
    if controller_1_sample_time:
        context["controller_1_sample_time"] = controller_1_sample_time
    if controller_2_sample_time:
        context["controller_2_sample_time"] = controller_2_sample_time
    if is_discrete_domain:
        context["discrete_tuning_hint"] = (
            "Primary goal is stability first. Increase I/D gradually when sample time is small."
        )
    if controller_count > 1:
        context["controller_structure"] = "dual_controller"
        context["controller_1_path"] = pid_block_path
        if controller_2_path:
            context["controller_2_path"] = controller_2_path
    if effective_pwm_signal_available:
        context["pwm_field_note"] = (
            "The current Simulink bridge populates PWM from the configured control signal when available. "
            "Use PWM only when the control signal is explicitly resolved."
        )
    else:
        context["pwm_field_note"] = (
            "The current Simulink bridge fills PWM with a placeholder 0.0. "
            "Do not treat zero PWM samples as real actuator saturation evidence."
        )
    if control_signal:
        context["control_signal"] = control_signal
    if output_signal_candidates:
        context["output_signal_candidates"] = list(output_signal_candidates)
    if setpoint_block:
        context["setpoint_block"] = setpoint_block
    if resolved_output_signal:
        context["resolved_output_signal"] = resolved_output_signal
    if resolved_control_signal:
        context["resolved_control_signal"] = resolved_control_signal
    return context


def _first_nonempty_text(*values: object) -> str:
    return next((text for value in values if (text := str(value or "").strip())), "")


def default_prompt_context_for_mode(sim: Any, llm_mode: str) -> Dict[str, Any] | None:
    if llm_mode == "python_sim":
        return build_python_sim_prompt_context()

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

    return build_simulink_prompt_context(
        model_path,
        pid_block_path,
        output_signal,
        sim_step_time_value,
        control_domain=str(getattr(sim, "control_domain", "") or ""),
        model_solver_type=str(getattr(sim, "model_solver_type", "") or ""),
        model_solver_name=str(getattr(sim, "model_solver_name", "") or ""),
        model_fixed_step=str(getattr(sim, "model_fixed_step", "") or ""),
        controller_1_sample_time=str(getattr(sim, "controller_1_sample_time", "") or ""),
        controller_2_sample_time=str(getattr(sim, "controller_2_sample_time", "") or ""),
    )


def refresh_prompt_context_for_mode(
    sim: Any,
    llm_mode: str,
    prompt_context: Optional[Dict[str, Any]],
) -> Dict[str, Any] | None:
    if prompt_context is None:
        return default_prompt_context_for_mode(sim, llm_mode)

    if llm_mode != "simulink":
        return dict(prompt_context)

    model_path = _first_nonempty_text(
        getattr(sim, "model_path", ""),
        prompt_context.get("model_path", ""),
    )
    pid_block_path = _first_nonempty_text(
        getattr(sim, "pid_block_path", ""),
        prompt_context.get("controller_1_path", ""),
        prompt_context.get("pid_block_path", ""),
    )
    output_signal = _first_nonempty_text(
        getattr(sim, "output_signal", ""),
        prompt_context.get("output_signal", ""),
    )
    sim_step_time = getattr(
        sim,
        "sim_step_time",
        prompt_context.get("sim_step_time_sec", 0.0),
    )
    try:
        sim_step_time_value = float(sim_step_time)
    except (TypeError, ValueError):
        sim_step_time_value = float(
            prompt_context.get("sim_step_time_sec", 0.0) or 0.0
        )

    control_signal_name = _first_nonempty_text(
        getattr(sim, "resolved_control_signal", ""),
        getattr(sim, "control_signal", ""),
        prompt_context.get("resolved_control_signal", ""),
        prompt_context.get("control_signal", ""),
    )
    resolved_output_signal_name = _first_nonempty_text(
        getattr(sim, "resolved_output_signal", ""),
        prompt_context.get("resolved_output_signal", ""),
        output_signal,
    )
    resolved_control_signal_name = _first_nonempty_text(
        getattr(sim, "resolved_control_signal", ""),
        prompt_context.get("resolved_control_signal", ""),
    )
    resolved_secondary_controller = _first_nonempty_text(
        getattr(sim, "secondary_pid_block_path", ""),
        prompt_context.get("controller_2_path", ""),
    )
    resolved_setpoint_block = _first_nonempty_text(
        getattr(sim, "setpoint_block", ""),
        prompt_context.get("setpoint_block", ""),
    )
    output_signal_candidates = prompt_context.get("output_signal_candidates")
    configured_controller_count = int(prompt_context.get("controller_count", 1) or 1)
    control_domain = _first_nonempty_text(
        getattr(sim, "control_domain", ""),
        prompt_context.get("control_domain", ""),
    )
    model_solver_type = _first_nonempty_text(
        getattr(sim, "model_solver_type", ""),
        prompt_context.get("model_solver_type", ""),
    )
    model_solver_name = _first_nonempty_text(
        getattr(sim, "model_solver_name", ""),
        prompt_context.get("model_solver_name", ""),
    )
    model_fixed_step = _first_nonempty_text(
        getattr(sim, "model_fixed_step", ""),
        prompt_context.get("model_fixed_step", ""),
    )
    controller_1_sample_time = _first_nonempty_text(
        getattr(sim, "controller_1_sample_time", ""),
        prompt_context.get("controller_1_sample_time", ""),
    )
    controller_2_sample_time = _first_nonempty_text(
        getattr(sim, "controller_2_sample_time", ""),
        prompt_context.get("controller_2_sample_time", ""),
    )
    pwm_signal_available = getattr(
        sim,
        "has_control_signal",
        prompt_context.get("pwm_signal_available", False),
    )

    refreshed_context = build_simulink_prompt_context(
        model_path=model_path,
        pid_block_path=pid_block_path,
        output_signal=output_signal,
        sim_step_time=sim_step_time_value,
        control_signal=control_signal_name,
        output_signal_candidates=list(output_signal_candidates)
        if output_signal_candidates
        else None,
        setpoint_block=resolved_setpoint_block,
        resolved_output_signal=resolved_output_signal_name,
        resolved_control_signal=resolved_control_signal_name,
        pwm_signal_available=bool(pwm_signal_available),
        controller_2_path=resolved_secondary_controller,
        controller_count=(
            2
            if resolved_secondary_controller or configured_controller_count > 1
            else 1
        ),
        control_domain=control_domain,
        model_solver_type=model_solver_type,
        model_solver_name=model_solver_name,
        model_fixed_step=model_fixed_step,
        controller_1_sample_time=controller_1_sample_time,
        controller_2_sample_time=controller_2_sample_time,
    )
    for key, value in prompt_context.items():
        refreshed_context.setdefault(key, value)
    return refreshed_context


__all__ = [
    "build_python_sim_prompt_context",
    "build_simulink_prompt_context",
    "default_prompt_context_for_mode",
    "_first_nonempty_text",
    "_merge_prompt_context",
    "refresh_prompt_context_for_mode",
]
