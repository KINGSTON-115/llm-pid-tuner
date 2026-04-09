#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Global runtime configuration helpers."""

import io
import json
import os
import sys
from typing import Any


def ensure_utf8_console() -> None:
    """Force UTF-8 console IO on Windows when possible."""
    if sys.platform != "win32":
        return

    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass

    def _retarget_stream_if_needed(stream: Any) -> Any:
        encoding = getattr(stream, "encoding", None)
        if not encoding or encoding.lower() == "utf-8":
            return None

        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", line_buffering=True)
                return None
            except Exception:
                pass

        is_tty = getattr(stream, "isatty", lambda: False)
        try:
            if is_tty():
                return None
        except Exception:
            pass

        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            return None
        try:
            return io.TextIOWrapper(buffer, encoding="utf-8", line_buffering=True)
        except Exception:
            return None

    wrapped_stdout = _retarget_stream_if_needed(sys.stdout)
    if wrapped_stdout is not None:
        sys.stdout = wrapped_stdout

    wrapped_stderr = _retarget_stream_if_needed(sys.stderr)
    if wrapped_stderr is not None:
        sys.stderr = wrapped_stderr


# ============================================================================
# Default Configuration
# ============================================================================

DEFAULT_CONFIG: dict[str, Any] = {
    "SERIAL_PORT"                   : "AUTO",
    "BAUD_RATE"                     : 115200,
    "LLM_API_KEY"                   : "your-api-key-here",
    "LLM_API_BASE_URL"              : "https://api.openai.com/v1",
    "LLM_MODEL_NAME"                : "gpt-4o",
    "LLM_PROVIDER"                  : "openai",
    "HTTP_PROXY"                    : "",
    "HTTPS_PROXY"                   : "",
    "ALL_PROXY"                     : "",
    "NO_PROXY"                      : "",
    "BUFFER_SIZE"                   : 100,
    "MIN_ERROR_THRESHOLD"           : 0.3,
    "MAX_TUNING_ROUNDS"             : 50,
    "LLM_REQUEST_TIMEOUT"           : 60,
    "LLM_DEBUG_OUTPUT"              : False,
    "GOOD_ENOUGH_AVG_ERROR"         : 0.1,
    "GOOD_ENOUGH_STEADY_STATE_ERROR": 0.05,
    "GOOD_ENOUGH_OVERSHOOT"         : 1.0,
    "REQUIRED_STABLE_ROUNDS"        : 10,
    "MATLAB_MODEL_PATH"             : "",
    "MATLAB_PID_BLOCK_PATH"         : "",
    "MATLAB_ROOT"                   : "",
    "MATLAB_OUTPUT_SIGNAL"          : "y_out",
    "MATLAB_OUTPUT_SIGNAL_CANDIDATES": [],
    "MATLAB_CONTROL_SIGNAL"         : "",
    "MATLAB_SETPOINT_BLOCK"         : "",
    "MATLAB_PID_BLOCK_PATHS"        : [],
    "MATLAB_PID_BLOCK_PATH_2"       : "",
    "MATLAB_SIM_STEP_TIME"          : 15.0,
    "MATLAB_SETPOINT"               : 200.0,
}

CONFIG: dict[str, Any] = dict(DEFAULT_CONFIG)
CONFIG_PATH = "config.json"
PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


def _parse_env_value(default_value: Any, raw_value: str) -> Any:
    if isinstance(default_value, bool):
        return raw_value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default_value, int) and not isinstance(default_value, bool):
        return int(raw_value)
    if isinstance(default_value, float):
        return float(raw_value)
    return raw_value


def load_config(create_if_missing: bool = True, verbose: bool = True) -> None:
    """Load config from disk and environment variables."""
    CONFIG.clear()
    CONFIG.update(DEFAULT_CONFIG)

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
                user_config = json.load(handle)
            CONFIG.update(user_config)
            if verbose:
                print(f"[INFO] 已加载配置文件: {CONFIG_PATH}")
        except Exception as exc:
            if verbose:
                print(f"[WARN] 配置文件加载失败: {exc}，将使用默认值。")
    elif create_if_missing:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
                json.dump(CONFIG, handle, indent=4, ensure_ascii=False)
            if verbose:
                print(f"[INFO] 未找到配置文件，已生成默认配置: {CONFIG_PATH}")
                print(f"[HINT] 请打开 {CONFIG_PATH} 修改您的 API Key 和串口设置。")
        except Exception as exc:
            if verbose:
                print(f"[WARN] 无法创建配置文件: {exc}")

    for key in list(CONFIG):
        env_val = os.getenv(key)
        if not env_val:
            continue
        try:
            CONFIG[key] = _parse_env_value(CONFIG[key], env_val)
        except Exception:
            if verbose:
                print(f"[WARN] 环境变量 {key} 值无效，已忽略。")


def _apply_proxy_env_from_config() -> None:
    """Populate proxy env vars from config when the environment is unset."""
    for key in PROXY_KEYS:
        raw_value = CONFIG.get(key)
        if raw_value is None:
            continue
        if not isinstance(raw_value, str):
            if CONFIG.get("LLM_DEBUG_OUTPUT"):
                print(
                    f"[WARN] 代理配置 {key} 应为字符串，当前类型为 "
                    f"{type(raw_value).__name__}，已忽略。"
                )
            continue
        value = raw_value.strip()
        if not value:
            continue
        if not os.getenv(key):
            os.environ[key] = value
        lower_key = key.lower()
        if not os.getenv(lower_key):
            os.environ[lower_key] = value


def initialize_runtime_config(
    create_if_missing: bool = True, verbose: bool = True
) -> None:
    """Initialize runtime config and proxy environment variables."""
    ensure_utf8_console()
    load_config(create_if_missing=create_if_missing, verbose=verbose)
    _apply_proxy_env_from_config()
