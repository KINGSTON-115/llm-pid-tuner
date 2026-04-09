import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent))

from hw.bridge import DEMO_SERIAL_PORT, SerialBridge, select_serial_port


class DemoSerialBridgeTests(unittest.TestCase):
    def test_demo_port_streams_parseable_hardware_data(self):
        bridge = SerialBridge(DEMO_SERIAL_PORT, 115200, emit_console=False)

        self.assertTrue(bridge.connect())
        first_line = bridge.read_line()
        first_data = bridge.parse_data(first_line)

        bridge.send_command("SET P:2.5 I:0.4 D:0.1")
        second_line = bridge.read_line()
        second_data = bridge.parse_data(second_line)
        bridge.disconnect()

        self.assertIsNotNone(first_data)
        self.assertIsNotNone(second_data)
        self.assertAlmostEqual(second_data["p"], 2.5, places=3)
        self.assertAlmostEqual(second_data["i"], 0.4, places=3)
        self.assertAlmostEqual(second_data["d"], 0.1, places=3)
        self.assertGreaterEqual(second_data["timestamp"], first_data["timestamp"])

    def test_demo_port_supports_set2_and_emits_secondary_pid_fields(self):
        bridge = SerialBridge(DEMO_SERIAL_PORT, 115200, emit_console=False)

        self.assertTrue(bridge.connect())
        bridge.send_command("SET2 P:3.5 I:0.7 D:0.2")
        line = bridge.read_line()
        data = bridge.parse_data(line)
        bridge.disconnect()

        self.assertIsNotNone(data)
        self.assertAlmostEqual(data["p2"], 3.5, places=3)
        self.assertAlmostEqual(data["i2"], 0.7, places=3)
        self.assertAlmostEqual(data["d2"], 0.2, places=3)


class SelectSerialPortTests(unittest.TestCase):
    def test_returns_demo_port_when_no_devices_and_user_requests_demo(self):
        with patch("hw.bridge.serial.tools.list_ports.comports", return_value=[]):
            with patch("builtins.input", return_value="d"):
                port = select_serial_port()

        self.assertEqual(port, DEMO_SERIAL_PORT)

    def test_returns_demo_port_when_devices_exist_and_user_requests_demo(self):
        fake_port = types.SimpleNamespace(device="COM7", description="USB Serial")
        with patch("hw.bridge.serial.tools.list_ports.comports", return_value=[fake_port]):
            with patch("builtins.input", return_value="d"):
                port = select_serial_port()

        self.assertEqual(port, DEMO_SERIAL_PORT)


if __name__ == "__main__":
    unittest.main()
