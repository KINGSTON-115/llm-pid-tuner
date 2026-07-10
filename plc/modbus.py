from __future__ import annotations

import math
import struct
import time
from typing import Any, Callable, Dict, Iterable, Mapping

from plc.pid_semantics import PIDSemanticsCodec, validate_canonical_pid
from plc.profile import PLCProfile, PLCProfileError, RegisterSpec


class PLCBridgeError(RuntimeError):
    """Raised when PLC communication or validation fails."""


_STRUCT_FORMATS = {
    "uint16": "H",
    "int16": "h",
    "uint32": "I",
    "int32": "i",
    "float32": "f",
}


class ModbusValueCodec:
    """Encode and decode engineering values stored in Modbus registers."""

    @staticmethod
    def decode(registers: Iterable[int], spec: RegisterSpec) -> Any:
        values = [int(value) for value in registers]
        if len(values) != spec.word_count:
            raise PLCBridgeError(
                f"expected {spec.word_count} words, received {len(values)}"
            )
        if any(value < 0 or value > 0xFFFF for value in values):
            raise PLCBridgeError("Modbus register values must be between 0 and 65535")

        words = [value.to_bytes(2, byteorder="big") for value in values]
        if spec.byte_order == "little":
            words = [word[::-1] for word in words]
        if spec.word_order == "little" and len(words) > 1:
            words.reverse()
        raw = b"".join(words)

        if spec.data_type == "bool":
            return bool(struct.unpack(">H", raw)[0])
        value = struct.unpack(">" + _STRUCT_FORMATS[spec.data_type], raw)[0]
        engineering_value = float(value) * spec.scale + spec.offset
        if not math.isfinite(engineering_value):
            raise PLCBridgeError("decoded Modbus value is not finite")
        return engineering_value

    @staticmethod
    def encode(value: Any, spec: RegisterSpec) -> list[int]:
        if spec.data_type == "bool":
            raw = struct.pack(">H", 1 if bool(value) else 0)
        else:
            try:
                engineering_value = float(value)
            except (TypeError, ValueError) as exc:
                raise PLCBridgeError("value must be numeric") from exc
            if not math.isfinite(engineering_value):
                raise PLCBridgeError("value must be finite")
            native_value = (engineering_value - spec.offset) / spec.scale
            if spec.data_type.startswith("float"):
                packed_value: Any = native_value
            else:
                packed_value = int(round(native_value))
            try:
                raw = struct.pack(">" + _STRUCT_FORMATS[spec.data_type], packed_value)
            except struct.error as exc:
                raise PLCBridgeError(
                    f"value {engineering_value:g} does not fit {spec.data_type}"
                ) from exc

        words = [raw[index : index + 2] for index in range(0, len(raw), 2)]
        if spec.word_order == "little" and len(words) > 1:
            words.reverse()
        if spec.byte_order == "little":
            words = [word[::-1] for word in words]
        return [int.from_bytes(word, byteorder="big") for word in words]


