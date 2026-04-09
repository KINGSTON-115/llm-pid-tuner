#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Simulink bridge backed by the local MATLAB Engine runtime."""

from __future__ import annotations

import contextlib
import importlib
import io
import os
import sys
from typing import Callable, Optional


_MATLAB_ENGINE = None
_DLL_DIRECTORY_HANDLES: list[object] = []
_MATLAB_RUNTIME_DIAGNOSTICS: dict[str, object] = {}


def _runtime_layout() -> tuple[str, str]:
    if sys.platform == "win32":
        return "win64", "PATH"
    if sys.platform.startswith("linux"):
        return "glnxa64", "LD_LIBRARY_PATH"
    if sys.platform == "darwin":
        machine = os.uname().machine.lower() if hasattr(os, "uname") else ""
        return (
            "maca64" if machine in {"arm64", "aarch64"} else "maci64",
            "DYLD_LIBRARY_PATH",
        )
    raise ImportError(f"[SimulinkBridge] Unsupported platform: {sys.platform}")


def _prepend_unique_path(path_list: list[str], new_path: str) -> None:
    normalized = os.path.normcase(os.path.normpath(new_path))
    for existing in path_list:
        if os.path.normcase(os.path.normpath(existing)) == normalized:
            return
    path_list.insert(0, new_path)


def _prepend_unique_env_path(var_name: str, new_path: str) -> None:
    current = os.environ.get(var_name, "")
    normalized = os.path.normcase(os.path.normpath(new_path))
    if current:
        parts = current.split(os.pathsep)
        if any(
            os.path.normcase(os.path.normpath(part)) == normalized
            for part in parts
            if part
        ):
            return
        os.environ[var_name] = new_path + os.pathsep + current
        return

    os.environ[var_name] = new_path


def _register_dll_directory(path: str) -> str | None:
    add_dll_directory = getattr(os, "add_dll_directory", None)
    if not callable(add_dll_directory):
        return "os.add_dll_directory is unavailable"
    try:
        handle = add_dll_directory(path)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    _DLL_DIRECTORY_HANDLES.append(handle)
    return None


def _purge_stale_matlab_modules(matlab_root: str) -> None:
    root = matlab_root.strip()
    if not root:
        return

    dist_dir = os.path.normcase(
        os.path.abspath(os.path.join(root, "extern", "engines", "python", "dist"))
    )
    expected_prefix = dist_dir + os.sep
    stale_modules: list[str] = []

    for module_name, module in list(sys.modules.items()):
        if module_name != "matlab" and not module_name.startswith("matlab."):
            continue
        module_file = getattr(module, "__file__", "")
        if not module_file:
            stale_modules.append(module_name)
            continue
        module_path = os.path.normcase(os.path.abspath(module_file))
        if module_path == dist_dir or module_path.startswith(expected_prefix):
            continue
        stale_modules.append(module_name)

    for module_name in stale_modules:
        sys.modules.pop(module_name, None)

    if stale_modules:
        importlib.invalidate_caches()


