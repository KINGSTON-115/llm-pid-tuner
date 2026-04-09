from __future__ import annotations

from typing import Any, Mapping

from core.compat import slotted_dataclass
from sim.prompt_context import build_simulink_prompt_context, _first_nonempty_text


@slotted_dataclass
class SimulinkRuntimeConfig:
    model_path: str
    pid_block_path: str
    matlab_root: str
    output_signal: str
    control_signal: str
    setpoint_block: str
    output_signal_candidates: list[str]
    pid_block_paths: list[str]
    p_block_path: str
    i_block_path: str
    d_block_path: str
    pid_block_path_2: str
    p_block_path_2: str
    i_block_path_2: str
    d_block_path_2: str
    sim_step_time: float
    setpoint: float


def _normalized_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def load_simulink_runtime_config(config: Mapping[str, Any]) -> SimulinkRuntimeConfig:
    try:
        sim_step_time = float(config.get("MATLAB_SIM_STEP_TIME", 10.0))
        setpoint = float(config.get("MATLAB_SETPOINT", 200.0))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid Simulink numeric configuration: {exc}") from exc

    return SimulinkRuntimeConfig(
        model_path=str(config.get("MATLAB_MODEL_PATH", "") or "").strip(),
        pid_block_path=str(config.get("MATLAB_PID_BLOCK_PATH", "") or "").strip(),
        matlab_root=str(config.get("MATLAB_ROOT", "") or "").strip(),
        output_signal=str(config.get("MATLAB_OUTPUT_SIGNAL", "") or "").strip(),
        control_signal=str(config.get("MATLAB_CONTROL_SIGNAL", "") or "").strip(),
        setpoint_block=str(config.get("MATLAB_SETPOINT_BLOCK", "") or "").strip(),
        output_signal_candidates=_normalized_string_list(
            config.get("MATLAB_OUTPUT_SIGNAL_CANDIDATES", [])
        ),
        pid_block_paths=_normalized_string_list(config.get("MATLAB_PID_BLOCK_PATHS", [])),
        p_block_path=str(config.get("MATLAB_P_BLOCK_PATH", "") or "").strip(),
        i_block_path=str(config.get("MATLAB_I_BLOCK_PATH", "") or "").strip(),
        d_block_path=str(config.get("MATLAB_D_BLOCK_PATH", "") or "").strip(),
        pid_block_path_2=str(config.get("MATLAB_PID_BLOCK_PATH_2", "") or "").strip(),
        p_block_path_2=str(config.get("MATLAB_P_BLOCK_PATH_2", "") or "").strip(),
        i_block_path_2=str(config.get("MATLAB_I_BLOCK_PATH_2", "") or "").strip(),
        d_block_path_2=str(config.get("MATLAB_D_BLOCK_PATH_2", "") or "").strip(),
        sim_step_time=sim_step_time,
        setpoint=setpoint,
    )


def validate_simulink_runtime_config(settings: SimulinkRuntimeConfig) -> str | None:
    if not settings.output_signal:
        return "MATLAB_OUTPUT_SIGNAL is required for Simulink mode."
    return None


def create_simulink_bridge(settings: SimulinkRuntimeConfig):
    from sim.simulink_bridge import SimulinkBridge

    sim = SimulinkBridge(
        model_path=settings.model_path,
        setpoint=settings.setpoint,
        pid_block_path=settings.pid_block_path,
        output_signal=settings.output_signal,
        matlab_root=settings.matlab_root,
        sim_step_time=settings.sim_step_time,
        control_signal=settings.control_signal,
        output_signal_candidates=settings.output_signal_candidates,
        setpoint_block=settings.setpoint_block,
        pid_block_paths=settings.pid_block_paths,
        p_block_path=settings.p_block_path,
        i_block_path=settings.i_block_path,
        d_block_path=settings.d_block_path,
    )
    sim.secondary_pid_block_path = settings.pid_block_path_2
    sim.secondary_pid_block_paths = (
        [settings.pid_block_path_2] if settings.pid_block_path_2 else []
    )
    sim.secondary_separate_gain_paths = {
        "p": settings.p_block_path_2,
        "i": settings.i_block_path_2,
        "d": settings.d_block_path_2,
    }
    return sim


def build_simulink_initial_prompt_context(
    sim: Any,
    settings: SimulinkRuntimeConfig,
) -> dict[str, Any]:
    resolved_primary_controller = _first_nonempty_text(
        getattr(sim, "pid_block_path", ""),
        settings.pid_block_path,
        ", ".join(settings.pid_block_paths),
        settings.p_block_path,
        settings.i_block_path,
        settings.d_block_path,
    )
    resolved_secondary_controller = _first_nonempty_text(
        getattr(sim, "secondary_pid_block_path", ""),
        settings.pid_block_path_2,
        settings.p_block_path_2,
        settings.i_block_path_2,
        settings.d_block_path_2,
    )
    resolved_setpoint_block = _first_nonempty_text(
        getattr(sim, "setpoint_block", ""),
        settings.setpoint_block,
    )
    resolved_output_signal_name = _first_nonempty_text(
        getattr(sim, "resolved_output_signal", ""),
        getattr(sim, "output_signal", ""),
        settings.output_signal,
    )
    resolved_control_signal_name = _first_nonempty_text(
        getattr(sim, "resolved_control_signal", ""),
        settings.control_signal,
    )

    return build_simulink_prompt_context(
        model_path=settings.model_path,
        pid_block_path=resolved_primary_controller,
        output_signal=settings.output_signal,
        sim_step_time=settings.sim_step_time,
        control_signal=resolved_control_signal_name,
        output_signal_candidates=settings.output_signal_candidates,
        setpoint_block=resolved_setpoint_block,
        resolved_output_signal=resolved_output_signal_name,
        resolved_control_signal=resolved_control_signal_name,
        pwm_signal_available=bool(getattr(sim, "has_control_signal", False)),
        controller_2_path=resolved_secondary_controller,
        controller_count=2 if resolved_secondary_controller else 1,
        control_domain=str(getattr(sim, "control_domain", "") or ""),
        model_solver_type=str(getattr(sim, "model_solver_type", "") or ""),
        model_solver_name=str(getattr(sim, "model_solver_name", "") or ""),
        model_fixed_step=str(getattr(sim, "model_fixed_step", "") or ""),
        controller_1_sample_time=str(
            getattr(sim, "controller_1_sample_time", "") or ""
        ),
        controller_2_sample_time=str(
            getattr(sim, "controller_2_sample_time", "") or ""
        ),
    )


__all__ = [
    "SimulinkRuntimeConfig",
    "build_simulink_initial_prompt_context",
    "create_simulink_bridge",
    "load_simulink_runtime_config",
    "validate_simulink_runtime_config",
]
