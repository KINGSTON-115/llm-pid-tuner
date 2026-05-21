from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

DEFAULT_HARDWARE_PROFILE = "generic_serial_csv"

_PROFILE_ALIASES = {
    "generic": DEFAULT_HARDWARE_PROFILE,
    "csv": DEFAULT_HARDWARE_PROFILE,
    "firmware": DEFAULT_HARDWARE_PROFILE,
    "default": DEFAULT_HARDWARE_PROFILE,
    "stm32": "stm32f407_openmv",
    "stm32f407": "stm32f407_openmv",
    "openmv": "stm32f407_openmv",
    "stm32f407_openmv": "stm32f407_openmv",
    "mspm0": "mspm0_datavision",
    "mspm03507": "mspm0_datavision",
    "mspm0_datavision": "mspm0_datavision",
}

_PROFILE_INFO: Dict[str, Dict[str, Any]] = {
    "generic_serial_csv": {
        "board_family": "generic_serial",
        "note": (
            "Default CSV telemetry from firmware.cpp or similar demo firmware. "
            "Expect timestamp,setpoint,input,pwm,error,p,i,d rows."
        ),
        "controller_count": 1,
        "vision_center": None,
    },
    "stm32f407_openmv": {
        "board_family": "stm32f407",
        "note": (
            "STM32F407 aiming console with OpenMV target stream. "
            "Status snapshots expose target, servo, and pid.x/pid.y values; "
            "treat this as a dual-controller profile."
        ),
        "controller_count": 2,
        "vision_center": (80, 60),
    },
    "mspm0_datavision": {
        "board_family": "mspm0",
        "note": (
            "MSPM0 DataVision telemetry is frame-based and telemetry-first. "
            "Pair SEND_TARGET and SEND_FACT samples by channel, and keep "
            "write-back disabled until a command protocol is confirmed."
        ),
        "controller_count": 1,
        "vision_center": None,
    },
}


def normalize_hardware_profile(profile: Any) -> str:
    normalized = str(profile or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        return DEFAULT_HARDWARE_PROFILE
    return _PROFILE_ALIASES.get(normalized, normalized if normalized in _PROFILE_INFO else DEFAULT_HARDWARE_PROFILE)


def get_hardware_profile_info(profile: Any) -> Dict[str, Any]:
    normalized = normalize_hardware_profile(profile)
    info = dict(_PROFILE_INFO.get(normalized, _PROFILE_INFO[DEFAULT_HARDWARE_PROFILE]))
    info["hardware_profile"] = normalized
    return info


def get_hardware_board_family(profile: Any) -> str:
    return str(get_hardware_profile_info(profile).get("board_family", "generic_serial"))


def get_hardware_profile_note(profile: Any) -> str:
    return str(get_hardware_profile_info(profile).get("note", ""))


def is_dual_controller_profile(profile: Any) -> bool:
    return int(get_hardware_profile_info(profile).get("controller_count", 1) or 1) > 1


def get_openmv_image_center(profile: Any) -> Optional[tuple[int, int]]:
    center = get_hardware_profile_info(profile).get("vision_center")
    if not center:
        return None
    return int(center[0]), int(center[1])


def get_hardware_sample_format_hint(profile: Any) -> str:
    normalized = normalize_hardware_profile(profile)
    if normalized == "stm32f407_openmv":
        return "STM32 Status: lines plus OpenMV T:x,y / N messages"
    if normalized == "mspm0_datavision":
        return "MSPM0 DataVision binary frames"
    return "CSV rows: timestamp,setpoint,input,pwm,error,p,i,d"


def build_profile_commands(
    profile: Any,
    kind: str,
    primary_pid: Optional[Dict[str, float]] = None,
    secondary_pid: Optional[Dict[str, float]] = None,
) -> List[str]:
    normalized = normalize_hardware_profile(profile)
    command_kind = str(kind or "").strip().upper()
    primary_pid = primary_pid or {}
    secondary_pid = secondary_pid or {}

    if command_kind == "STATUS":
        return ["status"] if normalized == "stm32f407_openmv" else ["STATUS"]

    if command_kind not in {"SET", "SET2"}:
        return []

    if normalized == "mspm0_datavision":
        return []

    pid = primary_pid if command_kind == "SET" else secondary_pid
    if not pid:
        return []

    if normalized == "stm32f407_openmv":
        prefix = "pid.x" if command_kind == "SET" else "pid.y"
        return [
            f"config {prefix}.kp {pid.get('p', 0.0)}",
            f"config {prefix}.ki {pid.get('i', 0.0)}",
            f"config {prefix}.kd {pid.get('d', 0.0)}",
        ]

    gain_label = "P" if command_kind == "SET" else "P"
    return [
        f"{command_kind} {gain_label}:{pid.get('p', 0.0)} I:{pid.get('i', 0.0)} D:{pid.get('d', 0.0)}"
    ]
