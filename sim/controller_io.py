from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple


CONTROLLER_PARAM_CANDIDATES = {
    "p": ("P", "Kp", "P_Gain", "ProportionalGain"),
    "i": ("I", "Ki", "I_Gain", "IntegralGain"),
    "d": ("D", "Kd", "D_Gain", "DerivativeGain"),
}

SEPARATE_GAIN_PARAM_CANDIDATES = ("Gain", "Value", "K", "Coefficient")
CONTROL_SIGNAL_FALLBACK_CANDIDATES = ("u_out", "u", "pwm", "control")


@dataclass(slots=True)
class ResolvedSignal:
    name: str
    container: object


class SimulinkControllerIO:
    def __init__(
        self,
        *,
        try_engine_method: Callable[..., object | None],
        call_engine_method: Callable[..., object],
        get_field_or_none: Callable[[object, str, bool], object | None],
        is_timeseries_object: Callable[[object], bool],
        to_float_series: Callable[[object], List[float]],
        to_string_list: Callable[[object], List[str]],
    ) -> None:
        self._try_engine_method = try_engine_method
        self._call_engine_method = call_engine_method
        self._get_field_or_none = get_field_or_none
        self._is_timeseries_object = is_timeseries_object
        self._to_float_series = to_float_series
        self._to_string_list = to_string_list

    def resolve_separate_gain_param_name(self, block_path: str) -> str | None:
        if not block_path:
            return None
        for param_name in SEPARATE_GAIN_PARAM_CANDIDATES:
            if self._try_engine_method("get_param", block_path, param_name) is not None:
                return param_name
        return None

    def resolve_controller_param_name_for_path(
        self, block_path: str, gain_key: str
    ) -> str | None:
        if not block_path:
            return None
        for param_name in CONTROLLER_PARAM_CANDIDATES[gain_key]:
            if self._try_engine_method("get_param", block_path, param_name) is not None:
                return param_name
        return None

    def resolve_active_controller_path(
        self,
        *,
        gain_key: str,
        separate_gain_paths: Dict[str, str],
        pid_block_path: str,
        pid_block_paths: List[str],
    ) -> str | None:
        separate_gain_path = separate_gain_paths.get(gain_key, "")
        if separate_gain_path:
            return separate_gain_path
        if (
            pid_block_path
            and self.resolve_controller_param_name_for_path(pid_block_path, gain_key)
            is not None
        ):
            return pid_block_path
        for candidate_path in pid_block_paths:
            if not candidate_path:
                continue
            if (
                self.resolve_controller_param_name_for_path(candidate_path, gain_key)
                is not None
            ):
                return candidate_path
        return None

    def read_controller_gain(
        self,
        *,
        gain_key: str,
        default: float,
        separate_gain_paths: Dict[str, str],
        pid_block_path: str,
        pid_block_paths: List[str],
    ) -> float:
        active_path = self.resolve_active_controller_path(
            gain_key=gain_key,
            separate_gain_paths=separate_gain_paths,
            pid_block_path=pid_block_path,
            pid_block_paths=pid_block_paths,
        )
        if not active_path:
            return default

        configured_separate_path = separate_gain_paths.get(gain_key)
        if configured_separate_path:
            separate_param = self.resolve_separate_gain_param_name(active_path)
            if not separate_param:
                return default
            value = self._try_engine_method("get_param", active_path, separate_param)
        else:
            param_name = self.resolve_controller_param_name_for_path(
                active_path, gain_key
            )
            if not param_name:
                return default
            value = self._try_engine_method("get_param", active_path, param_name)

        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def write_controller_gain(
        self,
        *,
        gain_key: str,
        value: float,
        separate_gain_paths: Dict[str, str],
        pid_block_path: str,
        pid_block_paths: List[str],
    ) -> None:
        active_path = self.resolve_active_controller_path(
            gain_key=gain_key,
            separate_gain_paths=separate_gain_paths,
            pid_block_path=pid_block_path,
            pid_block_paths=pid_block_paths,
        )
        if not active_path:
            return

        configured_separate_path = separate_gain_paths.get(gain_key)
        if configured_separate_path:
            separate_param = self.resolve_separate_gain_param_name(active_path)
            if not separate_param:
                return
            self._call_engine_method(
                "set_param",
                active_path,
                separate_param,
                str(value),
                nargout=0,
            )
            return

        param_name = self.resolve_controller_param_name_for_path(active_path, gain_key)
        if not param_name:
            return
        self._call_engine_method(
            "set_param",
            active_path,
            param_name,
            str(value),
            nargout=0,
        )

    def resolve_signal_candidates(
        self,
        primary_signal: str,
        *,
        configured_candidates: Optional[List[str]] = None,
        fallback_candidates: Tuple[str, ...] = (),
    ) -> List[str]:
        candidates: List[str] = []
        for signal_name in [primary_signal, *(configured_candidates or []), *fallback_candidates]:
            normalized = str(signal_name or "").strip()
            if normalized and normalized not in candidates:
                candidates.append(normalized)
        return candidates

    def resolve_named_signal(
        self,
        sim_out: object,
        primary_signal: str,
        *,
        candidates: List[str],
    ) -> ResolvedSignal:
        for signal_name in candidates:
            direct_signal = self._get_field_or_none(sim_out, signal_name, True)
            if direct_signal is not None:
                return ResolvedSignal(signal_name, direct_signal)

            out_container = self._get_field_or_none(sim_out, "out", True)
            if out_container is not None:
                nested_signal = self._get_field_or_none(out_container, signal_name, False)
                if nested_signal is not None:
                    return ResolvedSignal(signal_name, nested_signal)

            logsout = self._get_field_or_none(sim_out, "logsout", True)
            if logsout is not None:
                nested_signal = self._try_engine_method("get", logsout, signal_name)
                if nested_signal is not None:
                    return ResolvedSignal(signal_name, nested_signal)

        available_fields = []
        try:
            field_names_raw = self._call_engine_method("fieldnames", sim_out)
            available_fields = self._to_string_list(field_names_raw)
        except Exception:
            pass

        error_msg = (
            f"[SimulinkBridge] Could not find signal '{primary_signal}' in the simulation output. "
            "Tried simOut.<signal>, simOut.out.<signal>, simOut.logsout.<signal> etc."
        )
        if available_fields:
            error_msg += f" Available fields in simOut: {', '.join(available_fields)}"
        raise RuntimeError(error_msg)

    def resolve_time_vector(self, sim_out: object) -> List[float]:
        for candidate in ("tout", "time", "Time"):
            raw_time = self._get_field_or_none(sim_out, candidate, True)
            if raw_time is not None:
                values = self._to_float_series(raw_time)
                if values:
                    return values

        out_container = self._get_field_or_none(sim_out, "out", True)
        if out_container is not None:
            for candidate in ("tout", "time", "Time"):
                raw_time = self._get_field_or_none(out_container, candidate, False)
                if raw_time is not None:
                    values = self._to_float_series(raw_time)
                    if values:
                        return values
        return []

    def extract_signal_series(self, signal_container: object, sim_out: object) -> Tuple[List[float], List[float]]:
        if self._is_timeseries_object(signal_container):
            raw_time = self._get_field_or_none(signal_container, "Time", False)
            raw_output = self._get_field_or_none(signal_container, "Data", False)
        else:
            raw_time = None
            raw_output = None

        if raw_time is not None and raw_output is not None:
            return (
                self._to_float_series(raw_time),
                self._to_float_series(raw_output),
            )

        output_values = self._to_float_series(signal_container)
        time_values = self.resolve_time_vector(sim_out)
        if not time_values:
            time_values = [float(index) for index in range(len(output_values))]
        return time_values, output_values


__all__ = [
    "CONTROL_SIGNAL_FALLBACK_CANDIDATES",
    "ResolvedSignal",
    "SimulinkControllerIO",
]
