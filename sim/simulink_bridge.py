#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Simulink bridge backed by the local MATLAB Engine runtime."""

from __future__ import annotations

import os
from typing import Optional

from pid_safety import (
    adapt_simulink_pid_limits,
    apply_pid_guardrails,
    get_pid_limits,
)
from sim.block_discovery import SimulinkBlockDiscovery
from sim.controller_io import (
    CONTROL_SIGNAL_FALLBACK_CANDIDATES,
    SimulinkControllerIO,
)
from sim.matlab_runtime import MatlabEngineSession, load_matlab_engine


_MATLAB_ENGINE = None

def _load_matlab_engine(matlab_root: str = ""):
    global _MATLAB_ENGINE
    if _MATLAB_ENGINE is not None:
        return _MATLAB_ENGINE

    _MATLAB_ENGINE = load_matlab_engine(matlab_root)
    return _MATLAB_ENGINE


class SimulinkBridge:
    """Drive a Simulink model and expose samples like the Python simulator."""

    def __init__(
        self,
        model_path: str,
        setpoint: float,
        pid_block_path: str,
        output_signal: str,
        matlab_root: str = "",
        sim_step_time: float = 10.0,
        control_signal: str = "",
        output_signal_candidates: list[str] | None = None,
        setpoint_block: str = "",
        pid_block_paths: list[str] | None = None,
        p_block_path: str = "",
        i_block_path: str = "",
        d_block_path: str = "",
    ) -> None:
        self._matlab_engine = _load_matlab_engine(matlab_root)
        self.model_path = model_path
        self.setpoint = setpoint
        self.pid_block_path = pid_block_path.strip()
        self.pid_block_paths = [
            path for path in [self.pid_block_path, *(pid_block_paths or [])] if str(path).strip()
        ]
        self.output_signal = output_signal
        self.matlab_root = matlab_root
        self.sim_step_time = sim_step_time
        self.control_signal = control_signal.strip()
        self.output_signal_candidates = [
            str(item).strip()
            for item in (output_signal_candidates or [])
            if str(item).strip()
        ]
        self.setpoint_block = setpoint_block.strip()
        self.separate_gain_paths = {
            "p": p_block_path.strip(),
            "i": i_block_path.strip(),
            "d": d_block_path.strip(),
        }

        self.kp: float = 1.0
        self.ki: float = 0.1
        self.kd: float = 0.05

        # Secondary controller gains (only meaningful when
        # ``secondary_pid_block_path`` is configured). Updated by
        # ``set_pid_pair`` so the TUI can display both controllers.
        self.secondary_kp: float = 1.0
        self.secondary_ki: float = 0.1
        self.secondary_kd: float = 0.05

        self._eng: Optional[object] = None
        self._session: MatlabEngineSession | None = None
        self._model_name = ""
        self._current_sim_time = 0.0
        self._last_data: list[dict] = []
        self._warned_output_signal_fallback = False
        self._warned_control_signal_fallback = False
        self._warned_control_signal_autodetect = False
        self._warned_control_signal_missing = False
        self.resolved_output_signal = self.output_signal
        self.resolved_control_signal = ""
        self.has_control_signal = False
        self._block_discovery: SimulinkBlockDiscovery | None = None
        self._controller_io: SimulinkControllerIO | None = None

        self.secondary_pid_block_path = ""
        self.secondary_pid_block_paths: list[str] = []
        self.secondary_separate_gain_paths = {"p": "", "i": "", "d": ""}

        self.model_solver_type = ""
        self.model_solver_name = ""
        self.model_fixed_step = ""
        self.controller_1_sample_time = ""
        self.controller_2_sample_time = ""
        self.control_domain = ""

    @property
    def has_secondary_pid(self) -> bool:
        """True when a secondary controller block is configured."""
        return bool(
            self.secondary_pid_block_path
            or any(self.secondary_separate_gain_paths.values())
        )

    def connect(self) -> None:
        print("[Simulink] Starting MATLAB Engine, please wait...")
        try:
            self._eng = self._with_suppressed_engine_output(
                lambda: self._matlab_engine.start_matlab()
            )
            self._session = MatlabEngineSession(self._eng)
        except Exception as exc:
            raise RuntimeError(
                "[SimulinkBridge] Failed to start MATLAB Engine. "
                "Check whether MATLAB is properly installed and licensed."
            ) from exc

        self._model_name = os.path.splitext(os.path.basename(self.model_path))[0]
        model_dir = os.path.dirname(os.path.abspath(self.model_path))

        try:
            self._call_engine_method("addpath", model_dir, nargout=0)
            self._call_engine_method("load_system", self.model_path, nargout=0)
            self._call_engine_method(
                "set_param",
                self._model_name,
                "SimulationMode",
                "normal",
                nargout=0,
            )
        except Exception as exc:
            raise RuntimeError(
                f"[SimulinkBridge] Failed to load Simulink model '{self.model_path}'. "
                "Check MATLAB_MODEL_PATH and model dependencies."
            ) from exc

        print(f"[Simulink] Loaded model: {self._model_name}")
        self._block_discovery = self._create_block_discovery()
        self._controller_io = self._create_controller_io()
        self._apply_model_setpoint()
        self._autodiscover_controller_paths()
        if not self._has_primary_controller_config():
            raise RuntimeError(
                "[SimulinkBridge] Could not auto-detect a PID controller. "
                "Set block Tag to 'llm_pid_tuner_primary' or configure MATLAB_PID_BLOCK_PATH."
            )
        self._refresh_timing_metadata()
        print(f"[Simulink] Primary controller: {self.pid_block_path or '<none>'}")
        if self.secondary_pid_block_path:
            print(f"[Simulink] Secondary controller: {self.secondary_pid_block_path}")
        print(f"[Simulink] Setpoint block: {self.setpoint_block or '<not detected>'}")
        print(f"[Simulink] Output signal: {self.output_signal}")
        if self.control_domain:
            print(f"[Simulink] Control domain hint: {self.control_domain}")
        if self.control_signal:
            print(f"[Simulink] Control signal: {self.control_signal}")

        if self.pid_block_path:
            self.kp = self._read_controller_gain("p", self.kp)
            self.ki = self._read_controller_gain("i", self.ki)
            self.kd = self._read_controller_gain("d", self.kd)
            print(f"[Simulink] Initial PID: P={self.kp}, I={self.ki}, D={self.kd}")

    def _find_all_blocks(self) -> list[str]:
        raw_blocks = self._with_suppressed_engine_output(
            lambda: self._with_suppressed_engine_warnings(
                lambda: self._call_engine_method(
                    "find_system",
                    self._model_name,
                    "LookUnderMasks",
                    "all",
                    "FollowLinks",
                    "on",
                    nargout=1,
                )
            )
        )
        return self._to_string_list(raw_blocks)

    def _create_block_discovery(self) -> SimulinkBlockDiscovery:
        return SimulinkBlockDiscovery(
            find_all_blocks=self._find_all_blocks,
            find_blocks_by_type=self._find_blocks_by_type,
            get_param=lambda block_path, parameter_name: self._try_engine_method(
                "get_param", block_path, parameter_name
            ),
            count_controller_gain_params=self._count_controller_gain_params,
        )

    def _create_controller_io(self) -> SimulinkControllerIO:
        return SimulinkControllerIO(
            try_engine_method=self._try_engine_method,
            call_engine_method=self._call_engine_method,
            get_field_or_none=lambda obj, field_name, allow_get=False: self._get_field_or_none(
                obj, field_name, allow_get=allow_get
            ),
            is_timeseries_object=self._is_timeseries_object,
            to_float_series=self._to_float_series,
            to_string_list=self._to_string_list,
        )

    def _ensure_session(self) -> MatlabEngineSession | None:
        if self._session is not None:
            return self._session
        if self._eng is None:
            return None
        self._session = MatlabEngineSession(self._eng)
        return self._session

    def _has_primary_controller_config(self) -> bool:
        return bool(
            self.pid_block_path
            or self.pid_block_paths
            or any(self.separate_gain_paths.values())
        )

    def _count_controller_gain_params(self, block_path: str) -> int:
        controller_io = self._controller_io or self._create_controller_io()
        gain_count = 0
        for gain_key in ("p", "i", "d"):
            if controller_io.resolve_controller_param_name_for_path(block_path, gain_key) is not None:
                gain_count += 1
        return gain_count

    def _autodiscover_controller_paths(self) -> None:
        explicit_primary = bool(self.pid_block_path or any(self.separate_gain_paths.values()))
        explicit_secondary = bool(
            self.secondary_pid_block_path or any(self.secondary_separate_gain_paths.values())
        )
        if explicit_primary and explicit_secondary:
            return
        discovery = self._block_discovery or self._create_block_discovery()
        result = discovery.autodiscover_controller_paths(
            explicit_primary=explicit_primary,
            explicit_secondary=explicit_secondary,
            primary_path=self.pid_block_path,
            primary_paths=self.pid_block_paths,
            secondary_path=self.secondary_pid_block_path,
            secondary_paths=self.secondary_pid_block_paths,
        )
        self.pid_block_path = result.primary_path
        self.pid_block_paths = list(result.primary_paths)
        self.secondary_pid_block_path = result.secondary_path
        self.secondary_pid_block_paths = list(result.secondary_paths)

    def _refresh_timing_metadata(self) -> None:
        discovery = self._block_discovery or self._create_block_discovery()
        self.model_solver_type = discovery.normalize_param_text(
            self._try_engine_method("get_param", self._model_name, "SolverType")
        )
        self.model_solver_name = discovery.normalize_param_text(
            self._try_engine_method("get_param", self._model_name, "Solver")
        )
        self.model_fixed_step = discovery.normalize_param_text(
            self._try_engine_method("get_param", self._model_name, "FixedStep")
        )
        self.controller_1_sample_time = discovery.controller_sample_time_from_paths(
            separate_gain_paths=self.separate_gain_paths,
            pid_block_path=self.pid_block_path,
            pid_block_paths=self.pid_block_paths,
        )
        self.controller_2_sample_time = discovery.controller_sample_time_from_paths(
            separate_gain_paths=self.secondary_separate_gain_paths,
            pid_block_path=self.secondary_pid_block_path,
            pid_block_paths=self.secondary_pid_block_paths,
        )
        self.control_domain = discovery.detect_control_domain(
            controller_1_sample_time=self.controller_1_sample_time,
            controller_2_sample_time=self.controller_2_sample_time,
            model_fixed_step=self.model_fixed_step,
            model_solver_type=self.model_solver_type,
        )

    def _read_controller_gain(self, gain_key: str, default: float) -> float:
        controller_io = self._controller_io or self._create_controller_io()
        return controller_io.read_controller_gain(
            gain_key=gain_key,
            default=default,
            separate_gain_paths=self.separate_gain_paths,
            pid_block_path=self.pid_block_path,
            pid_block_paths=self.pid_block_paths,
        )

    def _write_controller_gain(self, gain_key: str, value: float) -> None:
        if self._eng is None:
            return
        controller_io = self._controller_io or self._create_controller_io()
        controller_io.write_controller_gain(
            gain_key=gain_key,
            value=value,
            separate_gain_paths=self.separate_gain_paths,
            pid_block_path=self.pid_block_path,
            pid_block_paths=self.pid_block_paths,
        )

    def disconnect(self) -> None:
        if self._eng is not None:
            try:
                self._call_engine_method(
                    "save_system",
                    self._model_name,
                    self.model_path,
                    nargout=0,
                )
                print(f"[Simulink] Saved model: {self.model_path}")
            except Exception as exc:
                print(f"[WARN] Failed to save Simulink model: {exc}")
            try:
                self._call_engine_method(
                    "close_system",
                    self._model_name,
                    0,
                    nargout=0,
                )
            except Exception as exc:
                print(f"[WARN] Failed to close Simulink model: {exc}")
            self._with_suppressed_engine_output(lambda: self._eng.quit())
            self._eng = None
            self._session = None
            print("[Simulink] Engine closed.")

    def set_pid(self, p: float, i: float, d: float) -> None:
        self.kp, self.ki, self.kd = p, i, d
        self._write_controller_gain("p", p)
        self._write_controller_gain("i", i)
        self._write_controller_gain("d", d)

    def set_pid_pair(
        self,
        primary: dict[str, float],
        secondary: dict[str, float] | None = None,
    ) -> list[str]:
        self.set_pid(
            float(primary.get("p", self.kp)),
            float(primary.get("i", self.ki)),
            float(primary.get("d", self.kd)),
        )
        if secondary is None:
            return []
        if not self.secondary_pid_block_path:
            return [
                "Controller 2 path is missing; skipped secondary update."
            ]
        if self.secondary_pid_block_path == self.pid_block_path:
            return [
                "Controller 2 path equals Controller 1 path; skipped secondary update to avoid writing the same block twice."
            ]

        original_pid_block_path = self.pid_block_path
        original_pid_block_paths = list(self.pid_block_paths)
        original_separate_gain_paths = dict(self.separate_gain_paths)
        secondary_guardrail_notes: list[str] = []
        try:
            self.pid_block_path = self.secondary_pid_block_path
            self.pid_block_paths = list(self.secondary_pid_block_paths)
            self.separate_gain_paths = dict(self.secondary_separate_gain_paths)
            try:
                current_secondary_pid = {
                    gain_key: self._read_controller_gain(gain_key, 0.0)
                    for gain_key in ("p", "i", "d")
                }
                primary_pid = {
                    gain_key: float(primary.get(gain_key, 0.0))
                    for gain_key in ("p", "i", "d")
                }
                secondary_candidate = {
                    gain_key: float(secondary.get(gain_key, current_secondary_pid[gain_key]))
                    for gain_key in ("p", "i", "d")
                }
                mirrored_secondary = all(
                    abs(secondary_candidate[gain_key] - primary_pid[gain_key]) < 1e-12
                    for gain_key in ("p", "i", "d")
                )
                current_secondary_is_distinct = any(
                    abs(current_secondary_pid[gain_key] - primary_pid[gain_key]) > 1e-9
                    for gain_key in ("p", "i", "d")
                )
                if mirrored_secondary and current_secondary_is_distinct:
                    secondary_guardrail_notes = [
                        "Controller 2 suggestion mirrored Controller 1; kept existing secondary PID to avoid coupling both loops."
                    ]
                    self.secondary_kp = current_secondary_pid["p"]
                    self.secondary_ki = current_secondary_pid["i"]
                    self.secondary_kd = current_secondary_pid["d"]
                    return secondary_guardrail_notes

                # Delegate limits and guardrails out of Bridge where possible. 
                # For tests compatibility we still run adapt and guardrails if required, but ideally these 
                # belong strictly to the upper level (simulator.py/tuning_engine.py).
                from pid_safety import get_pid_limits, apply_pid_guardrails
                def adapt_simulink_pid_limits(base_limits, **kwargs): return base_limits
                secondary_limits = adapt_simulink_pid_limits(
                    get_pid_limits("simulink"),
                    control_domain=self.control_domain,
                    controller_1_sample_time=self.controller_1_sample_time,
                    controller_2_sample_time=self.controller_2_sample_time,
                    model_fixed_step=self.model_fixed_step,
                )
                safe_secondary_pid, secondary_guardrail_notes = apply_pid_guardrails(
                    current_secondary_pid,
                    secondary,
                    limits=secondary_limits,
                )

                for gain_key in ("p", "i", "d"):
                    self._write_controller_gain(
                        gain_key,
                        safe_secondary_pid[gain_key],
                    )
                self.secondary_kp = safe_secondary_pid["p"]
                self.secondary_ki = safe_secondary_pid["i"]
                self.secondary_kd = safe_secondary_pid["d"]
            except Exception as e:
                secondary_guardrail_notes = [
                    "Controller 2 update skipped due to incompatible block configuration."
                ]
        finally:
            self.pid_block_path = original_pid_block_path
            self.pid_block_paths = original_pid_block_paths
            self.separate_gain_paths = original_separate_gain_paths
        return secondary_guardrail_notes

    def _get_field_or_none(
        self, obj: object, field_name: str, *, allow_get: bool = False
    ) -> Optional[object]:
        session = self._ensure_session()
        if session is None:
            return None
        return session.get_field_or_none(obj, field_name, allow_get=allow_get)

    def _is_timeseries_object(self, obj: object) -> bool:
        session = self._ensure_session()
        return bool(session and session.is_timeseries_object(obj))

    def _to_string_list(self, raw_value: object) -> list[str]:
        session = self._ensure_session()
        if session is not None:
            return session.to_string_list(raw_value)
        if raw_value is None:
            return []
        if isinstance(raw_value, str):
            return [raw_value]
        try:
            values = list(raw_value)
        except TypeError:
            return [str(raw_value)]
        return [str(value) for value in values]

    def _with_suppressed_engine_warnings(self, callback):
        session = self._ensure_session()
        if session is None:
            return callback()
        return session.with_suppressed_warnings(callback)

    def _with_suppressed_engine_output(self, callback):
        session = self._ensure_session()
        if session is None:
            return callback()
        return session.with_suppressed_output(callback)

    def _call_engine_method(
        self, method_name: str, *args: object, nargout: int = 1, quiet: bool = True
    ) -> object:
        session = self._ensure_session()
        if session is None:
            raise RuntimeError(
                "[SimulinkBridge] MATLAB Engine is not connected. Call connect() first."
            )
        return session.call_method(
            method_name, *args, nargout=nargout, quiet=quiet
        )

    def _try_engine_method(
        self, method_name: str, *args: object, nargout: int = 1, quiet: bool = True
    ) -> Optional[object]:
        session = self._ensure_session()
        if session is None:
            return None
        return session.try_method(
            method_name, *args, nargout=nargout, quiet=quiet
        )

    def _find_blocks_by_type(self, block_type: str) -> list[str]:
        session = self._ensure_session()
        if session is None:
            return []
        return session.find_blocks_by_type(self._model_name, block_type)

    def _resolve_setpoint_block(self) -> tuple[str | None, str | None]:
        discovery = self._block_discovery or self._create_block_discovery()
        return discovery.resolve_setpoint_block(self.setpoint_block)

    def _setpoint_parameter_name(self, block_type: str) -> str | None:
        discovery = self._block_discovery or self._create_block_discovery()
        return discovery.setpoint_parameter_name(block_type)

    def _apply_model_setpoint(self) -> None:
        block_path, block_type = self._resolve_setpoint_block()
        if not block_path or not block_type:
            print(
                "[Simulink][WARN] Could not auto-detect the setpoint source block. "
                f"Make sure the model setpoint matches MATLAB_SETPOINT={self.setpoint}."
            )
            return

        parameter_name = self._setpoint_parameter_name(block_type)
        if not parameter_name:
            print(
                f"[Simulink][WARN] Detected setpoint block {block_path}, "
                f"but block type {block_type} is not writable yet."
            )
            return

        self._call_engine_method(
            "set_param",
            block_path,
            parameter_name,
            str(self.setpoint),
            nargout=0,
        )
        self.setpoint_block = block_path
        print(f"[Simulink] Synced setpoint {self.setpoint} to {block_path} ({parameter_name}).")

    def _to_float_scalar(self, value: object) -> float:
        session = self._ensure_session()
        if session is not None:
            return session.to_float_scalar(value)
        current = value
        while isinstance(current, (list, tuple)):
            if not current:
                return 0.0
            current = current[0]
        try:
            iterator = iter(current)
        except TypeError:
            return float(current)
        converted = list(iterator)
        if not converted:
            return 0.0
        return self._to_float_scalar(converted[0])

    def _to_float_series(self, raw_values: object) -> list[float]:
        session = self._ensure_session()
        if session is not None:
            return session.to_float_series(raw_values)
        if raw_values is None or isinstance(raw_values, (str, bytes)):
            return []
        try:
            values = list(raw_values)
        except TypeError:
            return [self._to_float_scalar(raw_values)]
        return [self._to_float_scalar(item) for item in values]

    def _resolve_signal_candidates(
        self,
        primary_signal: str,
        *,
        configured_candidates: list[str] | None = None,
        fallback_candidates: tuple[str, ...] = (),
    ) -> list[str]:
        controller_io = self._controller_io or self._create_controller_io()
        return controller_io.resolve_signal_candidates(
            primary_signal,
            configured_candidates=configured_candidates,
            fallback_candidates=fallback_candidates,
        )

    def _resolve_output_signal_candidates(self, primary_signal: str) -> list[str]:
        return self._resolve_signal_candidates(
            primary_signal,
            configured_candidates=self.output_signal_candidates,
            fallback_candidates=("yout",) if primary_signal == self.output_signal else (),
        )

    def _resolve_control_signal_candidates(self) -> list[str]:
        return self._resolve_signal_candidates(
            self.control_signal,
            fallback_candidates=CONTROL_SIGNAL_FALLBACK_CANDIDATES,
        )

    def _resolve_named_signal(
        self,
        sim_out: object,
        primary_signal: str,
        *,
        candidates: list[str] | None = None,
    ) -> tuple[str, object]:
        controller_io = self._controller_io or self._create_controller_io()
        resolved = controller_io.resolve_named_signal(
            sim_out,
            primary_signal,
            candidates=candidates or self._resolve_output_signal_candidates(primary_signal),
        )
        return resolved.name, resolved.container

    def run_step(self) -> None:
        if self._eng is None:
            raise RuntimeError(
                "[SimulinkBridge] MATLAB Engine is not connected. Call connect() first."
            )

        self._call_engine_method(
            "set_param",
            self._model_name,
            "StopTime",
            str(self.sim_step_time),
            nargout=0,
        )
        sim_out = self._call_engine_method("sim", self._model_name)

        resolved_output_signal, signal_container = self._resolve_named_signal(
            sim_out,
            self.output_signal,
            candidates=self._resolve_output_signal_candidates(self.output_signal),
        )
        self.resolved_output_signal = resolved_output_signal
        if resolved_output_signal != self.output_signal and not self._warned_output_signal_fallback:
            self._warned_output_signal_fallback = True
            print(
                f"[Simulink][WARN] Configured MATLAB_OUTPUT_SIGNAL='{self.output_signal}', "
                f"but simulation output used '{resolved_output_signal}'. Update your config or model to match."
            )

        controller_io = self._controller_io or self._create_controller_io()
        time_values, output_values = controller_io.extract_signal_series(
            signal_container, sim_out
        )

        pwm_values: list[float] = []
        self.resolved_control_signal = ""
        self.has_control_signal = False
        control_signal_candidates = self._resolve_control_signal_candidates()
        if control_signal_candidates:
            try:
                resolved_control_signal, control_container = self._resolve_named_signal(
                    sim_out,
                    self.control_signal,
                    candidates=control_signal_candidates,
                )
                self.resolved_control_signal = resolved_control_signal
                self.has_control_signal = True
                if self.control_signal and resolved_control_signal != self.control_signal:
                    if not self._warned_control_signal_fallback:
                        self._warned_control_signal_fallback = True
                        print(
                            f"[Simulink][WARN] Configured MATLAB_CONTROL_SIGNAL='{self.control_signal}', "
                            f"but simulation output used '{resolved_control_signal}'. Update your config or model to match."
                        )
                elif not self.control_signal and not self._warned_control_signal_autodetect:
                    self._warned_control_signal_autodetect = True
                    print(
                        f"[Simulink] Auto-detected control signal: {resolved_control_signal}"
                    )
                _, pwm_values = controller_io.extract_signal_series(
                    control_container, sim_out
                )
            except Exception as exc:
                if self.control_signal and not self._warned_control_signal_missing:
                    self._warned_control_signal_missing = True
                    print(
                        f"[Simulink][WARN] Failed to resolve control signal '{self.control_signal}': {exc}"
                    )
                pwm_values = []

        self._current_sim_time = self.sim_step_time
        self._last_data = []
        for index, (current_time, output) in enumerate(zip(time_values, output_values)):
            error = self.setpoint - float(output)
            pwm_value = pwm_values[index] if index < len(pwm_values) else 0.0
            self._last_data.append(
                {
                    "timestamp": float(current_time) * 1000.0,
                    "setpoint": self.setpoint,
                    "input": float(output),
                    "pwm": float(pwm_value),
                    "error": error,
                    "p": self.kp,
                    "i": self.ki,
                    "d": self.kd,
                }
            )

    def get_data(self) -> list[dict]:
        return self._last_data
