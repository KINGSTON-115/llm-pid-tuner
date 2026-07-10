from __future__ import annotations

from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import unittest

from plc.demo import MemoryModbusClient, main as demo_main
from plc.environment import PLCEnvironment
from plc.modbus import PLCBridgeError, ModbusPLCBridge, ModbusValueCodec
from plc.pid_semantics import PIDSemanticsCodec, validate_canonical_pid
from plc.profile import (
    PIDSemantics,
    PLCProfile,
    PLCProfileError,
    RegisterSpec,
    load_plc_profile,
)


PROFILE_PATH = Path(__file__).resolve().parents[1] / "plc" / "example_profile.json"


def profile_data() -> dict:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def connected_bridge(profile: PLCProfile | None = None) -> ModbusPLCBridge:
    resolved_profile = profile or load_plc_profile(PROFILE_PATH)
    bridge = ModbusPLCBridge(
        resolved_profile,
        client=MemoryModbusClient(resolved_profile),
        sleep_fn=lambda _seconds: None,
    )
    if not bridge.connect():
        raise AssertionError(bridge.last_error)
    return bridge


class PLCProfileTests(unittest.TestCase):
    def test_example_profile_loads(self):
        profile = load_plc_profile(PROFILE_PATH)

        self.assertEqual(profile.connection.protocol, "modbus_tcp")
        self.assertEqual(profile.pid_semantics.form, "ideal")
        self.assertEqual(profile.write_policy.mode, "confirm")
        self.assertEqual(profile.registers["pv"].word_count, 2)

    def test_overlapping_registers_are_rejected(self):
        raw = profile_data()
        raw["registers"]["setpoint"]["address"] = raw["registers"]["pv"][
            "address"
        ]

        with self.assertRaisesRegex(PLCProfileError, "overlaps"):
            PLCProfile.from_dict(raw)

    def test_write_mode_requires_handshake_registers(self):
        raw = profile_data()
        raw["registers"].pop("ack_sequence")

        with self.assertRaisesRegex(PLCProfileError, "ack_sequence"):
            PLCProfile.from_dict(raw)

    def test_required_safety_flag_is_enforced(self):
        raw = profile_data()
        raw["registers"].pop("interlock_ok")

        with self.assertRaisesRegex(PLCProfileError, "interlock_ok"):
            PLCProfile.from_dict(raw)

    def test_register_range_is_enforced(self):
        raw = profile_data()
        raw["registers"]["pv"]["address"] = 65535

        with self.assertRaisesRegex(PLCProfileError, "address range"):
            PLCProfile.from_dict(raw)

    def test_pid_values_must_use_numeric_registers(self):
        raw = profile_data()
        raw["registers"]["kp"]["data_type"] = "bool"

        with self.assertRaisesRegex(PLCProfileError, "numeric data type"):
            PLCProfile.from_dict(raw)

    def test_handshake_sequences_must_use_matching_unsigned_types(self):
        raw = profile_data()
        raw["registers"]["ack_sequence"]["data_type"] = "uint32"

        with self.assertRaisesRegex(PLCProfileError, "same uint16 or uint32"):
            PLCProfile.from_dict(raw)


class PIDSemanticsTests(unittest.TestCase):
    def test_ideal_seconds_round_trip(self):
        codec = PIDSemanticsCodec(
            PIDSemantics("ideal", "seconds", "seconds", "reverse")
        )

        canonical = codec.native_to_canonical(2.0, 10.0, 0.25)
        native = codec.canonical_to_native(canonical)

        self.assertEqual(canonical, {"p": 2.0, "i": 0.2, "d": 0.5})
        self.assertAlmostEqual(native["kp"], 2.0)
        self.assertAlmostEqual(native["integral"], 10.0)
        self.assertAlmostEqual(native["derivative"], 0.25)

    def test_parallel_minutes_conversion(self):
        codec = PIDSemanticsCodec(
            PIDSemantics("parallel", "minutes", "minutes", "reverse")
        )

        canonical = codec.native_to_canonical(3.0, 6.0, 0.2)
        native = codec.canonical_to_native(canonical)

        self.assertAlmostEqual(canonical["p"], 3.0)
        self.assertAlmostEqual(canonical["i"], 0.1)
        self.assertAlmostEqual(canonical["d"], 12.0)
        self.assertAlmostEqual(native["integral"], 6.0)
        self.assertAlmostEqual(native["derivative"], 0.2)

    def test_canonical_limits_reject_unsafe_value(self):
        limits = {
            "p": {"min": 0.0, "max": 2.0},
            "i": {"min": 0.0, "max": 1.0},
            "d": {"min": 0.0, "max": 1.0},
        }

        with self.assertRaisesRegex(PLCProfileError, "outside"):
            validate_canonical_pid({"p": 3.0, "i": 0.1, "d": 0.1}, limits)

    def test_ideal_form_rejects_unrepresentable_zero_p(self):
        codec = PIDSemanticsCodec(
            PIDSemantics("ideal", "seconds", "seconds", "reverse")
        )

        with self.assertRaisesRegex(PLCProfileError, "cannot represent"):
            codec.canonical_to_native({"p": 0.0, "i": 0.1, "d": 0.0})