def _prepare_matlab_root(matlab_root: str) -> None:
    global _MATLAB_RUNTIME_DIAGNOSTICS
    root = matlab_root.strip()
    if not root:
        return

    arch, path_var = _runtime_layout()
    root_path = os.path.abspath(root)
    dist_dir = os.path.join(root_path, "extern", "engines", "python", "dist")
    engine_dir = os.path.join(dist_dir, "matlab", "engine", arch)
    extern_bin_dir = os.path.join(root_path, "extern", "bin", arch)
    bin_dir = os.path.join(root_path, "bin", arch)
    runtime_dir = os.path.join(root_path, "runtime", arch)
    sys_os_dir = os.path.join(root_path, "sys", "os", arch)
    bin_root_dir = os.path.join(root_path, "bin")
    matlab_package_dir = os.path.join(dist_dir, "matlab")

    required_paths = {
        "MATLAB_ROOT": root_path,
        "MATLAB engine dist": dist_dir,
        "MATLAB engine binary": engine_dir,
        "MATLAB extern bin": extern_bin_dir,
        "MATLAB bin": bin_dir,
    }
    missing = [
        name for name, path in required_paths.items() if not os.path.exists(path)
    ]
    if missing:
        raise ImportError(
            "[SimulinkBridge] Invalid MATLAB_ROOT. Missing: "
            + ", ".join(missing)
            + f". Current MATLAB_ROOT='{root_path}'."
        )

    os.environ["MWE_INSTALL"] = root_path
    os.environ["MATLAB_ROOT"] = root_path
    _prepend_unique_path(sys.path, dist_dir)
    _prepend_unique_path(sys.path, engine_dir)
    _prepend_unique_path(sys.path, extern_bin_dir)
    dll_search_dirs = (
        dist_dir,
        matlab_package_dir,
        engine_dir,
        bin_root_dir,
        runtime_dir,
        sys_os_dir,
        bin_dir,
        extern_bin_dir,
    )
    dll_registration_errors: list[str] = []
    configured_dll_dirs: list[str] = []
    for dll_dir in dll_search_dirs:
        if not os.path.exists(dll_dir):
            continue
        _prepend_unique_env_path(path_var, dll_dir)
        configured_dll_dirs.append(dll_dir)
        registration_error = _register_dll_directory(dll_dir)
        if registration_error:
            dll_registration_errors.append(f"{dll_dir} -> {registration_error}")

    _MATLAB_RUNTIME_DIAGNOSTICS = {
        "root": root_path,
        "sys_path_entries": [extern_bin_dir, engine_dir, dist_dir],
        "dll_search_dirs": configured_dll_dirs,
        "dll_registration_errors": dll_registration_errors,
        "path_variable": path_var,
    }


def _format_runtime_diagnostics() -> str:
    if not _MATLAB_RUNTIME_DIAGNOSTICS:
        return " Runtime preparation did not record diagnostics."

    parts = []
    sys_path_entries = _MATLAB_RUNTIME_DIAGNOSTICS.get("sys_path_entries", [])
    dll_search_dirs = _MATLAB_RUNTIME_DIAGNOSTICS.get("dll_search_dirs", [])
    registration_errors = _MATLAB_RUNTIME_DIAGNOSTICS.get(
        "dll_registration_errors", []
    )
    path_variable = _MATLAB_RUNTIME_DIAGNOSTICS.get("path_variable", "")

    if sys_path_entries:
        parts.append(
            " sys.path prepared: "
            + ", ".join(str(path) for path in sys_path_entries)
        )
    if dll_search_dirs:
        parts.append(
            " DLL search dirs prepared: "
            + ", ".join(str(path) for path in dll_search_dirs)
        )
    if path_variable:
        parts.append(f" DLL env var: {path_variable}.")
    if registration_errors:
        parts.append(
            " add_dll_directory failures: "
            + "; ".join(str(item) for item in registration_errors)
        )

    return "".join(parts)


