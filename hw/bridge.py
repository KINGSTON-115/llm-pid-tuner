#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hw/bridge.py - 串口通信桥接

提供 SerialBridge 类及串口选择工具函数。
"""

import serial
import serial.tools.list_ports


class SerialBridge:
    def __init__(self, port: str, baudrate: int):
        self.port     = port
        self.baudrate = baudrate
        self.serial   = None

    def connect(self) -> bool:
        try:
            self.serial = serial.Serial(self.port, self.baudrate, timeout=1)
            print(f"[INFO] Connected to {self.port}")
            return True
        except Exception as e:
            print(f"[ERROR] Connection failed: {e}")
            return False

    def disconnect(self) -> None:
        if self.serial:
            self.serial.close()

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
                print(f"[CMD] Sent: {cmd}")
            except Exception as e:
                print(f"[ERROR] Failed to send command '{cmd}': {e}")

    def parse_data(self, line: str):
        if not line or line.startswith("#"):
            return None
        parts = line.split(",")
        if len(parts) >= 5:
            try:
                return {
                    "timestamp": float(parts[0]),
                    "setpoint" : float(parts[1]),
                    "input"    : float(parts[2]),
                    "pwm"      : float(parts[3]),
                    "error"    : float(parts[4]),
                    "p"        : float(parts[5]) if len(parts) > 5 else 1.0,
                    "i"        : float(parts[6]) if len(parts) > 6 else 0.1,
                    "d"        : float(parts[7]) if len(parts) > 7 else 0.05,
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
    """交互式选择串口"""
    print("\n[INFO] 正在扫描可用串口...")
    ports = list(serial.tools.list_ports.comports())

    if not ports:
        print("[WARN] 未发现任何串口设备！")
        return input("请输入串口号 (例如 COM3 或 /dev/ttyUSB0): ").strip()

    print(f"发现 {len(ports)} 个设备:")
    for i, p in enumerate(ports):
        print(f"  [{i + 1}] {p.device} - {p.description}")

    while True:
        choice = (
            input(f"\n请选择序号 (1-{len(ports)}) 或输入 'm' 手动指定: ")
            .strip()
            .lower()
        )
        if choice == "m":
            return input("请输入串口号: ").strip()

        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(ports):
                return ports[idx].device

        print("[ERROR] 输入无效，请重试。")