class ModbusValueCodecTests(unittest.TestCase):
    def test_float32_round_trip_with_little_word_order(self):
        spec = RegisterSpec(
            address=0,
            data_type="float32",
            byte_order="big",
            word_order="little",
            scale=0.5,
            offset=10.0,
        )

        encoded = ModbusValueCodec.encode(42.5, spec)
        decoded = ModbusValueCodec.decode(encoded, spec)

        self.assertAlmostEqual(decoded, 42.5, places=5)

    def test_int16_scaling_round_trip(self):
        spec = RegisterSpec(address=0, data_type="int16", scale=0.1)

        encoded = ModbusValueCodec.encode(-12.3, spec)

        self.assertAlmostEqual(ModbusValueCodec.decode(encoded, spec), -12.3)

    def test_non_finite_plc_float_is_rejected(self):
        spec = RegisterSpec(address=0, data_type="float32")

        with self.assertRaisesRegex(PLCBridgeError, "not finite"):
            ModbusValueCodec.decode([0x7FC0, 0x0000], spec)


class ModbusPLCBridgeTests(unittest.TestCase):
    def test_snapshot_maps_plc_values_to_tuning_sample(self):
        bridge = connected_bridge()

        sample = bridge.read_snapshot()

        self.assertEqual(sample["setpoint"], 50.0)
        self.assertEqual(sample["input"], 42.0)
        self.assertEqual(sample["pwm"], 35.0)
        self.assertEqual(sample["error"], 8.0)
        self.assertEqual(sample["p"], 2.0)
        self.assertEqual(sample["i"], 0.2)
        self.assertEqual(sample["d"], 0.0)
        self.assertTrue(sample["plc_interlock_ok"])

    def test_confirm_mode_rejects_unarmed_write(self):
        bridge = connected_bridge()

        applied = bridge.apply_pid({"p": 2.5, "i": 0.25, "d": 0.5})

        self.assertFalse(applied)
        self.assertIn("arm_writes_once", bridge.last_error)

    def test_confirm_mode_applies_after_one_shot_arm(self):
        bridge = connected_bridge()
        candidate = {"p": 2.5, "i": 0.25, "d": 0.5}

        self.assertTrue(bridge.arm_writes_once())
        self.assertTrue(bridge.apply_pid(candidate), bridge.last_error)

        readback = bridge.read_active_pid()
        self.assertAlmostEqual(readback["p"], candidate["p"], places=4)
        self.assertAlmostEqual(readback["i"], candidate["i"], places=4)
        self.assertAlmostEqual(readback["d"], candidate["d"], places=4)
        self.assertFalse(bridge.apply_pid(candidate))
        self.assertIn("arm_writes_once", bridge.last_error)

    def test_interlock_blocks_write(self):
        bridge = connected_bridge()
        client = bridge.client
        client.set_engineering_value("interlock_ok", False)

        self.assertTrue(bridge.arm_writes_once())
        self.assertFalse(bridge.apply_pid({"p": 2.5, "i": 0.25, "d": 0.5}))
        self.assertIn("interlock", bridge.last_error)

    def test_disabled_profile_never_writes(self):
        raw = profile_data()
        raw["write_policy"]["mode"] = "disabled"
        profile = PLCProfile.from_dict(raw)
        bridge = connected_bridge(profile)

        self.assertFalse(bridge.apply_pid({"p": 2.5, "i": 0.25, "d": 0.5}))
        self.assertIn("disabled", bridge.last_error)

    def test_write_communication_exception_is_reported(self):
        profile = load_plc_profile(PROFILE_PATH)

        class BrokenWriteClient(MemoryModbusClient):
            def write_registers(self, address, values, *, device_id=1):
                raise OSError("simulated link loss")

        bridge = ModbusPLCBridge(profile, client=BrokenWriteClient(profile))
        self.assertTrue(bridge.connect())
        self.assertTrue(bridge.arm_writes_once())

        self.assertFalse(bridge.apply_pid({"p": 2.5, "i": 0.25, "d": 0.5}))
        self.assertIn("simulated link loss", bridge.last_error)


class PLCEnvironmentTests(unittest.TestCase):
    def test_collect_samples_uses_profile_sample_count(self):
        raw = profile_data()
        raw["acquisition"].update(
            {
                "poll_interval_sec": 0.0,
                "sample_count": 3,
                "minimum_samples": 2,
                "timeout_sec": 1.0,
            }
        )
        profile = PLCProfile.from_dict(raw)
        bridge = connected_bridge(profile)
        env = PLCEnvironment(bridge)

        samples = env.collect_samples()

        self.assertEqual(len(samples), 3)
        self.assertEqual(env.get_current_pid()[1], None)
        self.assertEqual(env.get_setpoint(), 50.0)
        self.assertEqual(env.get_prompt_context()["environment_type"], "plc")

    def test_environment_surfaces_write_failure_and_success(self):
        bridge = connected_bridge()
        env = PLCEnvironment(bridge)
        candidate = {"p": 2.5, "i": 0.25, "d": 0.5}

        env.apply_pid(candidate)
        self.assertIn("arm_writes_once", env.last_apply_issue)

        self.assertTrue(env.arm_writes_once())
        env.apply_pid(candidate)
        self.assertEqual(env.last_apply_issue, "")

    def test_setpoint_write_is_disabled(self):
        env = PLCEnvironment(connected_bridge())

        self.assertFalse(env.set_setpoint(60.0))
        self.assertIn("disabled", env.last_setpoint_issue)

    def test_bad_plc_quality_rejects_samples(self):
        bridge = connected_bridge()
        bridge.client.set_engineering_value("quality", False)
        env = PLCEnvironment(bridge)

        self.assertEqual(env.collect_samples(), [])
        self.assertIn("bad sample quality", env.last_collect_issue)


class PLCDemoTests(unittest.TestCase):
    def test_demo_runs_without_external_dependency(self):
        output = io.StringIO()

        with redirect_stdout(output):
            result = demo_main()

        self.assertEqual(result, 0)
        self.assertIn("Unarmed write rejected", output.getvalue())
        self.assertIn("PID readback", output.getvalue())


if __name__ == "__main__":
    unittest.main()
