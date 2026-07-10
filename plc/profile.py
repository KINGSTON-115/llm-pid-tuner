from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping


class PLCProfileError(ValueError):
    """Raised when a PLC profile is incomplete or unsafe."""


_REGISTER_WORDS = {
    "bool": 1,
    "uint16": 1,
    "int16": 1,
    "uint32": 2,
    "int32": 2,
    "float32": 2,
}

_REQUIRED_READ_REGISTERS = {
    "pv",
    "setpoint",
    "output",
    "kp",
    "integral",
    "derivative",
}

_REQUIRED_WRITE_REGISTERS = {
    "candidate_kp",
    "candidate_integral",
    "candidate_derivative",
    "apply_sequence",
    "ack_sequence",
}


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PLCProfileError(f"{field_name} must be an object")
    return value


def _finite_float(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PLCProfileError(f"{field_name} must be numeric") from exc
    if not math.isfinite(result):
        raise PLCProfileError(f"{field_name} must be finite")
    return result


@dataclass(frozen=True)
class RegisterSpec:
    address: int
    table: str = "holding"
    data_type: str = "float32"
    scale: float = 1.0
    offset: float = 0.0
    byte_order: str = "big"
    word_order: str = "big"

    @property
    def word_count(self) -> int:
        return _REGISTER_WORDS[self.data_type]

    @property
    def writable(self) -> bool:
        return self.table == "holding"

    @classmethod
    def from_dict(cls, name: str, raw: Any) -> "RegisterSpec":
        data = _mapping(raw, f"registers.{name}")
        try:
            address = int(data["address"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PLCProfileError(
                f"registers.{name}.address must be a non-negative integer"
            ) from exc
        if address < 0:
            raise PLCProfileError(f"registers.{name}.address must be non-negative")

        table = str(data.get("table", "holding")).strip().lower()
        if table not in {"holding", "input"}:
            raise PLCProfileError(
                f"registers.{name}.table must be 'holding' or 'input'"
            )

        data_type = str(data.get("data_type", "float32")).strip().lower()
        if data_type not in _REGISTER_WORDS:
            supported = ", ".join(sorted(_REGISTER_WORDS))
            raise PLCProfileError(
                f"registers.{name}.data_type must be one of: {supported}"
            )
        if address + _REGISTER_WORDS[data_type] > 65536:
            raise PLCProfileError(
                f"registers.{name} exceeds the Modbus address range"
            )

        scale = _finite_float(data.get("scale", 1.0), f"registers.{name}.scale")
        if scale == 0.0:
            raise PLCProfileError(f"registers.{name}.scale must not be zero")
        offset = _finite_float(
            data.get("offset", 0.0), f"registers.{name}.offset"
        )

        byte_order = str(data.get("byte_order", "big")).strip().lower()
        word_order = str(data.get("word_order", "big")).strip().lower()
        if byte_order not in {"big", "little"}:
            raise PLCProfileError(
                f"registers.{name}.byte_order must be 'big' or 'little'"
            )
        if word_order not in {"big", "little"}:
            raise PLCProfileError(
                f"registers.{name}.word_order must be 'big' or 'little'"
            )

        return cls(
            address=address,
            table=table,
            data_type=data_type,
            scale=scale,
            offset=offset,
            byte_order=byte_order,
            word_order=word_order,
        )


@dataclass(frozen=True)
class ConnectionSettings:
    protocol: str
    host: str
    port: int
    device_id: int
    timeout_sec: float

    @classmethod
    def from_dict(cls, raw: Any) -> "ConnectionSettings":
        data = _mapping(raw, "connection")
        protocol = str(data.get("protocol", "modbus_tcp")).strip().lower()
        if protocol != "modbus_tcp":
            raise PLCProfileError("the prototype currently supports modbus_tcp only")
        host = str(data.get("host", "")).strip()
        if not host:
            raise PLCProfileError("connection.host must not be empty")
        try:
            port = int(data.get("port", 502))
            device_id = int(data.get("device_id", 1))
        except (TypeError, ValueError) as exc:
            raise PLCProfileError(
                "connection.port and connection.device_id must be integers"
            ) from exc
        if not 1 <= port <= 65535:
            raise PLCProfileError("connection.port must be between 1 and 65535")
        if not 0 <= device_id <= 255:
            raise PLCProfileError("connection.device_id must be between 0 and 255")
        timeout_sec = _finite_float(
            data.get("timeout_sec", 3.0), "connection.timeout_sec"
        )
        if timeout_sec <= 0.0:
            raise PLCProfileError("connection.timeout_sec must be positive")
        return cls(protocol, host, port, device_id, timeout_sec)


@dataclass(frozen=True)
class AcquisitionSettings:
    poll_interval_sec: float
    sample_count: int
    minimum_samples: int
    timeout_sec: float

    @classmethod
    def from_dict(cls, raw: Any) -> "AcquisitionSettings":
        data = _mapping(raw or {}, "acquisition")
        poll_interval = _finite_float(
            data.get("poll_interval_sec", 0.2), "acquisition.poll_interval_sec"
        )
        timeout = _finite_float(
            data.get("timeout_sec", 30.0), "acquisition.timeout_sec"
        )
        try:
            sample_count = int(data.get("sample_count", 100))
            minimum_samples = int(data.get("minimum_samples", min(20, sample_count)))
        except (TypeError, ValueError) as exc:
            raise PLCProfileError(
                "acquisition sample counts must be integers"
            ) from exc
        if poll_interval < 0.0:
            raise PLCProfileError("acquisition.poll_interval_sec must be non-negative")
        if timeout <= 0.0:
            raise PLCProfileError("acquisition.timeout_sec must be positive")
        if sample_count < 1:
            raise PLCProfileError("acquisition.sample_count must be at least 1")
        if not 1 <= minimum_samples <= sample_count:
            raise PLCProfileError(
                "acquisition.minimum_samples must be between 1 and sample_count"
            )
        return cls(poll_interval, sample_count, minimum_samples, timeout)


@dataclass(frozen=True)
class PIDSemantics:
    form: str
    integral_time_unit: str
    derivative_time_unit: str
    controller_action: str

    @classmethod
    def from_dict(cls, raw: Any) -> "PIDSemantics":
        data = _mapping(raw, "pid_semantics")
        form = str(data.get("form", "parallel")).strip().lower()
        if form not in {"parallel", "ideal"}:
            raise PLCProfileError("pid_semantics.form must be 'parallel' or 'ideal'")
        integral_unit = str(
            data.get("integral_time_unit", "seconds")
        ).strip().lower()
        derivative_unit = str(
            data.get("derivative_time_unit", "seconds")
        ).strip().lower()
        for field_name, unit in (
            ("integral_time_unit", integral_unit),
            ("derivative_time_unit", derivative_unit),
        ):
            if unit not in {"seconds", "minutes"}:
                raise PLCProfileError(
                    f"pid_semantics.{field_name} must be 'seconds' or 'minutes'"
                )
        action = str(data.get("controller_action", "reverse")).strip().lower()
        if action not in {"direct", "reverse"}:
            raise PLCProfileError(
                "pid_semantics.controller_action must be 'direct' or 'reverse'"
            )
        return cls(form, integral_unit, derivative_unit, action)


@dataclass(frozen=True)
class WritePolicy:
    mode: str
    require_auto_mode: bool
    require_tune_enable: bool
    require_interlock: bool
    ack_timeout_sec: float
    ack_poll_interval_sec: float

    @classmethod
    def from_dict(cls, raw: Any) -> "WritePolicy":
        data = _mapping(raw or {}, "write_policy")
        mode = str(data.get("mode", "disabled")).strip().lower()
        if mode not in {"disabled", "confirm", "auto"}:
            raise PLCProfileError(
                "write_policy.mode must be disabled, confirm, or auto"
            )
        ack_timeout = _finite_float(
            data.get("ack_timeout_sec", 3.0), "write_policy.ack_timeout_sec"
        )
        ack_poll = _finite_float(
            data.get("ack_poll_interval_sec", 0.05),
            "write_policy.ack_poll_interval_sec",
        )
        if ack_timeout <= 0.0 or ack_poll < 0.0:
            raise PLCProfileError(
                "write_policy ack timeout must be positive and poll interval non-negative"
            )
        return cls(
            mode=mode,
            require_auto_mode=bool(data.get("require_auto_mode", True)),
            require_tune_enable=bool(data.get("require_tune_enable", True)),
            require_interlock=bool(data.get("require_interlock", True)),
            ack_timeout_sec=ack_timeout,
            ack_poll_interval_sec=ack_poll,
        )


@dataclass(frozen=True)
class PLCProfile:
    name: str
    connection: ConnectionSettings
    acquisition: AcquisitionSettings
    pid_semantics: PIDSemantics
    write_policy: WritePolicy
    registers: Dict[str, RegisterSpec]
    canonical_pid_limits: Dict[str, Dict[str, float]]
    process: Dict[str, Any]

    @classmethod
    def from_dict(cls, raw: Any) -> "PLCProfile":
        data = _mapping(raw, "profile")
        name = str(data.get("name", "")).strip()
        if not name:
            raise PLCProfileError("profile.name must not be empty")

        raw_registers = _mapping(data.get("registers"), "registers")
        registers = {
            str(register_name): RegisterSpec.from_dict(
                str(register_name), register_data
            )
            for register_name, register_data in raw_registers.items()
        }
        missing_read = sorted(_REQUIRED_READ_REGISTERS - set(registers))
        if missing_read:
            raise PLCProfileError(
                "missing required read registers: " + ", ".join(missing_read)
            )
        for register_name in _REQUIRED_READ_REGISTERS:
            if registers[register_name].data_type == "bool":
                raise PLCProfileError(
                    f"registers.{register_name} must use a numeric data type"
                )

        write_policy = WritePolicy.from_dict(data.get("write_policy", {}))
        if write_policy.mode != "disabled":
            missing_write = sorted(_REQUIRED_WRITE_REGISTERS - set(registers))
            if missing_write:
                raise PLCProfileError(
                    "write mode requires registers: " + ", ".join(missing_write)
                )
            for register_name in _REQUIRED_WRITE_REGISTERS - {"ack_sequence"}:
                if not registers[register_name].writable:
                    raise PLCProfileError(
                        f"registers.{register_name} must use the holding table"
                    )
            for register_name in (
                "candidate_kp",
                "candidate_integral",
                "candidate_derivative",
            ):
                if registers[register_name].data_type == "bool":
                    raise PLCProfileError(
                        f"registers.{register_name} must use a numeric data type"
                    )
            apply_sequence = registers["apply_sequence"]
            ack_sequence = registers["ack_sequence"]
            if (
                apply_sequence.data_type not in {"uint16", "uint32"}
                or ack_sequence.data_type != apply_sequence.data_type
            ):
                raise PLCProfileError(
                    "apply_sequence and ack_sequence must use the same uint16 "
                    "or uint32 data type"
                )
            for register_name, spec in (
                ("apply_sequence", apply_sequence),
                ("ack_sequence", ack_sequence),
            ):
                if spec.scale != 1.0 or spec.offset != 0.0:
                    raise PLCProfileError(
                        f"registers.{register_name} must use scale=1 and offset=0"
                    )
            required_flags = []
            if write_policy.require_auto_mode:
                required_flags.append("auto_mode")
            if write_policy.require_tune_enable:
                required_flags.append("tune_enable")
            if write_policy.require_interlock:
                required_flags.append("interlock_ok")
            missing_flags = [name for name in required_flags if name not in registers]
            if missing_flags:
                raise PLCProfileError(
                    "write safety requires registers: " + ", ".join(missing_flags)
                )

        occupied: Dict[tuple[str, int], str] = {}
        for register_name, spec in registers.items():
            for address in range(spec.address, spec.address + spec.word_count):
                key = (spec.table, address)
                if key in occupied:
                    raise PLCProfileError(
                        f"registers.{register_name} overlaps registers.{occupied[key]} "
                        f"at {spec.table} address {address}"
                    )
                occupied[key] = register_name

        raw_limits = _mapping(
            data.get("canonical_pid_limits"), "canonical_pid_limits"
        )
        limits: Dict[str, Dict[str, float]] = {}
        for gain in ("p", "i", "d"):
            gain_data = _mapping(raw_limits.get(gain), f"canonical_pid_limits.{gain}")
            minimum = _finite_float(
                gain_data.get("min"), f"canonical_pid_limits.{gain}.min"
            )
            maximum = _finite_float(
                gain_data.get("max"), f"canonical_pid_limits.{gain}.max"
            )
            if minimum < 0.0 or maximum < minimum:
                raise PLCProfileError(
                    f"canonical_pid_limits.{gain} must satisfy 0 <= min <= max"
                )
            limits[gain] = {"min": minimum, "max": maximum}

        process = dict(_mapping(data.get("process", {}), "process"))
        return cls(
            name=name,
            connection=ConnectionSettings.from_dict(data.get("connection")),
            acquisition=AcquisitionSettings.from_dict(data.get("acquisition", {})),
            pid_semantics=PIDSemantics.from_dict(data.get("pid_semantics")),
            write_policy=write_policy,
            registers=registers,
            canonical_pid_limits=limits,
            process=process,
        )


def load_plc_profile(path: str | Path) -> PLCProfile:
    profile_path = Path(path)
    try:
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PLCProfileError(f"could not load PLC profile {profile_path}: {exc}") from exc
    return PLCProfile.from_dict(raw)
