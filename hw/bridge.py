#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hw/bridge.py - serial bridge helpers for real hardware and demo mode.
"""

from __future__ import annotations

import re
import time

import serial
import serial.tools.list_ports


DEMO_SERIAL_PORT = "COM_FAKE"
DEMO_SERIAL_PORT_ALIASES = {
    DEMO_SERIAL_PORT,
    "FAKE",
    "DEMO",
    "DEMO_HW",
    "VIRTUAL",
}


def _is_demo_port(port: Optional[str]) -> bool:
    return str(port or "").strip().upper() in DEMO_SERIAL_PORT_ALIASES


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

    def connect(self) -> bool:
        try:
            if _is_demo_port(self.port):
                self.serial = _DemoSerialDevice()
                self.last_error = ""
                if self.emit_console:
                    print(f"[INFO] Connected to virtual hardware feed: {DEMO_SERIAL_PORT}")
                return True

            self.serial = serial.Serial(self.port, self.baudrate, timeout=1)
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

    def read_line(self):
        if self.serial and self.serial.is_open:
            try:
                return self.serial.readline().decode("utf-8", errors="ignore").strip()
            except Exception:
                pass
        return None

    def send_command(self, cmd: str) -> None:
        if self.serial and self.serial.is_open:
            try:
                self.serial.write(f"{cmd}\n".encode("utf-8"))
                self.last_error = ""
                if self.emit_console:
                    print(f"[CMD] Sent: {cmd}")
            except Exception as e:
                self.last_error = str(e)
                if self.emit_console:
                    print(f"[ERROR] Failed to send command '{cmd}': {e}")

    def parse_data(self, line: str):
        if not line or line.startswith("#"):
            return None
        parts = line.split(",")
        if len(parts) >= 5:
            try:
                return {
                    "timestamp": float(parts[0]),
                    "setpoint": float(parts[1]),
                    "input": float(parts[2]),
                    "pwm": float(parts[3]),
                    "error": float(parts[4]),
                    "p": float(parts[5]) if len(parts) > 5 else 1.0,
                    "i": float(parts[6]) if len(parts) > 6 else 0.1,
                    "d": float(parts[7]) if len(parts) > 7 else 0.05,
                    **(
                        {
                            "p2": float(parts[8]),
                            "i2": float(parts[9]),
                            "d2": float(parts[10]),
                        }
                        if len(parts) > 10
                        else {}
                    ),
                }
            except Exception:
                pass
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
