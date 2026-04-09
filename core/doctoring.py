from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from core.compat import slotted_dataclass


@slotted_dataclass
class DoctorCheck:
    name: str
    status: str
    detail: str


def mask_secret(value: str) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def models_endpoint(
    provider: str,
    base_url: str,
    api_key: str,
) -> tuple[str, dict[str, str]]:
    normalized_base_url = (base_url or "").rstrip("/")
    normalized_provider = (provider or "openai").strip().lower()

    if normalized_provider == "anthropic":
        if not normalized_base_url.endswith("/v1"):
            normalized_base_url = f"{normalized_base_url}/v1"
        return (
            f"{normalized_base_url}/models",
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )

    return (
        f"{normalized_base_url}/models",
        {"Authorization": f"Bearer {api_key}"},
    )


def _collect_matlab_checks(
    config: Mapping[str, Any],
    *,
    tr_fn: Callable[[str, str], str],
    path_exists: Callable[[str], bool],
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    matlab_model_path = str(config.get("MATLAB_MODEL_PATH", "") or "").strip()
    if not matlab_model_path:
        return checks

    checks.append(
        DoctorCheck(
            tr_fn("MATLAB 模型", "MATLAB model"),
            "PASS" if path_exists(matlab_model_path) else "FAIL",
            matlab_model_path,
        )
    )

    output_signal = str(config.get("MATLAB_OUTPUT_SIGNAL", "") or "").strip()
    signal_candidates = config.get("MATLAB_OUTPUT_SIGNAL_CANDIDATES", [])
    if not output_signal:
        checks.append(
            DoctorCheck(
                tr_fn("MATLAB 输出信号", "MATLAB output signal"),
                "FAIL",
                tr_fn(
                    "MATLAB_OUTPUT_SIGNAL 为空",
                    "MATLAB_OUTPUT_SIGNAL is empty",
                ),
            )
        )
    else:
        detail = f"primary={output_signal}"
        if isinstance(signal_candidates, list) and signal_candidates:
            detail += "; candidates=" + ", ".join(
                str(item).strip() for item in signal_candidates if str(item).strip()
            )
        checks.append(
            DoctorCheck(
                tr_fn("MATLAB 输出信号", "MATLAB output signal"),
                "PASS",
                detail,
            )
        )

    pid_block_path = str(config.get("MATLAB_PID_BLOCK_PATH", "") or "").strip()
    pid_block_paths = config.get("MATLAB_PID_BLOCK_PATHS", [])
    p_block_path = str(config.get("MATLAB_P_BLOCK_PATH", "") or "").strip()
    i_block_path = str(config.get("MATLAB_I_BLOCK_PATH", "") or "").strip()
    d_block_path = str(config.get("MATLAB_D_BLOCK_PATH", "") or "").strip()
    detail = pid_block_path or tr_fn(
        "MATLAB_PID_BLOCK_PATH 为空",
        "MATLAB_PID_BLOCK_PATH is empty",
    )
    if isinstance(pid_block_paths, list) and pid_block_paths:
        detail += "; candidates=" + ", ".join(
            str(item).strip() for item in pid_block_paths if str(item).strip()
        )
    if p_block_path or i_block_path or d_block_path:
        detail += (
            f"; separate=P:{p_block_path or '-'} "
            f"I:{i_block_path or '-'} D:{d_block_path or '-'}"
        )
    checks.append(
        DoctorCheck(
            tr_fn("PID 模块路径", "PID block path"),
            (
                "PASS"
                if (
                    pid_block_path
                    or (
                        isinstance(pid_block_paths, list)
                        and any(str(item).strip() for item in pid_block_paths)
                    )
                    or p_block_path
                )
                else "WARN"
            ),
            detail
            if (
                pid_block_path
                or (
                    isinstance(pid_block_paths, list)
                    and any(str(item).strip() for item in pid_block_paths)
                )
                or p_block_path
            )
            else tr_fn(
                "未显式配置，运行时将尝试自动发现 PID 控制器",
                "Not explicitly configured; runtime auto-discovery will be used",
            ),
        )
    )

    matlab_root = str(config.get("MATLAB_ROOT", "") or "").strip()
    checks.append(
        DoctorCheck(
            tr_fn("MATLAB 根目录", "MATLAB root"),
            "PASS" if not matlab_root or path_exists(matlab_root) else "WARN",
            matlab_root
            or tr_fn(
                "未配置，使用环境/已安装 Engine",
                "Not set; using environment/installed Engine",
            ),
        )
    )

    control_signal = str(config.get("MATLAB_CONTROL_SIGNAL", "") or "").strip()
    checks.append(
        DoctorCheck(
            tr_fn("控制输出信号", "control signal"),
            "PASS" if control_signal else "WARN",
            control_signal
            or tr_fn(
                "未配置，将继续使用占位 PWM=0.0",
                "Not configured; the bridge will keep using placeholder PWM=0.0",
            ),
        )
    )

    setpoint_block = str(config.get("MATLAB_SETPOINT_BLOCK", "") or "").strip()
    checks.append(
        DoctorCheck(
            tr_fn("设定值块", "setpoint block"),
            "PASS" if setpoint_block else "WARN",
            setpoint_block
            or tr_fn(
                "未显式指定，将尝试自动探测",
                "Not explicitly set; auto-detection will be used",
            ),
        )
    )

    return checks


def collect_doctor_checks(
    config: Mapping[str, Any],
    *,
    config_path: str,
    tr_fn: Callable[[str, str], str],
    initialize_runtime_config_fn: Callable[..., Any],
    requests_get: Callable[..., Any],
    list_serial_ports: Callable[[], Iterable[Any]],
    path_exists: Callable[[str], bool],
    getenv: Callable[[str], str | None],
) -> list[DoctorCheck]:
    initialize_runtime_config_fn(create_if_missing=False, verbose=False)
    checks: list[DoctorCheck] = []

    has_config = path_exists(config_path)
    checks.append(
        DoctorCheck(
            tr_fn("配置文件", "config.json"),
            "PASS" if has_config else "FAIL",
            f"{tr_fn('路径', 'path')}={config_path}",
        )
    )

    required_fields = (
        "LLM_API_KEY",
        "LLM_API_BASE_URL",
        "LLM_MODEL_NAME",
        "LLM_PROVIDER",
    )
    missing = [field for field in required_fields if not config.get(field)]
    placeholder_key = str(config.get("LLM_API_KEY", "")) == "your-api-key-here"
    if missing or placeholder_key:
        detail: list[str] = []
        if missing:
            detail.append(tr_fn("缺失=", "missing=") + ", ".join(missing))
        if placeholder_key:
            detail.append(
                tr_fn(
                    "LLM_API_KEY 仍为默认占位符",
                    "LLM_API_KEY is still the placeholder value",
                )
            )
        checks.append(
            DoctorCheck(
                tr_fn("配置字段", "config fields"),
                "FAIL",
                "; ".join(detail),
            )
        )
    else:
        checks.append(
            DoctorCheck(
                tr_fn("配置字段", "config fields"),
                "PASS",
                (
                    f"{tr_fn('提供商', 'provider')}={config.get('LLM_PROVIDER')} "
                    f"{tr_fn('模型', 'model')}={config.get('LLM_MODEL_NAME')} "
                    f"api_key={mask_secret(str(config.get('LLM_API_KEY', '')))}"
                ),
            )
        )

    base_url = str(config.get("LLM_API_BASE_URL", "")).strip()
    provider = str(config.get("LLM_PROVIDER", "openai")).strip()
    if not base_url:
        checks.append(
            DoctorCheck(
                tr_fn("API 连通性", "API reachability"),
                "FAIL",
                tr_fn("LLM_API_BASE_URL 为空", "LLM_API_BASE_URL is empty"),
            )
        )
    else:
        endpoint, headers = models_endpoint(
            provider,
            base_url,
            str(config.get("LLM_API_KEY", "")),
        )
        try:
            response = requests_get(endpoint, headers=headers, timeout=5)
            if response.status_code < 500:
                status = "PASS" if response.ok else "WARN"
                detail = (
                    f"{tr_fn('可连通状态码', 'reachable status')}={response.status_code} "
                    f"{tr_fn('端点', 'endpoint')}={endpoint}"
                )
            else:
                status = "FAIL"
                detail = (
                    f"{tr_fn('服务端错误状态码', 'server error status')}={response.status_code} "
                    f"{tr_fn('端点', 'endpoint')}={endpoint}"
                )
        except Exception as exc:
            status = "FAIL"
            detail = f"{tr_fn('请求失败', 'request failed')}: {exc}"
        checks.append(
            DoctorCheck(
                tr_fn("API 连通性", "API reachability"),
                status,
                detail,
            )
        )

    ports = list(list_serial_ports())
    if ports:
        detail = ", ".join(port.device for port in ports[:5])
        if len(ports) > 5:
            detail += ", ..."
        checks.append(
            DoctorCheck(tr_fn("串口设备", "serial ports"), "PASS", detail)
        )
    else:
        checks.append(
            DoctorCheck(
                tr_fn("串口设备", "serial ports"),
                "WARN",
                tr_fn(
                    "未检测到串口设备。仅在纯仿真模式下这没有问题。",
                    "No serial device detected. This is fine for simulator-only usage.",
                ),
            )
        )

    checks.append(
        DoctorCheck(
            tr_fn("协议字段", "protocol fields"),
            "PASS",
            tr_fn("预期的 CSV 格式: ", "expected CSV: ")
            + "timestamp_ms,setpoint,input,pwm,error,p,i,d",
        )
    )

    proxy_keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
    proxy_parts = []
    for key in proxy_keys:
        env_value = getenv(key) or getenv(key.lower()) or ""
        cfg_value = str(config.get(key, "") or "")
        if env_value:
            proxy_parts.append(f"{key}=env:{env_value}")
        elif cfg_value:
            proxy_parts.append(f"{key}=config:{cfg_value}")
    checks.append(
        DoctorCheck(
            tr_fn("代理设置", "proxy settings"),
            "PASS",
            "; ".join(proxy_parts)
            if proxy_parts
            else tr_fn("未配置代理", "No proxy configured"),
        )
    )

    checks.extend(
        _collect_matlab_checks(
            config,
            tr_fn=tr_fn,
            path_exists=path_exists,
        )
    )
    return checks


def summarize_doctor_checks(
    checks: Iterable[DoctorCheck],
    *,
    tr_fn: Callable[[str, str], str],
) -> str:
    items = list(checks)
    pass_count = sum(1 for check in items if check.status == "PASS")
    warn_count = sum(1 for check in items if check.status == "WARN")
    fail_count = sum(1 for check in items if check.status == "FAIL")
    return tr_fn(
        f"Doctor 诊断汇总: {pass_count} 通过, {warn_count} 警告, {fail_count} 失败。",
        f"Doctor summary: {pass_count} pass, {warn_count} warn, {fail_count} fail.",
    )


def print_doctor_report(
    checks: Iterable[DoctorCheck],
    *,
    tr_fn: Callable[[str, str], str],
    printer: Callable[[str], None] = print,
) -> int:
    items = list(checks)
    printer("=" * 60)
    printer("LLM PID Tuner Doctor")
    printer("=" * 60)
    for check in items:
        printer(f"[{check.status:<4}] {check.name}: {check.detail}")

    has_fail = any(check.status == "FAIL" for check in items)
    has_warn = any(check.status == "WARN" for check in items)
    printer("-" * 60)
    if has_fail:
        printer(
            tr_fn(
                "Doctor 诊断完成，包含失败(FAIL)项。",
                "Doctor finished with FAIL items.",
            )
        )
        return 1
    if has_warn:
        printer(
            tr_fn(
                "Doctor 诊断完成，包含警告(WARN)项。",
                "Doctor finished with WARN items.",
            )
        )
        return 0
    printer(tr_fn("Doctor 诊断成功通过。", "Doctor finished successfully."))
    return 0
