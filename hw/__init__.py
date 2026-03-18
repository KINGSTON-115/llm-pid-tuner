"""hardware package - serial hardware bridge helpers."""

from hw.bridge import DEMO_SERIAL_PORT, SerialBridge, safe_pause, select_serial_port

__all__ = [
    "DEMO_SERIAL_PORT",
    "SerialBridge",
    "select_serial_port",
    "safe_pause",
]
