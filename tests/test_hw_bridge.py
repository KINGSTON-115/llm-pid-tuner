import sys
import types
import unittest
import struct
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent))

from hw.bridge import DEMO_SERIAL_PORT, SerialBridge, select_serial_port


def _make_datavision_frame(cmd: int, channel: int, value: float) -> bytes:
    head = struct.pack("<I", 0x59485A53)
    payload = struct.pack("<f", value)
    frame_len = 11 + len(payload)
    frame = (
        head
        + struct.pack("<B", channel)
        + struct.pack("<I", frame_len)
        + struct.pack("<B", cmd)
        + payload
    )
    checksum = sum(frame) & 0xFF
    return frame + struct.pack("<B", checksum)


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

    def test_stm32_status_snapshot_is_normalized(self):
        bridge = SerialBridge(DEMO_SERIAL_PORT, 115200, emit_console=False)
        bridge.hardware_profile = "stm32f407_openmv"

        data = bridge.parse_data(
            "Status:\r\n"
            "  pid.x = 0.310 0.110 0.000\r\n"
            "  pid.y = 0.280 0.110 0.000\r\n"
            "  target = (123,456)\r\n"
            "  servo.x = 750\r\n"
            "  servo.y = 480\r\n"
            "  servo.delta = 6\r\n"
        )

        self.assertIsNotNone(data)
        self.assertEqual(data["hardware_profile"], "stm32f407_openmv")
        self.assertEqual(data["board_family"], "stm32f407")
        self.assertEqual(data["sample_kind"], "status_snapshot")
        self.assertAlmostEqual(data["p"], 0.31, places=3)
        self.assertAlmostEqual(data["i"], 0.11, places=3)
        self.assertAlmostEqual(data["d"], 0.0, places=3)
        self.assertAlmostEqual(data["p2"], 0.28, places=3)
        self.assertAlmostEqual(data["i2"], 0.11, places=3)
        self.assertAlmostEqual(data["d2"], 0.0, places=3)
        self.assertAlmostEqual(data["setpoint"], 123.0, places=3)
        self.assertAlmostEqual(data["input"], 750.0, places=3)
        self.assertAlmostEqual(data["error"], -627.0, places=3)
        self.assertEqual(data["target_y"], 456)
        self.assertEqual(data["servo_y"], 480)
        self.assertEqual(data["pwm"], 6.0)

    def test_openmv_target_messages_are_normalized(self):
        bridge = SerialBridge(DEMO_SERIAL_PORT, 115200, emit_console=False)
        bridge.hardware_profile = "stm32f407_openmv"

        target_data = bridge.parse_data("T:123,456\n")
        no_target_data = bridge.parse_data("N\n")

        self.assertIsNotNone(target_data)
        self.assertEqual(target_data["sample_kind"], "openmv_target")
        self.assertTrue(target_data["has_target"])
        self.assertEqual(target_data["target_x"], 123)
        self.assertEqual(target_data["target_y"], 456)
        self.assertEqual(target_data["image_center_x"], 80)
        self.assertEqual(target_data["image_center_y"], 60)
        self.assertGreater(target_data["error"], 0.0)

        self.assertIsNotNone(no_target_data)
        self.assertEqual(no_target_data["sample_kind"], "openmv_no_target")
        self.assertFalse(no_target_data["has_target"])
        self.assertEqual(no_target_data["error"], 0.0)
        self.assertEqual(no_target_data["image_center_x"], 80)
        self.assertEqual(no_target_data["image_center_y"], 60)

    def test_mspm0_datavision_frames_are_pair_normalized(self):
        bridge = SerialBridge(DEMO_SERIAL_PORT, 115200, emit_console=False)
        bridge.hardware_profile = "mspm0_datavision"

        target_frame = _make_datavision_frame(0x01, 0x01, 1500.0)
        fact_frame = _make_datavision_frame(0x02, 0x01, 1450.0)

        self.assertIsNone(bridge.parse_data(target_frame))
        data = bridge.parse_data(fact_frame)

        self.assertIsNotNone(data)
        self.assertEqual(data["hardware_profile"], "mspm0_datavision")
        self.assertEqual(data["sample_kind"], "datavision_pair")
        self.assertEqual(data["channel"], 1)
        self.assertAlmostEqual(data["setpoint"], 1500.0, places=3)
        self.assertAlmostEqual(data["input"], 1450.0, places=3)
        self.assertAlmostEqual(data["error"], 50.0, places=3)

    def test_stm32_profile_commands_emit_config_syntax(self):
        bridge = SerialBridge(DEMO_SERIAL_PORT, 115200, emit_console=False)
        bridge.hardware_profile = "stm32f407_openmv"
        sent_commands: list[str] = []
        bridge.send_command = lambda cmd: sent_commands.append(cmd) or True  # type: ignore[method-assign]

        ok = bridge.send_profile_command(
            "SET",
            primary_pid={"p": 0.31, "i": 0.11, "d": 0.0},
        )

        self.assertTrue(ok)
        self.assertEqual(
            sent_commands,
            [
                "config pid.x.kp 0.31",
                "config pid.x.ki 0.11",
                "config pid.x.kd 0.0",
            ],
        )

    def test_mspm0_profile_blocks_writeback_commands(self):
        bridge = SerialBridge(DEMO_SERIAL_PORT, 115200, emit_console=False)
        bridge.hardware_profile = "mspm0_datavision"
        sent_commands: list[str] = []
        bridge.send_command = lambda cmd: sent_commands.append(cmd) or True  # type: ignore[method-assign]

        ok = bridge.send_profile_command(
            "SET",
            primary_pid={"p": 1.0, "i": 0.1, "d": 0.05},
        )

        self.assertFalse(ok)
        self.assertEqual(sent_commands, [])


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
