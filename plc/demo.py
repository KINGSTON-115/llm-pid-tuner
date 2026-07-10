from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from plc.modbus import ModbusPLCBridge, ModbusValueCodec
from plc.profile import PLCProfile, RegisterSpec, load_plc_profile


class MemoryModbusResponse:
    def __init__(self, registers: list[int] | None = None, error: str = ""):
        self.registers = registers or []
        self.error = error

    def isError(self) -> bool:
        return bool(self.error)

    def __str__(self) -> str:
        return self.error or f"MemoryModbusResponse({self.registers!r})"


class MemoryModbusClient:
    """In-memory Modbus client used by the demo and unit tests."""

    def __init__(self, profile: PLCProfile):
        self.profile = profile
        self.registers: dict[int, int] = {}
        self.connected = False
        self._seed_defaults()

    def connect(self) -> bool:
        self.connected = True
        return True

    def close(self) -> None:
        self.connected = False

    def _read(self, address: int, count: int) -> MemoryModbusResponse:
        if not self.connected:
            return MemoryModbusResponse(error="not connected")
        return MemoryModbusResponse(
            [self.registers.get(address + offset, 0) for offset in range(count)]
        )

    def read_holding_registers(
        self, address: int, *, count: int = 1, device_id: int = 1
    ) -> MemoryModbusResponse:
        return self._read(address, count)

    def read_input_registers(
        self, address: int, *, count: int = 1, device_id: int = 1
    ) -> MemoryModbusResponse:
        return self._read(address, count)

    def write_registers(
        self, address: int, values: list[int], *, device_id: int = 1
    ) -> MemoryModbusResponse:
        if not self.connected:
            return MemoryModbusResponse(error="not connected")
        for offset, value in enumerate(values):
            self.registers[address + offset] = int(value) & 0xFFFF
        apply_spec = self.profile.registers.get("apply_sequence")
        if apply_spec is not None and address == apply_spec.address:
            self._apply_staged_pid()
        return MemoryModbusResponse([])

    def _raw_words(self, spec: RegisterSpec) -> list[int]:
        return [
            self.registers.get(spec.address + offset, 0)
            for offset in range(spec.word_count)
        ]

    def _copy_register(self, source_name: str, target_name: str) -> None:
        source = self.profile.registers[source_name]
        target = self.profile.registers[target_name]
        words = self._raw_words(source)
        for offset, word in enumerate(words):
            self.registers[target.address + offset] = word

    def _apply_staged_pid(self) -> None:
        self._copy_register("candidate_kp", "kp")
        self._copy_register("candidate_integral", "integral")
        self._copy_register("candidate_derivative", "derivative")
        apply_spec = self.profile.registers["apply_sequence"]
        ack_spec = self.profile.registers["ack_sequence"]
        sequence = ModbusValueCodec.decode(self._raw_words(apply_spec), apply_spec)
        for offset, word in enumerate(ModbusValueCodec.encode(sequence, ack_spec)):
            self.registers[ack_spec.address + offset] = word
        status_spec = self.profile.registers.get("apply_status")
        if status_spec is not None:
            for offset, word in enumerate(ModbusValueCodec.encode(0, status_spec)):
                self.registers[status_spec.address + offset] = word

    def set_engineering_value(self, name: str, value: Any) -> None:
        spec = self.profile.registers[name]
        for offset, word in enumerate(ModbusValueCodec.encode(value, spec)):
            self.registers[spec.address + offset] = word

    def _seed_defaults(self) -> None:
        defaults = {
            "pv": 42.0,
            "setpoint": 50.0,
            "output": 35.0,
            "kp": 2.0,
            "integral": 10.0,
            "derivative": 0.0,
            "auto_mode": True,
            "tune_enable": True,
            "interlock_ok": True,
            "quality": True,
            "sample_counter": 1,
            "apply_sequence": 0,
            "ack_sequence": 0,
            "apply_status": 0,
            "candidate_kp": 2.0,
            "candidate_integral": 10.0,
            "candidate_derivative": 0.0,
        }
        for name, value in defaults.items():
            if name in self.profile.registers:
                self.set_engineering_value(name, value)


def build_demo_bridge() -> ModbusPLCBridge:
    profile_path = Path(__file__).with_name("example_profile.json")
    profile = load_plc_profile(profile_path)
    client = MemoryModbusClient(profile)
    return ModbusPLCBridge(profile, client=client, sleep_fn=lambda _seconds: None)


def main() -> int:
    bridge = build_demo_bridge()
    if not bridge.connect():
        print(f"connect failed: {bridge.last_error}")
        return 1
    try:
        print("Initial snapshot:")
        print(json.dumps(bridge.read_snapshot(), indent=2, ensure_ascii=False))

        candidate = {"p": 2.5, "i": 0.25, "d": 0.5}
        if bridge.apply_pid(candidate):
            print("Unexpected: confirm-mode write succeeded without arming")
            return 2
        print(f"Unarmed write rejected: {bridge.last_error}")

        if not bridge.arm_writes_once() or not bridge.apply_pid(candidate):
            print(f"staged write failed: {bridge.last_error}")
            return 3
        print("PID readback after acknowledged write:")
        print(json.dumps(bridge.read_active_pid(), indent=2, ensure_ascii=False))
        return 0
    finally:
        bridge.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
