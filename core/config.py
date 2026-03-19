#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/config.py - 全局配置管理

提供 CONFIG 字典、配置文件加载及运行时初始化函数。
其他模块直接从本模块导入 CONFIG 和 initialize_runtime_config。
"""

import sys


def ensure_utf8_console():
    """在 Windows 下强制设置控制台的输入输出编码为 UTF-8"""
    if sys.platform == "win32":
        try:
            import ctypes

            # 设置控制台输出代码页为 UTF-8 (65001)
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            # 同时也设置输入代码页，防止后续输入出问题
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception as e:
            pass

        # 强制重置 stdout/stderr 为 UTF-8（分别检查，避免 stdout 已是 UTF-8 时漏掉 stderr）
        import io

        def _wrap_if_needed(stream):
            if not hasattr(stream, "encoding") or not stream.encoding:
                return
            if stream.encoding.lower() == "utf-8":
                return
            try:
                return io.TextIOWrapper(
                    stream.buffer, encoding="utf-8", line_buffering=True
                )
            except AttributeError:
                return None

        wrapped_stdout = _wrap_if_needed(sys.stdout)
        if wrapped_stdout is not None:
            sys.stdout = wrapped_stdout
        wrapped_stderr = _wrap_if_needed(sys.stderr)
        if wrapped_stderr is not None:
            sys.stderr = wrapped_stderr


import json
import os
from typing import Any

from core.i18n import tr

# ============================================================================
# 默认配置
# ============================================================================

CONFIG: dict = {
    "SERIAL_PORT"                   : "AUTO",  # "AUTO" 或具体端口号 (如 "COM3")
    "BAUD_RATE"                     : 115200,
    "LLM_API_KEY"                   : "your-api-key-here",
    "LLM_API_BASE_URL"              : "https://api.openai.com/v1",
    "LLM_MODEL_NAME"                : "gpt-4",
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
    "GOOD_ENOUGH_AVG_ERROR"         : 1.2,
    "GOOD_ENOUGH_STEADY_STATE_ERROR": 0.3,
    "GOOD_ENOUGH_OVERSHOOT"         : 2.0,
    "REQUIRED_STABLE_ROUNDS"        : 2,
    "LANGUAGE"                      : "",      # "zh" / "en"，空则自动检测
    # MATLAB/Simulink 模式专属配置（使用 matlab_tuner.py 时填写）
    "MATLAB_MODEL_PATH"             : "",      # Simulink .slx 文件完整路径
    "MATLAB_PID_BLOCK_PATH"         : "",      # PID 模块路径，如 "my_model/PID Controller"
    "MATLAB_OUTPUT_SIGNAL"          : "y_out", # To Workspace 变量名
    "MATLAB_SIM_STEP_TIME"          : 10.0,    # 每轮仿真时长（仿真秒数）
    "MATLAB_SETPOINT"               : 200.0,   # 调参目标值
}

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
    """加载配置文件；按需创建，避免 import 时产生副作用"""
    global CONFIG

    # 1. 尝试读取配置文件
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user_config = json.load(f)
                CONFIG.update(user_config)
                if verbose:
                    print(
                        tr(
                            f"[INFO] 已加载配置文件: {CONFIG_PATH}",
                            f"[INFO] Config loaded: {CONFIG_PATH}",
                        )
                    )
        except Exception as e:
            if verbose:
                print(
                    tr(
                        f"[WARN] 配置文件加载失败: {e}，将使用默认值。",
                        f"[WARN] Config load failed: {e}, using defaults.",
                    )
                )
    elif create_if_missing:
        # 2. 如果不存在，自动创建默认配置
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(CONFIG, f, indent=4, ensure_ascii=False)
            if verbose:
                print(
                    tr(
                        f"[INFO] 未找到配置文件，已生成默认配置: {CONFIG_PATH}",
                        f"[INFO] Config not found, created default: {CONFIG_PATH}",
                    )
                )
                print(
                    tr(
                        f"[HINT] 请打开 {CONFIG_PATH} 修改您的 API Key 和串口设置。",
                        f"[HINT] Open {CONFIG_PATH} to set your API Key and serial port.",
                    )
                )
        except Exception as e:
            if verbose:
                print(
                    tr(
                        f"[WARN] 无法创建配置文件: {e}",
                        f"[WARN] Cannot create config file: {e}",
                    )
                )

    # 3. 环境变量覆盖 (优先级最高)
    for key in CONFIG:
        env_val = os.getenv(key)
        if env_val:
            try:
                CONFIG[key] = _parse_env_value(CONFIG[key], env_val)
            except Exception:
                if verbose:
                    print(f"[WARN] 环境变量 {key} 值无效，已忽略。")


def _apply_proxy_env_from_config() -> None:
    """将配置中的代理写入环境变量（仅在环境未显式设置时生效）。"""
    for key in PROXY_KEYS:
        raw_value = CONFIG.get(key)
        if raw_value is None:
            continue
        if not isinstance(raw_value, str):
            if CONFIG.get("LLM_DEBUG_OUTPUT"):
                print(
                    f"[WARN] 代理配置 {key} 应为字符串，当前类型为 {type(raw_value).__name__}，已忽略。"
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
    """加载配置文件并更新 CONFIG。可安全地多次调用。"""
    ensure_utf8_console()
    load_config(create_if_missing=create_if_missing, verbose=verbose)
    _apply_proxy_env_from_config()
    lang = CONFIG.get("LANGUAGE", "").strip().lower()
    if lang in ("zh", "en"):
        from core.i18n import set_language
        set_language(lang)
