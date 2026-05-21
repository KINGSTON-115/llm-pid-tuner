#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hw/bridge.py - serial bridge helpers for real hardware and demo mode.
"""

from __future__ import annotations

import math
import re
import struct
import time
from typing import Any, Dict, Optional

import serial
import serial.tools.list_ports

from hw.profiles import (
    DEFAULT_HARDWARE_PROFILE,
    build_profile_commands,
    get_hardware_board_family,
    get_hardware_profile_info,
    get_openmv_image_center,
    normalize_hardware_profile,
)


DEMO_SERIAL_PORT = "COM_FAKE"
DEMO_SERIAL_PORT_ALIASES = {
    DEMO_SERIAL_PORT,
    "FAKE",
    "DEMO",
    "DEMO_HW",
    "VIRTUAL",
}

_DATAVISION_FRAME_HEADER = struct.pack("<I", 0x59485A53)
_OPENMV_COMPACT_RE = re.compile(r"^T:(-?\d+),(-?\d+)$", re.IGNORECASE)
_OPENMV_TARGET_RE = re.compile(
    r"^TARGET:(-?\d+),(-?\d+),CENTER:(-?\d+),(-?\d+),OFFSET:(-?\d+),(-?\d+)$",
    re.IGNORECASE,
)
_OPENMV_NO_TARGET_RE = re.compile(
    r"^NO_TARGET(?:,CENTER:(-?\d+),(-?\d+))?$",
    re.IGNORECASE,
)
_STM32_TARGET_RE = re.compile(r"^target\s*=\s*\(([-\d]+),\s*([-\d]+)\)$", re.IGNORECASE)
_STM32_SERVO_RE = re.compile(r"^servo\.(x|y|delta)\s*=\s*([-\d]+(?:\.\d+)?)$", re.IGNORECASE)
_STM32_PID_PAIR_RE = re.compile(
    r"^pid\.(x|y)\s*=\s*([-\d]+(?:\.\d+)?)\s+([-\d]+(?:\.\d+)?)\s+([-\d]+(?:\.\d+)?)$",
    re.IGNORECASE,
)
_STM32_PID_GAIN_RE = re.compile(
    r"^pid\.(x|y)\.(kp|ki|kd)\s*=\s*([-\d]+(?:\.\d+)?)$",
    re.IGNORECASE,
)
_STM32_RECT_RE = re.compile(r"^rect\.detect\s*=\s*(\d+)$", re.IGNORECASE)
_STM32_IMU_RE = re.compile(
    r"^imu\.(roll|pitch|yaw)\s*=\s*([-\d]+(?:\.\d+)?)$",
    re.IGNORECASE,
)


def _is_demo_port(port: Optional[str]) -> bool:
    return str(port or "").strip().upper() in DEMO_SERIAL_PORT_ALIASES


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


class _DemoSerialDevice:
    """In-process fake serial device so the hardware TUI can be previewed."""

    _set_pid_re = re.compile(
        r"SET\s+P:(?P<p>-?\d+(?:\.\d+)?)\s+I:(?P<i>-?\d+(?:\.\d+)?)\s+D:(?P<d>-?\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )
    _set_pid_2_re = re.compile(
        r"SET2\s+P:(?P<p>-?\d+(?:\.\d+)?)\s+I:(?P<i>-?\d+(?:\.\d+)?)\s+D:(?P<d>-?\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )

    def __init__(self) -> None:
        from sim.model import HeatingSimulator

        self.is_open = True
        self._sim = HeatingSimulator(random_seed=7)
        self._last_command = ""
        self._secondary_pid: Optional[Dict[str, float]] = None

    def close(self) -> None:
        self.is_open = False

    def readline(self) -> bytes:
        if not self.is_open:
            return b""

        self._sim.compute_pid()
        self._sim.update()
        data = self._sim.get_data()
        # Slow the preview down a bit so the TUI remains readable.
        time.sleep(0.05)
        line = (
            f"{data['timestamp']:.0f},{data['setpoint']:.3f},{data['input']:.3f},"
            f"{data['pwm']:.3f},{data['error']:.3f},{data['p']:.4f},"
            f"{data['i']:.4f},{data['d']:.4f}\n"
        )
        if self._secondary_pid is not None:
            line = line.rstrip("\n") + (
                f",{self._secondary_pid['p']:.4f},{self._secondary_pid['i']:.4f},"
                f"{self._secondary_pid['d']:.4f}\n"
            )
        return line.encode("utf-8")

    def write(self, payload: bytes) -> None:
        if not self.is_open:
            return

        command = payload.decode("utf-8", errors="ignore").strip()
        self._last_command = command
        if not command:
            return
        if command.upper() == "STATUS":
            return

        match = self._set_pid_re.fullmatch(command)
        if match:
            self._sim.set_pid(
                float(match.group("p")),
                float(match.group("i")),
                float(match.group("d")),
            )
            return

        match2 = self._set_pid_2_re.fullmatch(command)
        if match2:
            self._secondary_pid = {
                "p": float(match2.group("p")),
                "i": float(match2.group("i")),
                "d": float(match2.group("d")),
            }


class SerialBridge:
    def __init__(self, port: str, baudrate: int, emit_console: bool = True):
        self.port = port
        self.baudrate = baudrate
        self.serial = None
        self.emit_console = emit_console
        self.last_error = ""
        self.hardware_profile = DEFAULT_HARDWARE_PROFILE
        self._current_pid: Dict[str, float] = {"p": 1.0, "i": 0.1, "d": 0.05}
        self._secondary_pid: Optional[Dict[str, float]] = None
        self._sample_index = 0
        self._datavision_buffer = bytearray()
        self._datavision_pending: Dict[int, Dict[str, float]] = {}

    def connect(self) -> bool:
        try:
            if _is_demo_port(self.port):
                self.serial = _DemoSerialDevice()
                self.last_error = ""
                if self.emit_console:
                    print(f"[INFO] Connected to virtual hardware feed: {DEMO_SERIAL_PORT}")
                return True

            # Set write_timeout so serial writes cannot block forever on
            # problematic adapters or flow-control mismatch.
            self.serial = serial.Serial(
                self.port,
                self.baudrate,
                timeout=1,
                write_timeout=1,
            )
            self.last_error = ""
            if self.emit_console:
                print(f"[INFO] Connected to {self.port}")
            return True
        except Exception as e:
            self.last_error = str(e)
            if self.emit_console:
                print(f"[ERROR] Connection failed: {e}")
            return False

    def disconnect(self) -> None:
        if self.serial:
            self.serial.close()
            self.serial = None

    def _read_datavision_frame(self) -> Optional[bytes]:
        if not self.serial or not self.serial.is_open:
            return None

        try:
            waiting = int(getattr(self.serial, "in_waiting", 0) or 0)
            chunk = self.serial.read(waiting or 1)
        except Exception:
            return None

        if not chunk:
            return None

        self._datavision_buffer.extend(chunk)
        return self._extract_datavision_frame()

    def _extract_datavision_frame(self) -> Optional[bytes]:
        header = _DATAVISION_FRAME_HEADER
        buffer = self._datavision_buffer

        header_index = buffer.find(header)
        if header_index < 0:
            if len(buffer) > len(header) - 1:
                del buffer[: -(len(header) - 1)]
            return None

        if header_index > 0:
            del buffer[:header_index]

        if len(buffer) < 10:
            return None

        frame_len = int(struct.unpack_from("<I", buffer, 5)[0])
        if frame_len < 11:
            del buffer[:1]
            return None

        if len(buffer) < frame_len:
            return None

        frame = bytes(buffer[:frame_len])
        del buffer[:frame_len]
        return frame

    def read_line(self):
        if self.serial and self.serial.is_open:
            try:
                if normalize_hardware_profile(self.hardware_profile) == "mspm0_datavision":
                    return self._read_datavision_frame()
                return self.serial.readline().decode("utf-8", errors="ignore").strip()
            except Exception:
                pass
        return None

    def send_command(self, cmd: str) -> bool:
        if self.serial and self.serial.is_open:
            try:
                self.serial.write(f"{cmd}\n".encode("utf-8"))
                self.last_error = ""
                if self.emit_console:
                    print(f"[CMD] Sent: {cmd}")
                return True
            except Exception as e:
                self.last_error = str(e)
                if self.emit_console:
                    print(f"[ERROR] Failed to send command '{cmd}': {e}")
                return False
        self.last_error = "serial port is not connected"
        return False

    def send_profile_command(
        self,
        kind: str,
        primary_pid: Optional[Dict[str, float]] = None,
        secondary_pid: Optional[Dict[str, float]] = None,
    ) -> bool:
        commands = build_profile_commands(
            self.hardware_profile,
            kind,
            primary_pid=primary_pid,
            secondary_pid=secondary_pid,
        )
        if not commands:
            self.last_error = (
                f"{normalize_hardware_profile(self.hardware_profile)} does not expose {kind} commands"
            )
            return False

        for cmd in commands:
            if not self.send_command(cmd):
                return False
        return True

    def _make_base_sample(self, sample_kind: str) -> Dict[str, Any]:
        profile = normalize_hardware_profile(self.hardware_profile)
        info = get_hardware_profile_info(profile)
        sample: Dict[str, Any] = {
            "hardware_profile": profile,
            "board_family": info.get("board_family", "generic_serial"),
            "sample_kind": sample_kind,
            "timestamp": float(self._sample_index),
            "setpoint": 0.0,
            "input": 0.0,
            "pwm": 0.0,
            "error": 0.0,
            "p": self._current_pid["p"],
            "i": self._current_pid["i"],
            "d": self._current_pid["d"],
        }
        if self._secondary_pid is not None:
            sample.update(
                {
                    "p2": self._secondary_pid["p"],
                    "i2": self._secondary_pid["i"],
                    "d2": self._secondary_pid["d"],
                }
            )
        return sample

    def _update_pid_cache(self, sample: Dict[str, Any]) -> None:
        if all(key in sample for key in ("p", "i", "d")):
            self._current_pid = {
                "p": _safe_float(sample.get("p"), self._current_pid["p"]),
                "i": _safe_float(sample.get("i"), self._current_pid["i"]),
                "d": _safe_float(sample.get("d"), self._current_pid["d"]),
            }
        if all(key in sample for key in ("p2", "i2", "d2")):
            self._secondary_pid = {
                "p": _safe_float(sample.get("p2")),
                "i": _safe_float(sample.get("i2")),
                "d": _safe_float(sample.get("d2")),
            }

    def _parse_csv_sample(self, text: str) -> Optional[Dict[str, Any]]:
        parts = [part.strip() for part in text.split(",")]
        if len(parts) < 5:
            return None

        try:
            sample = self._make_base_sample("csv")
            sample.update(
                {
                    "timestamp": _safe_float(parts[0]),
                    "setpoint": _safe_float(parts[1]),
                    "input": _safe_float(parts[2]),
                    "pwm": _safe_float(parts[3]),
                    "error": _safe_float(parts[4]),
                    "p": _safe_float(parts[5], sample["p"]) if len(parts) > 5 else sample["p"],
                    "i": _safe_float(parts[6], sample["i"]) if len(parts) > 6 else sample["i"],
                    "d": _safe_float(parts[7], sample["d"]) if len(parts) > 7 else sample["d"],
                }
            )
            if len(parts) > 10:
                sample.update(
                    {
                        "p2": _safe_float(parts[8]),
                        "i2": _safe_float(parts[9]),
                        "d2": _safe_float(parts[10]),
                    }
                )
            self._sample_index += 1
            self._update_pid_cache(sample)
            return sample
        except Exception:
            return None

    def _parse_openmv_sample(self, text: str) -> Optional[Dict[str, Any]]:
        profile = normalize_hardware_profile(self.hardware_profile)
        if profile != "stm32f407_openmv":
            return None

        center = get_openmv_image_center(profile) or (80, 60)
        if text.upper() == "N":
            sample = self._make_base_sample("openmv_no_target")
            sample.update(
                {
                    "setpoint": 0.0,
                    "input": 0.0,
                    "pwm": 0.0,
                    "error": 0.0,
                    "image_center_x": center[0],
                    "image_center_y": center[1],
                    "has_target": False,
                    "source_protocol": "openmv_no_target",
                }
            )
            self._sample_index += 1
            return sample

        compact_match = _OPENMV_COMPACT_RE.match(text)
        if compact_match:
            target_x = int(compact_match.group(1))
            target_y = int(compact_match.group(2))
            offset_x = target_x - center[0]
            offset_y = target_y - center[1]
            error = math.hypot(offset_x, offset_y)
            sample = self._make_base_sample("openmv_target")
            sample.update(
                {
                    "setpoint": 0.0,
                    "input": error,
                    "error": error,
                    "target_x": target_x,
                    "target_y": target_y,
                    "image_center_x": center[0],
                    "image_center_y": center[1],
                    "offset_x": offset_x,
                    "offset_y": offset_y,
                    "has_target": True,
                    "source_protocol": "openmv_compact",
                }
            )
            self._sample_index += 1
            return sample

        target_match = _OPENMV_TARGET_RE.match(text)
        if target_match:
            target_x = int(target_match.group(1))
            target_y = int(target_match.group(2))
            center_x = int(target_match.group(3))
            center_y = int(target_match.group(4))
            offset_x = int(target_match.group(5))
            offset_y = int(target_match.group(6))
            error = math.hypot(offset_x, offset_y)
            sample = self._make_base_sample("openmv_target")
            sample.update(
                {
                    "setpoint": 0.0,
                    "input": error,
                    "error": error,
                    "target_x": target_x,
                    "target_y": target_y,
                    "image_center_x": center_x,
                    "image_center_y": center_y,
                    "offset_x": offset_x,
                    "offset_y": offset_y,
                    "has_target": True,
                    "source_protocol": "openmv_expanded",
                }
            )
            self._sample_index += 1
            return sample

        no_target_match = _OPENMV_NO_TARGET_RE.match(text)
        if no_target_match:
            sample = self._make_base_sample("openmv_no_target")
            if no_target_match.group(1) and no_target_match.group(2):
                sample["image_center_x"] = int(no_target_match.group(1))
                sample["image_center_y"] = int(no_target_match.group(2))
            else:
                sample["image_center_x"] = center[0]
                sample["image_center_y"] = center[1]
            sample.update(
                {
                    "setpoint": 0.0,
                    "input": 0.0,
                    "pwm": 0.0,
                    "error": 0.0,
                    "has_target": False,
                    "source_protocol": "openmv_no_target",
                }
            )
            self._sample_index += 1
            return sample

        return None

    def _finalize_stm32_snapshot(self, sample: Dict[str, Any]) -> Dict[str, Any]:
        setpoint = sample.get("target_x")
        input_value = sample.get("servo_x")
        if setpoint is None and sample.get("target_y") is not None:
            setpoint = sample.get("target_y")
        if input_value is None and sample.get("servo_y") is not None:
            input_value = sample.get("servo_y")

        sample["setpoint"] = _safe_float(setpoint, sample.get("setpoint", 0.0))
        sample["input"] = _safe_float(input_value, sample.get("input", 0.0))
        sample["error"] = sample["setpoint"] - sample["input"]
        sample["pwm"] = _safe_float(sample.get("servo_delta", sample.get("pwm", 0.0)))
        sample.setdefault("source_protocol", "stm32_status")
        return sample

    def _parse_stm32_sample(self, text: str) -> Optional[Dict[str, Any]]:
        profile = normalize_hardware_profile(self.hardware_profile)
        if profile != "stm32f407_openmv":
            return None

        stripped = text.strip()
        if not stripped or stripped.lower() in {"status:", "current config:"}:
            return None

        sample: Optional[Dict[str, Any]] = None

        target_match = _STM32_TARGET_RE.match(stripped)
        if target_match:
            sample = sample or self._make_base_sample("status_snapshot")
            sample["target_x"] = int(target_match.group(1))
            sample["target_y"] = int(target_match.group(2))

        servo_match = _STM32_SERVO_RE.match(stripped)
        if servo_match:
            sample = sample or self._make_base_sample("status_snapshot")
            axis = servo_match.group(1).lower()
            value = _safe_float(servo_match.group(2))
            if axis == "x":
                sample["servo_x"] = value
            elif axis == "y":
                sample["servo_y"] = value
            else:
                sample["servo_delta"] = value

        pid_pair_match = _STM32_PID_PAIR_RE.match(stripped)
        if pid_pair_match:
            sample = sample or self._make_base_sample("status_snapshot")
            axis = pid_pair_match.group(1).lower()
            gains = {
                "p": _safe_float(pid_pair_match.group(2)),
                "i": _safe_float(pid_pair_match.group(3)),
                "d": _safe_float(pid_pair_match.group(4)),
            }
            if axis == "x":
                sample.update(gains)
            else:
                sample.update({"p2": gains["p"], "i2": gains["i"], "d2": gains["d"]})

        pid_gain_match = _STM32_PID_GAIN_RE.match(stripped)
        if pid_gain_match:
            sample = sample or self._make_base_sample("status_snapshot")
            axis = pid_gain_match.group(1).lower()
            gain_name = pid_gain_match.group(2).lower()
            value = _safe_float(pid_gain_match.group(3))
            key_map = {"kp": "p", "ki": "i", "kd": "d"}
            if axis == "x":
                sample[key_map[gain_name]] = value
            else:
                sample[f"{key_map[gain_name]}2"] = value

        rect_match = _STM32_RECT_RE.match(stripped)
        if rect_match:
            sample = sample or self._make_base_sample("status_snapshot")
            sample["rect_detected"] = bool(int(rect_match.group(1)))

        imu_match = _STM32_IMU_RE.match(stripped)
        if imu_match:
            sample = sample or self._make_base_sample("status_snapshot")
            sample[f"imu_{imu_match.group(1).lower()}"] = _safe_float(imu_match.group(2))

        if sample is None:
            return None

        sample["source_protocol"] = "stm32_status"
        sample["sample_kind"] = "status_snapshot"
        if "target_x" in sample or "servo_x" in sample or "target_y" in sample or "servo_y" in sample:
            sample = self._finalize_stm32_snapshot(sample)
        self._sample_index += 1
        self._update_pid_cache(sample)
        return sample

    def _parse_multiline_text(self, text: str) -> Optional[Dict[str, Any]]:
        combined: Dict[str, Any] = {}
        found = False
        for raw_line in text.replace("\r", "").split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            part = self._parse_openmv_sample(line)
            if part is None:
                part = self._parse_stm32_sample(line)
            if part is None:
                part = self._parse_csv_sample(line)
            if part is None:
                continue
            found = True
            combined.update(part)

        if not found:
            return None

        if combined.get("sample_kind") == "status_snapshot":
            combined = self._finalize_stm32_snapshot(combined)
        self._update_pid_cache(combined)
        return combined

    def _parse_datavision_frame(self, frame: bytes) -> Optional[Dict[str, Any]]:
        if not frame or len(frame) < 15:
            return None

        if frame[:4] != _DATAVISION_FRAME_HEADER:
            return None

        frame_len = struct.unpack_from("<I", frame, 5)[0]
        if len(frame) < frame_len or frame_len < 15:
            return None

        checksum = sum(frame[: frame_len - 1]) & 0xFF
        if checksum != frame[frame_len - 1]:
            return None

        channel = int(frame[4])
        cmd = int(frame[9])
        payload = frame[10 : frame_len - 1]
        if len(payload) < 4:
            return None

        value = struct.unpack_from("<f", payload)[0]
        if cmd == 0x01:
            self._datavision_pending[channel] = {"setpoint": float(value)}
            return None

        if cmd != 0x02:
            return None

        pending = self._datavision_pending.pop(channel, None)
        setpoint = float(pending.get("setpoint", value)) if pending else float(value)
        sample = self._make_base_sample("datavision_pair")
        sample.update(
            {
                "timestamp": float(self._sample_index),
                "setpoint": setpoint,
                "input": float(value),
                "error": float(setpoint - value),
                "pwm": 0.0,
                "channel": channel,
                "source_protocol": "mspm0_datavision",
                "cmd": cmd,
            }
        )
        self._sample_index += 1
        self._update_pid_cache(sample)
        return sample

    def parse_data(self, line: Any):
        if line is None:
            return None

        profile = normalize_hardware_profile(self.hardware_profile)
        if profile == "mspm0_datavision" and isinstance(line, (bytes, bytearray)):
            return self._parse_datavision_frame(bytes(line))

        if isinstance(line, (bytes, bytearray)):
            line = bytes(line).decode("utf-8", errors="ignore")

        text = str(line).strip()
        if not text or text.startswith("#"):
            return None

        if "\n" in text or "\r" in text:
            return self._parse_multiline_text(text)

        if profile == "stm32f407_openmv":
            parsed = self._parse_openmv_sample(text)
            if parsed is not None:
                self._update_pid_cache(parsed)
                return parsed
            parsed = self._parse_stm32_sample(text)
            if parsed is not None:
                return parsed

        if profile == "mspm0_datavision":
            parsed = self._parse_csv_sample(text)
            if parsed is not None:
                return parsed
            return None

        parsed = self._parse_csv_sample(text)
        if parsed is not None:
            return parsed
        parsed = self._parse_openmv_sample(text)
        if parsed is not None:
            return parsed
        parsed = self._parse_stm32_sample(text)
        if parsed is not None:
            return parsed
        return None


def safe_pause(message: str = "按回车键退出...") -> None:
    try:
        input(message)
    except EOFError:
        pass


def select_serial_port() -> str:
    """Interactively choose a serial port, or start the virtual demo feed."""
    print("\n[INFO] 正在扫描可用串口...")
    ports = list(serial.tools.list_ports.comports())

    if not ports:
        print("[WARN] 未发现任何串口设备。")
        choice = input(
            f"输入串口号（例如 COM3），或输入 'd' 进入虚拟硬件演示模式 [{DEMO_SERIAL_PORT}]: "
        ).strip()
        if choice.lower() == "d":
            return DEMO_SERIAL_PORT
        return choice

    print(f"发现 {len(ports)} 个设备:")
    for i, p in enumerate(ports):
        print(f"  [{i + 1}] {p.device} - {p.description}")
    print(f"  [D] 虚拟硬件演示模式 - {DEMO_SERIAL_PORT}")

    while True:
        choice = (
            input(
                f"\n请选择序号 (1-{len(ports)})、输入 'd' 演示，或输入 'm' 手动指定: "
            )
            .strip()
            .lower()
        )
        if choice == "d":
            return DEMO_SERIAL_PORT
        if choice == "m":
            return input("请输入串口号: ").strip()

        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(ports):
                return ports[idx].device

        print("[ERROR] 输入无效，请重试。")
