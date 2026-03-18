#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
from typing import Iterable

import requests
import serial.tools.list_ports

from core.compat import slotted_dataclass
from core.config import CONFIG, CONFIG_PATH, initialize_runtime_config


@slotted_dataclass
class DoctorCheck:
    name: str
    status: str
    detail: str


def _mask_secret(value: str) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _models_endpoint(provider: str, base_url: str) -> tuple[str, dict[str, str]]:
    base_url = (base_url or "").rstrip("/")
    provider = (provider or "openai").strip().lower()

    if provider == "anthropic":
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        return (
            f"{base_url}/models",
            {
                "x-api-key": str(CONFIG.get("LLM_API_KEY", "")),
                "anthropic-version": "2023-06-01",
            },
        )

    return (
        f"{base_url}/models",
        {"Authorization": f"Bearer {CONFIG.get('LLM_API_KEY', '')}"},
    )


def collect_doctor_checks() -> list[DoctorCheck]:
    initialize_runtime_config(create_if_missing=False, verbose=False)
    checks: list[DoctorCheck] = []

    has_config = os.path.exists(CONFIG_PATH)
    checks.append(
        DoctorCheck(
            "config.json",
            "PASS" if has_config else "FAIL",
            f"path={CONFIG_PATH}",
        )
    )

    required_fields = ("LLM_API_KEY", "LLM_API_BASE_URL", "LLM_MODEL_NAME", "LLM_PROVIDER")
    missing = [field for field in required_fields if not CONFIG.get(field)]
    placeholder_key = str(CONFIG.get("LLM_API_KEY", "")) == "your-api-key-here"
    if missing or placeholder_key:
        detail = []
        if missing:
            detail.append("missing=" + ", ".join(missing))
        if placeholder_key:
            detail.append("LLM_API_KEY is still the placeholder value")
        checks.append(DoctorCheck("config fields", "FAIL", "; ".join(detail)))
    else:
        checks.append(
            DoctorCheck(
                "config fields",
                "PASS",
                (
                    f"provider={CONFIG.get('LLM_PROVIDER')} "
                    f"model={CONFIG.get('LLM_MODEL_NAME')} "
                    f"api_key={_mask_secret(str(CONFIG.get('LLM_API_KEY', '')))}"
                ),
            )
        )

    base_url = str(CONFIG.get("LLM_API_BASE_URL", "")).strip()
    provider = str(CONFIG.get("LLM_PROVIDER", "openai")).strip()
    if not base_url:
        checks.append(DoctorCheck("API reachability", "FAIL", "LLM_API_BASE_URL is empty"))
    else:
        endpoint, headers = _models_endpoint(provider, base_url)
        try:
            response = requests.get(endpoint, headers=headers, timeout=3)
            if response.status_code < 500:
                status = "PASS" if response.ok else "WARN"
                detail = f"reachable status={response.status_code} endpoint={endpoint}"
            else:
                status = "FAIL"
                detail = f"server error status={response.status_code} endpoint={endpoint}"
        except requests.RequestException as exc:
            status = "FAIL"
            detail = f"request failed: {exc}"
        checks.append(DoctorCheck("API reachability", status, detail))

    ports = list(serial.tools.list_ports.comports())
    if ports:
        detail = ", ".join(port.device for port in ports[:5])
        if len(ports) > 5:
            detail += ", ..."
        checks.append(DoctorCheck("serial ports", "PASS", detail))
    else:
        checks.append(
            DoctorCheck(
                "serial ports",
                "WARN",
                "No serial device detected. This is fine for simulator-only usage.",
            )
        )

    checks.append(
        DoctorCheck(
            "protocol fields",
            "PASS",
            "expected CSV: timestamp_ms,setpoint,input,pwm,error,p,i,d",
        )
    )

    proxy_keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
    proxy_parts = []
    for key in proxy_keys:
        env_value = os.getenv(key) or os.getenv(key.lower()) or ""
        cfg_value = str(CONFIG.get(key, "") or "")
        if env_value:
            proxy_parts.append(f"{key}=env:{env_value}")
        elif cfg_value:
            proxy_parts.append(f"{key}=config:{cfg_value}")
    checks.append(
        DoctorCheck(
            "proxy settings",
            "PASS",
            "; ".join(proxy_parts) if proxy_parts else "No proxy configured",
        )
    )

    return checks


def summarize_doctor_checks(checks: Iterable[DoctorCheck]) -> str:
    checks = list(checks)
    pass_count = sum(1 for check in checks if check.status == "PASS")
    warn_count = sum(1 for check in checks if check.status == "WARN")
    fail_count = sum(1 for check in checks if check.status == "FAIL")
    return f"Doctor summary: {pass_count} pass, {warn_count} warn, {fail_count} fail."


def print_doctor_report(checks: Iterable[DoctorCheck]) -> int:
    checks = list(checks)
    print("=" * 60)
    print("LLM PID Tuner Doctor")
    print("=" * 60)
    for check in checks:
        print(f"[{check.status:<4}] {check.name}: {check.detail}")

    has_fail = any(check.status == "FAIL" for check in checks)
    has_warn = any(check.status == "WARN" for check in checks)
    print("-" * 60)
    if has_fail:
        print("Doctor finished with FAIL items.")
        return 1
    if has_warn:
        print("Doctor finished with WARN items.")
        return 0
    print("Doctor finished successfully.")
    return 0


def main() -> int:
    return print_doctor_report(collect_doctor_checks())


if __name__ == "__main__":
    raise SystemExit(main())