class ModbusPLCBridge:
    """Profile-driven Modbus TCP bridge with staged PID write-back."""

    def __init__(
        self,
        profile: PLCProfile,
        *,
        client: Any = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.profile = profile
        self.client = client
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self.pid_codec = PIDSemanticsCodec(profile.pid_semantics)
        self.last_error = ""
        self.connected = False
        self._write_armed = profile.write_policy.mode == "auto"

    def connect(self) -> bool:
        self.last_error = ""
        try:
            if self.client is None:
                try:
                    from pymodbus.client import ModbusTcpClient
                except ImportError as exc:
                    raise PLCBridgeError(
                        "pymodbus is required for a real PLC connection; "
                        "install plc/requirements.txt"
                    ) from exc
                settings = self.profile.connection
                self.client = ModbusTcpClient(
                    settings.host,
                    port=settings.port,
                    timeout=settings.timeout_sec,
                )
            result = self.client.connect()
            self.connected = bool(True if result is None else result)
            if not self.connected:
                raise PLCBridgeError("Modbus TCP connection was rejected")
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self.connected = False
            return False

    def disconnect(self) -> None:
        if self.client is not None and hasattr(self.client, "close"):
            try:
                self.client.close()
            finally:
                self.connected = False

    def _ensure_client(self) -> Any:
        if self.client is None or not self.connected:
            raise PLCBridgeError("PLC is not connected")
        return self.client

    def _call_read(self, method_name: str, spec: RegisterSpec) -> Any:
        client = self._ensure_client()
        method = getattr(client, method_name)
        kwargs = {"count": spec.word_count}
        device_id = self.profile.connection.device_id
        for keyword in ("device_id", "slave", "unit"):
            try:
                return method(spec.address, **kwargs, **{keyword: device_id})
            except TypeError:
                continue
        raise PLCBridgeError(f"{method_name} does not accept a device id argument")

    def _call_write(self, spec: RegisterSpec, values: list[int]) -> Any:
        client = self._ensure_client()
        method = getattr(client, "write_registers")
        device_id = self.profile.connection.device_id
        for keyword in ("device_id", "slave", "unit"):
            try:
                return method(spec.address, values, **{keyword: device_id})
            except TypeError:
                continue
        raise PLCBridgeError("write_registers does not accept a device id argument")

    @staticmethod
    def _raise_for_response(response: Any, operation: str) -> None:
        if response is None:
            raise PLCBridgeError(f"{operation} returned no response")
        is_error = getattr(response, "isError", None)
        if callable(is_error) and is_error():
            raise PLCBridgeError(f"{operation} failed: {response}")

    def read_value(self, register_name: str) -> Any:
        try:
            spec = self.profile.registers[register_name]
        except KeyError as exc:
            raise PLCBridgeError(f"profile has no register named {register_name}") from exc
        method_name = (
            "read_holding_registers"
            if spec.table == "holding"
            else "read_input_registers"
        )
        response = self._call_read(method_name, spec)
        self._raise_for_response(response, f"read {register_name}")
        registers = getattr(response, "registers", None)
        if registers is None:
            raise PLCBridgeError(f"read {register_name} returned no registers")
        return ModbusValueCodec.decode(registers, spec)

    def write_value(self, register_name: str, value: Any) -> None:
        try:
            spec = self.profile.registers[register_name]
        except KeyError as exc:
            raise PLCBridgeError(f"profile has no register named {register_name}") from exc
        if not spec.writable:
            raise PLCBridgeError(f"register {register_name} is not writable")
        response = self._call_write(spec, ModbusValueCodec.encode(value, spec))
        self._raise_for_response(response, f"write {register_name}")

    def read_active_pid(self) -> Dict[str, float]:
        return self.pid_codec.native_to_canonical(
            self.read_value("kp"),
            self.read_value("integral"),
            self.read_value("derivative"),
        )

    def read_snapshot(self) -> Dict[str, Any]:
        pv = float(self.read_value("pv"))
        setpoint = float(self.read_value("setpoint"))
        output = float(self.read_value("output"))
        pid = self.read_active_pid()
        if "timestamp" in self.profile.registers:
            timestamp = float(self.read_value("timestamp"))
        else:
            timestamp = self.monotonic_fn() * 1000.0
        sample: Dict[str, Any] = {
            "timestamp": timestamp,
            "setpoint": setpoint,
            "input": pv,
            "pwm": output,
            "control_output": output,
            "error": setpoint - pv,
            **pid,
        }
        for register_name in (
            "sample_counter",
            "quality",
            "auto_mode",
            "tune_enable",
            "interlock_ok",
            "apply_status",
        ):
            if register_name in self.profile.registers:
                sample[f"plc_{register_name}"] = self.read_value(register_name)
        return sample

    def arm_writes_once(self) -> bool:
        self.last_error = ""
        if self.profile.write_policy.mode != "confirm":
            self.last_error = (
                "one-shot arming is available only when write_policy.mode is confirm"
            )
            return False
        self._write_armed = True
        return True

    def _require_flag(self, register_name: str, message: str) -> None:
        if not bool(self.read_value(register_name)):
            raise PLCBridgeError(message)

    def _validate_write_state(self) -> None:
        policy = self.profile.write_policy
        if policy.mode == "disabled":
            raise PLCBridgeError("PID write-back is disabled by the PLC profile")
        if policy.mode == "confirm" and not self._write_armed:
            raise PLCBridgeError(
                "PID write-back requires arm_writes_once() before this update"
            )
        if policy.require_auto_mode:
            self._require_flag("auto_mode", "PLC PID controller is not in auto mode")
        if policy.require_tune_enable:
            self._require_flag("tune_enable", "PLC TuneEnable is not active")
        if policy.require_interlock:
            self._require_flag("interlock_ok", "PLC safety interlock is not satisfied")

    @staticmethod
    def _pid_matches(
        actual: Mapping[str, float], expected: Mapping[str, float]
    ) -> bool:
        for gain in ("p", "i", "d"):
            target = float(expected[gain])
            tolerance = max(1e-5, abs(target) * 1e-4)
            if abs(float(actual[gain]) - target) > tolerance:
                return False
        return True

    def apply_pid(self, candidate_pid: Mapping[str, Any]) -> bool:
        self.last_error = ""
        policy = self.profile.write_policy
        try:
            candidate = validate_canonical_pid(
                candidate_pid, self.profile.canonical_pid_limits
            )
            self._validate_write_state()
            native = self.pid_codec.canonical_to_native(candidate)
            self.write_value("candidate_kp", native["kp"])
            self.write_value("candidate_integral", native["integral"])
            self.write_value("candidate_derivative", native["derivative"])

            sequence_spec = self.profile.registers["apply_sequence"]
            sequence_mask = (1 << (sequence_spec.word_count * 16)) - 1
            current_sequence = int(self.read_value("apply_sequence")) & sequence_mask
            next_sequence = (current_sequence + 1) & sequence_mask
            if next_sequence == 0:
                next_sequence = 1
            self.write_value("apply_sequence", next_sequence)

            deadline = self.monotonic_fn() + policy.ack_timeout_sec
            while True:
                ack_sequence = int(self.read_value("ack_sequence")) & sequence_mask
                if ack_sequence == next_sequence:
                    break
                if self.monotonic_fn() >= deadline:
                    raise PLCBridgeError(
                        f"PLC did not acknowledge apply sequence {next_sequence}"
                    )
                self.sleep_fn(policy.ack_poll_interval_sec)

            if "apply_status" in self.profile.registers:
                apply_status = int(self.read_value("apply_status"))
                if apply_status != 0:
                    raise PLCBridgeError(
                        f"PLC rejected PID parameters with status {apply_status}"
                    )
            actual = self.read_active_pid()
            if not self._pid_matches(actual, candidate):
                raise PLCBridgeError(
                    f"PLC PID readback mismatch: expected {candidate}, got {actual}"
                )
            return True
        except (PLCBridgeError, PLCProfileError) as exc:
            self.last_error = str(exc)
            return False
        except Exception as exc:
            self.last_error = f"PLC PID communication failed: {exc}"
            return False
        finally:
            if policy.mode == "confirm":
                self._write_armed = False