def _load_matlab_engine(matlab_root: str = ""):
    global _MATLAB_ENGINE
    if _MATLAB_ENGINE is not None:
        return _MATLAB_ENGINE

    _prepare_matlab_root(matlab_root)
    _purge_stale_matlab_modules(matlab_root)

    try:
        matlab_engine = importlib.import_module("matlab.engine")
    except Exception as exc:
        if matlab_root.strip():
            raise ImportError(
                "[SimulinkBridge] Failed to initialize MATLAB Engine with the configured "
                f"MATLAB_ROOT='{matlab_root.strip()}'."
                + _format_runtime_diagnostics()
            ) from exc
        raise ImportError(
            "[SimulinkBridge] Failed to initialize MATLAB Engine. "
            "Set MATLAB_ROOT in config.json to your local MATLAB installation directory."
        ) from exc

    _MATLAB_ENGINE = matlab_engine
    return matlab_engine


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
    ) -> None:
        self._matlab_engine = _load_matlab_engine(matlab_root)
        self.model_path = model_path
        self.setpoint = setpoint
        self.pid_block_path = pid_block_path
        self.output_signal = output_signal
        self.matlab_root = matlab_root
        self.sim_step_time = sim_step_time

        self.kp: float = 1.0
        self.ki: float = 0.1
        self.kd: float = 0.05

        self._eng: Optional[object] = None
        self._model_name = ""
        self._current_sim_time = 0.0
        self._last_data: list[dict] = []
        self._warned_output_signal_fallback = False

    def connect(self) -> None:
        print("[Simulink] Starting MATLAB Engine, please wait...")
        try:
            self._eng = self._with_suppressed_engine_output(
                lambda: self._matlab_engine.start_matlab()
            )
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
        except Exception as exc:
            raise RuntimeError(
                f"[SimulinkBridge] Failed to load Simulink model '{self.model_path}'. "
                "Check MATLAB_MODEL_PATH and model dependencies."
            ) from exc
        print(f"[Simulink] Loaded model: {self._model_name}")
        self._apply_model_setpoint()

        self._call_engine_method(
            "set_param",
            self._model_name,
            "SimulationMode",
            "normal",
            nargout=0,
        )
        self._current_sim_time = 0.0

        try:
            self.kp = float(self._call_engine_method("get_param", self.pid_block_path, "P"))
            self.ki = float(self._call_engine_method("get_param", self.pid_block_path, "I"))
            self.kd = float(self._call_engine_method("get_param", self.pid_block_path, "D"))
            print(
                f"[Simulink] Initial PID: P={self.kp}, I={self.ki}, D={self.kd}"
            )
        except Exception:
            print(
                "[Simulink] Could not read initial PID from the block. "
                f"Using defaults P={self.kp}, I={self.ki}, D={self.kd}."
            )

    def disconnect(self) -> None:
        if self._eng is not None:
            try:
                self._eng.save_system(self._model_name, self.model_path, nargout=0)
                print(f"[Simulink] Saved model: {self.model_path}")
            except Exception as exc:
                print(f"[WARN] Failed to save Simulink model: {exc}")
            try:
                self._eng.close_system(self._model_name, 0, nargout=0)
            except Exception as exc:
                print(f"[WARN] Failed to close Simulink model: {exc}")
            self._eng.quit()
            self._eng = None
            print("[Simulink] Engine closed.")

    def set_pid(self, p: float, i: float, d: float) -> None:
        self.kp, self.ki, self.kd = p, i, d
        if self._eng is not None:
            for param_name, value in (("P", p), ("I", i), ("D", d)):
                self._call_engine_method(
                    "set_param",
                    self.pid_block_path,
                    param_name,
                    str(value),
                    nargout=0,
                )

    def _get_field_or_none(
        self, obj: object, field_name: str, *, allow_get: bool = False
    ) -> Optional[object]:
        if allow_get:
            # Prefer .get() for SimulationOutput-like containers because failed getfield()
            # calls can print noisy MATLAB diagnostics directly to the console.
            resolved = self._try_engine_method("get", obj, field_name)
            if resolved is not None:
                return resolved
        return self._try_engine_method("getfield", obj, field_name)

    def _is_timeseries_object(self, obj: object) -> bool:
        return bool(self._try_engine_method("isa", obj, "timeseries"))

    def _to_string_list(self, raw_value: object) -> list[str]:
        if raw_value is None:
            return []
        if isinstance(raw_value, str):
            return [raw_value]
        try:
            values = list(raw_value)  # type: ignore[arg-type]
        except TypeError:
            return [str(raw_value)]
        return [str(value) for value in values]

    def _with_suppressed_engine_warnings(self, callback: Callable[[], object]):
        if self._eng is None or not hasattr(self._eng, "eval"):
            return callback()

        warnings_disabled = False
        try:
            self._eng.eval("warning('off','all');", nargout=0)  # type: ignore[union-attr]
            warnings_disabled = True
        except Exception:
            return callback()

        try:
            return callback()
        finally:
            if warnings_disabled:
                try:
                    self._eng.eval("warning('on','all');", nargout=0)  # type: ignore[union-attr]
                except Exception:
                    pass

    def _with_suppressed_engine_output(self, callback: Callable[[], object]):
        if self._eng is None:
            return callback()

        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                return callback()

    def _call_engine_method(
        self, method_name: str, *args: object, nargout: int = 1, quiet: bool = True
    ) -> object:
        if self._eng is None:
            raise RuntimeError(
                "[SimulinkBridge] MATLAB Engine is not connected. Call connect() first."
            )
        method = getattr(self._eng, method_name)
        if quiet:
            return self._with_suppressed_engine_output(
                lambda: method(*args, nargout=nargout)
            )
        return method(*args, nargout=nargout)

    def _try_engine_method(
        self, method_name: str, *args: object, nargout: int = 1, quiet: bool = True
    ) -> Optional[object]:
        try:
            return self._call_engine_method(
                method_name, *args, nargout=nargout, quiet=quiet
            )
        except Exception:
            return None

    def _find_blocks_by_type(self, block_type: str) -> list[str]:
        def _call_find_system():
            return self._call_engine_method(
                "find_system",
                self._model_name,
                "LookUnderMasks",
                "all",
                "FollowLinks",
                "on",
                "BlockType",
                block_type,
                nargout=1,
                quiet=False,
            )

        raw_blocks = self._with_suppressed_engine_output(
            lambda: self._with_suppressed_engine_warnings(_call_find_system)
        )
        return self._to_string_list(raw_blocks)

    def _resolve_setpoint_block(self) -> tuple[str | None, str | None]:
        keywords = ("setpoint", "reference", "ref", "step", "目标", "给定")
        candidates: list[tuple[int, str, str]] = []

        for block_type in ("Step", "Constant"):
            for block_path in self._find_blocks_by_type(block_type):
                score = 0
                lowered = block_path.lower()
                if any(keyword in lowered for keyword in keywords):
                    score += 10
                if block_path.rsplit("/", 1)[-1] in {"Step", "Setpoint", "Reference"}:
                    score += 5
                candidates.append((score, block_path, block_type))

        if not candidates:
            return None, None

        candidates.sort(key=lambda item: (-item[0], item[1]))
        best_score, best_path, best_type = candidates[0]

        if len(candidates) > 1:
            second_score = candidates[1][0]
            if best_score == second_score and best_score == 0:
                return None, None

        return best_path, best_type

    def _setpoint_parameter_name(self, block_type: str) -> str | None:
        if block_type == "Step":
            return "After"
        if block_type == "Constant":
            return "Value"
        return None

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

        try:
            self._call_engine_method(
                "set_param",
                block_path,
                parameter_name,
                str(self.setpoint),
                nargout=0,
            )
        except Exception as exc:
            raise RuntimeError(
                f"[SimulinkBridge] Failed to sync MATLAB_SETPOINT={self.setpoint} "
                f"to block '{block_path}'."
            ) from exc
        print(
            f"[Simulink] Synced setpoint {self.setpoint} to {block_path} "
            f"({parameter_name})."
        )

    def _to_float_scalar(self, value: object) -> float:
        current = value
        while isinstance(current, (list, tuple)):
            if not current:
                return 0.0
            current = current[0]
        try:
            iterator = iter(current)  # type: ignore[arg-type]
        except TypeError:
            return float(current)  # type: ignore[arg-type]
        converted = list(iterator)
        if not converted:
            return 0.0
        return self._to_float_scalar(converted[0])

    def _to_float_series(self, raw_values: object) -> list[float]:
        if raw_values is None:
            return []
        if isinstance(raw_values, (str, bytes)):
            return []
        try:
            values = list(raw_values)  # type: ignore[arg-type]
        except TypeError:
            return [self._to_float_scalar(raw_values)]
        return [self._to_float_scalar(item) for item in values]

    def _resolve_signal_container(self, sim_out: object) -> object:
        # Try several candidates: user-specified, then 'yout' as a fallback
        candidates = [self.output_signal]
        if self.output_signal != "yout":
            candidates.append("yout")

        for signal_name in candidates:
            # 1. Direct signal (simOut.y_out or simOut.get('y_out'))
            direct_signal = self._get_field_or_none(
                sim_out, signal_name, allow_get=True
            )
            if direct_signal is not None:
                if signal_name != self.output_signal:
                    self._warn_output_signal_fallback(signal_name)
                return direct_signal

            # 2. Nested in 'out' (simOut.out.y_out)
            out_container = self._get_field_or_none(sim_out, "out", allow_get=True)
            if out_container is not None:
                nested_signal = self._get_field_or_none(out_container, signal_name)
                if nested_signal is not None:
                    if signal_name != self.output_signal:
                        self._warn_output_signal_fallback(signal_name)
                    return nested_signal

            # 3. In 'logsout' (simOut.logsout.get('y_out'))
            logsout = self._get_field_or_none(sim_out, "logsout", allow_get=True)
            if logsout is not None:
                nested_signal = self._try_engine_method("get", logsout, signal_name)
                if nested_signal is not None:
                    if signal_name != self.output_signal:
                        self._warn_output_signal_fallback(signal_name)
                    return nested_signal

        # If we reached here, we failed. Try to get available field names to help the user.
        available_fields = []
        try:
            # SimulationOutput properties
            field_names_raw = self._eng.fieldnames(sim_out, nargout=1)  # type: ignore[union-attr]
            available_fields = self._to_string_list(field_names_raw)
        except Exception:
            pass

        error_msg = (
            f"[SimulinkBridge] Could not find signal '{self.output_signal}' in the simulation output. "
            f"Tried simOut.<signal>, simOut.out.<signal>, simOut.logsout.<signal> etc."
        )
        if available_fields:
            error_msg += f" Available fields in simOut: {', '.join(available_fields)}"

        raise RuntimeError(error_msg)

    def _warn_output_signal_fallback(self, resolved_signal: str) -> None:
        if self._warned_output_signal_fallback:
            return
        self._warned_output_signal_fallback = True
        print(
            f"[Simulink][WARN] Configured MATLAB_OUTPUT_SIGNAL='{self.output_signal}', "
            f"but simulation output used '{resolved_signal}'. Update your config or model to match."
        )

    def _resolve_time_vector(self, sim_out: object) -> list[float]:
        for candidate in ("tout", "time", "Time"):
            raw_time = self._get_field_or_none(sim_out, candidate, allow_get=True)
            if raw_time is not None:
                values = self._to_float_series(raw_time)
                if values:
                    return values

        out_container = self._get_field_or_none(sim_out, "out", allow_get=True)
        if out_container is not None:
            for candidate in ("tout", "time", "Time"):
                raw_time = self._get_field_or_none(out_container, candidate)
                if raw_time is not None:
                    values = self._to_float_series(raw_time)
                    if values:
                        return values

        return []

    def run_step(self) -> None:
        if self._eng is None:
            raise RuntimeError(
                "[SimulinkBridge] MATLAB Engine is not connected. Call connect() first."
            )

        try:
            self._call_engine_method(
                "set_param",
                self._model_name,
                "StopTime",
                str(self.sim_step_time),
                nargout=0,
            )
            sim_out = self._call_engine_method("sim", self._model_name)
        except Exception as exc:
            raise RuntimeError(
                f"[SimulinkBridge] MATLAB simulation failed while running '{self._model_name}'. "
                "Check the model for compile/runtime errors in MATLAB."
            ) from exc

        try:
            signal_container = self._resolve_signal_container(sim_out)

            if self._is_timeseries_object(signal_container):
                raw_time = self._get_field_or_none(signal_container, "Time")
                raw_output = self._get_field_or_none(signal_container, "Data")
            else:
                raw_time = None
                raw_output = None

            if raw_time is not None and raw_output is not None:
                time_values = self._to_float_series(raw_time)
                output_values = self._to_float_series(raw_output)
            else:
                output_values = self._to_float_series(signal_container)
                time_values = self._resolve_time_vector(sim_out)
                if not time_values:
                    time_values = [float(index) for index in range(len(output_values))]
        except Exception as exc:
            raise RuntimeError(
                f"[SimulinkBridge] Failed to read output signal '{self.output_signal}': {exc}. "
                "Check MATLAB_OUTPUT_SIGNAL and the To Workspace block name."
            ) from exc

        self._current_sim_time = self.sim_step_time
        self._last_data = []
        for current_time, output in zip(time_values, output_values):
            error = self.setpoint - float(output)
            self._last_data.append(
                {
                    "timestamp": float(current_time) * 1000.0,
                    "setpoint": self.setpoint,
                    "input": float(output),
                    "pwm": 0.0,
                    "error": error,
                    "p": self.kp,
                    "i": self.ki,
                    "d": self.kd,
                }
            )

    def get_data(self) -> list[dict]:
        return self._last_data
