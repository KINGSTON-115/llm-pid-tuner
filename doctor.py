#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
from typing import Iterable

import requests
import serial.tools.list_ports

from core.config import CONFIG, CONFIG_PATH, initialize_runtime_config
from core.doctoring import (
    DoctorCheck,
    collect_doctor_checks as _collect_doctor_checks,
    print_doctor_report as _print_doctor_report,
    summarize_doctor_checks as _summarize_doctor_checks,
)
from core.i18n import tr


def collect_doctor_checks() -> list[DoctorCheck]:
    return _collect_doctor_checks(
        CONFIG,
        config_path=CONFIG_PATH,
        tr_fn=tr,
        initialize_runtime_config_fn=initialize_runtime_config,
        requests_get=requests.get,
        list_serial_ports=serial.tools.list_ports.comports,
        path_exists=os.path.exists,
        getenv=os.getenv,
    )


def summarize_doctor_checks(checks: Iterable[DoctorCheck]) -> str:
    return _summarize_doctor_checks(checks, tr_fn=tr)


def print_doctor_report(checks: Iterable[DoctorCheck]) -> int:
    return _print_doctor_report(checks, tr_fn=tr)


def main() -> int:
    return print_doctor_report(collect_doctor_checks())


if __name__ == "__main__":
    raise SystemExit(main())
