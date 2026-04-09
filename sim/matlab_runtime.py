from __future__ import annotations

import importlib
import contextlib
import io
import os
import sys


_DLL_DIRECTORY_HANDLES: list[object] = []


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


def purge_stale_matlab_modules(matlab_root: str) -> None:
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


def prepare_matlab_root(matlab_root: str) -> None:
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
    for dll_dir in dll_search_dirs:
        if not os.path.exists(dll_dir):
            continue
        _prepend_unique_env_path(path_var, dll_dir)
        _register_dll_directory(dll_dir)


def load_matlab_engine(matlab_root: str = ""):
    prepare_matlab_root(matlab_root)
    purge_stale_matlab_modules(matlab_root)

    try:
        return importlib.import_module("matlab.engine")
    except Exception as exc:
        if matlab_root.strip():
            raise ImportError(
                "[SimulinkBridge] Failed to initialize MATLAB Engine with the configured "
                f"MATLAB_ROOT='{matlab_root.strip()}'."
            ) from exc
        raise ImportError(
            "[SimulinkBridge] Failed to initialize MATLAB Engine. "
            "Set MATLAB_ROOT in config.json to your local MATLAB installation directory."
        ) from exc


class MatlabEngineSession:
    def __init__(self, engine: object) -> None:
        self.engine = engine

    def with_suppressed_output(self, callback):
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                return callback()

    def with_suppressed_warnings(self, callback):
        eval_fn = getattr(self.engine, "eval", None)
        if not callable(eval_fn):
            return callback()

        warnings_disabled = False
        try:
            eval_fn("warning('off','all');", nargout=0)
            warnings_disabled = True
        except Exception:
            return callback()

        try:
            return callback()
        finally:
            if warnings_disabled:
                try:
                    eval_fn("warning('on','all');", nargout=0)
                except Exception:
                    pass

    def call_method(
        self, method_name: str, *args: object, nargout: int = 1, quiet: bool = True
    ) -> object:
        method = getattr(self.engine, method_name)
        if quiet:
            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()
            try:
                return method(
                    *args,
                    nargout=nargout,
                    stdout=stdout_buffer,
                    stderr=stderr_buffer,
                )
            except TypeError:
                return self.with_suppressed_output(
                    lambda: method(*args, nargout=nargout)
                )
        return method(*args, nargout=nargout)

    def try_method(
        self, method_name: str, *args: object, nargout: int = 1, quiet: bool = True
    ) -> object | None:
        try:
            return self.call_method(
                method_name, *args, nargout=nargout, quiet=quiet
            )
        except Exception:
            return None

    def get_field_or_none(
        self, obj: object, field_name: str, *, allow_get: bool = False
    ) -> object | None:
        if allow_get:
            resolved = self.try_method("get", obj, field_name)
            if resolved is not None:
                return resolved
        return self.try_method("getfield", obj, field_name)

    def is_timeseries_object(self, obj: object) -> bool:
        return bool(self.try_method("isa", obj, "timeseries"))

    def to_string_list(self, raw_value: object) -> list[str]:
        if raw_value is None:
            return []
        if isinstance(raw_value, str):
            return [raw_value]
        try:
            values = list(raw_value)
        except TypeError:
            return [str(raw_value)]
        return [str(value) for value in values]

    def to_float_scalar(self, value: object) -> float:
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
        return self.to_float_scalar(converted[0])

    def to_float_series(self, raw_values: object) -> list[float]:
        if raw_values is None or isinstance(raw_values, (str, bytes)):
            return []
        try:
            values = list(raw_values)
        except TypeError:
            return [self.to_float_scalar(raw_values)]
        return [self.to_float_scalar(item) for item in values]

    def find_blocks_by_type(self, model_name: str, block_type: str) -> list[str]:
        def _call_find_system():
            return self.call_method(
                "find_system",
                model_name,
                "LookUnderMasks",
                "all",
                "FollowLinks",
                "on",
                "BlockType",
                block_type,
                nargout=1,
            )

        raw_blocks = self.with_suppressed_output(
            lambda: self.with_suppressed_warnings(_call_find_system)
        )
        return self.to_string_list(raw_blocks)


__all__ = [
    "MatlabEngineSession",
    "load_matlab_engine",
    "prepare_matlab_root",
    "purge_stale_matlab_modules",
]
