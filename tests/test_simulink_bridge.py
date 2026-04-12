import os
import shutil
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).parent.parent))

import sim.simulink_bridge as simulink_bridge
from sim.simulink_bridge import SimulinkBridge


class _FakeEngine:
    def __init__(self, sim_output):
        self._sim_output = sim_output
        self.blocks = {}
        self.set_param_calls = []
        self.eval_calls = []

    def set_param(self, *_args, **_kwargs):
        self.set_param_calls.append(_args)
        return None

    def sim(self, *_args, **_kwargs):
        return self._sim_output

    def getfield(self, obj, field_name, nargout=1):
        if isinstance(obj, dict) and field_name in obj:
            return obj[field_name]
        raise KeyError(field_name)

    def get(self, obj, field_name, nargout=1):
        if isinstance(obj, dict) and field_name in obj:
            return obj[field_name]
        return None

    def fieldnames(self, obj, nargout=1):
        if isinstance(obj, dict):
            return list(obj.keys())
        return []

    def find_system(self, _model_name, *args, **kwargs):
        block_type = None
        tag_value = None
        for index in range(0, len(args), 2):
            if index + 1 >= len(args):
                break
            if args[index] == "BlockType":
                block_type = args[index + 1]
            if args[index] == "Tag":
                tag_value = args[index + 1]
        paths = list(self.blocks.keys())
        if block_type:
            paths = [
                path for path in paths
                if self.blocks[path].get("BlockType") == block_type
            ]
        if tag_value is not None:
            paths = [
                path for path in paths
                if self.blocks[path].get("Tag") == tag_value
            ]
        return paths

    def get_param(self, block_path, parameter_name, nargout=1):
        return self.blocks[block_path][parameter_name]

    def eval(self, expression, nargout=0):
        self.eval_calls.append((expression, nargout))
        return None

    def isa(self, obj, class_name, nargout=1):
        if class_name == "timeseries" and isinstance(obj, dict):
            return "Time" in obj and "Data" in obj
        return False


class SimulinkBridgeCompatTests(unittest.TestCase):
    def _make_temp_matlab_root(self, folder_name: str) -> Path:
        root = Path(__file__).resolve().parent.parent / "artifacts" / folder_name
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _populate_matlab_root(self, matlab_root: Path, *, include_runtime_dirs: bool) -> None:
        from sim.matlab_runtime import _runtime_layout
        arch = _runtime_layout()[0]
        (matlab_root / "bin" / arch).mkdir(parents=True)
        (matlab_root / "extern" / "bin" / arch).mkdir(parents=True)
        (
            matlab_root
            / "extern"
            / "engines"
            / "python"
            / "dist"
            / "matlab"
            / "engine"
            / arch
        ).mkdir(parents=True)
        if include_runtime_dirs:
            (matlab_root / "runtime" / arch).mkdir(parents=True)
            (matlab_root / "sys" / "os" / arch).mkdir(parents=True)

    def _make_bridge(self, sim_output):
        fake_engine_module = type(
            "_FakeMatlabEngineModule",
            (),
            {"start_matlab": staticmethod(lambda: None)},
        )
        with patch(
            "sim.simulink_bridge._load_matlab_engine",
            return_value=fake_engine_module,
        ):
            bridge = SimulinkBridge(
                model_path="C:/models/demo.slx",
                setpoint=200.0,
                pid_block_path="demo/PID Controller",
                output_signal="y_out",
                matlab_root="C:/Program Files/MATLAB/R2022b",
                sim_step_time=10.0,
            )
        bridge._eng = _FakeEngine(sim_output)
        bridge._model_name = "demo"
        return bridge

    def test_connect_uses_model_path_basename_without_name_error(self):
        class _ConnectEngine:
            def __init__(self):
                self.calls = []

            def addpath(self, *args, **kwargs):
                self.calls.append(("addpath", args))
                return None

            def load_system(self, *args, **kwargs):
                self.calls.append(("load_system", args))
                return None

            def set_param(self, *args, **kwargs):
                self.calls.append(("set_param", args))
                return None

        fake_engine = _ConnectEngine()
        fake_engine_module = type(
            "_FakeMatlabEngineModule",
            (),
            {"start_matlab": staticmethod(lambda: fake_engine)},
        )
        with patch("sim.simulink_bridge._load_matlab_engine", return_value=fake_engine_module):
            bridge = SimulinkBridge(
                model_path="C:/models/demo.slx",
                setpoint=200.0,
                pid_block_path="demo/PID Controller",
                output_signal="y_out",
                matlab_root="C:/Program Files/MATLAB/R2022b",
                sim_step_time=10.0,
            )

        with patch.object(bridge, "_apply_model_setpoint", return_value=None):
            with patch.object(bridge, "_autodiscover_controller_paths", return_value=None):
                with patch.object(bridge, "_read_controller_gain", side_effect=lambda _k, default: default):
                    bridge.connect()

        self.assertEqual(bridge._model_name, "demo")
        self.assertTrue(any(name == "addpath" for name, _ in fake_engine.calls))
        self.assertTrue(any(name == "load_system" for name, _ in fake_engine.calls))

    def test_call_engine_method_uses_engine_stream_capture_when_supported(self):
        captured: dict[str, object] = {}

        class _CaptureEngine(_FakeEngine):
            def get_param(  # type: ignore[override]
                self, block_path, parameter_name, nargout=1, stdout=None, stderr=None
            ):
                captured["args"] = (block_path, parameter_name, nargout)
                captured["stdout"] = stdout
                captured["stderr"] = stderr
                return "1.5"

        bridge = self._make_bridge({})
        bridge._eng = _CaptureEngine({})

        value = bridge._call_engine_method("get_param", "demo/PID Controller", "Kp")

        self.assertEqual(value, "1.5")
        self.assertEqual(captured["args"], ("demo/PID Controller", "Kp", 1))
        self.assertIsNotNone(captured["stdout"])
        self.assertIsNotNone(captured["stderr"])

    def test_run_step_reads_control_signal_into_pwm(self):
        sim_output = {
            "y_out": {
                "Time": [[0.0], [1.0]],
                "Data": [[100.0], [120.0]],
            },
            "u_out": {
                "Time": [[0.0], [1.0]],
                "Data": [[10.0], [20.0]],
            },
        }
        bridge = self._make_bridge(sim_output)
        bridge.control_signal = "u_out"

        bridge.run_step()

        self.assertEqual(bridge.get_data()[0]["pwm"], 10.0)
        self.assertEqual(bridge.get_data()[1]["pwm"], 20.0)
        self.assertTrue(bridge.has_control_signal)
        self.assertEqual(bridge.resolved_control_signal, "u_out")

    def test_apply_model_setpoint_updates_explicit_block(self):
        bridge = self._make_bridge({})
        bridge.setpoint = 220.0
        bridge.setpoint_block = "demo/ManualSetpoint"
        bridge._eng.blocks = {
            "demo/ManualSetpoint": {"BlockType": "Constant", "Value": "0"},
        }

        bridge._apply_model_setpoint()

        self.assertIn(("demo/ManualSetpoint", "Value", "220.0"), bridge._eng.set_param_calls)

    def test_run_step_reads_top_level_timeseries(self):
        sim_output = {
            "y_out": {
                "Time": [[0.0], [1.0]],
                "Data": [[100.0], [120.0]],
            }
        }
        bridge = self._make_bridge(sim_output)

        bridge.run_step()

        self.assertEqual(len(bridge.get_data()), 2)
        self.assertEqual(bridge.get_data()[0]["timestamp"], 0.0)
        self.assertEqual(bridge.get_data()[1]["input"], 120.0)

    def test_run_step_reads_nested_out_timeseries(self):
        sim_output = {
            "out": {
                "y_out": {
                    "Time": [[0.0], [1.0]],
                    "Data": [[150.0], [160.0]],
                }
            }
        }
        bridge = self._make_bridge(sim_output)

        bridge.run_step()

        self.assertEqual(len(bridge.get_data()), 2)
        self.assertEqual(bridge.get_data()[0]["input"], 150.0)
        self.assertEqual(bridge.get_data()[1]["input"], 160.0)

    def test_run_step_reads_nested_array_with_tout(self):
        sim_output = {
            "out": {
                "y_out": [[80.0], [90.0]],
                "tout": [[0.0], [2.0]],
            }
        }
        bridge = self._make_bridge(sim_output)

        bridge.run_step()

        self.assertEqual(len(bridge.get_data()), 2)
        self.assertEqual(bridge.get_data()[0]["timestamp"], 0.0)
        self.assertEqual(bridge.get_data()[1]["timestamp"], 2000.0)
        self.assertEqual(bridge.get_data()[0]["input"], 80.0)
        self.assertEqual(bridge.get_data()[1]["input"], 90.0)

    def test_run_step_reads_from_logsout(self):
        sim_output = {
            "logsout": {
                "y_out": {
                    "Time": [[0.0], [1.0]],
                    "Data": [[200.0], [210.0]],
                }
            }
        }
        bridge = self._make_bridge(sim_output)

        bridge.run_step()

        self.assertEqual(len(bridge.get_data()), 2)
        self.assertEqual(bridge.get_data()[0]["input"], 200.0)
        self.assertEqual(bridge.get_data()[1]["input"], 210.0)

    def test_run_step_reads_signal_from_matlab_workspace_when_sim_out_omits_it(self):
        class _WorkspaceEngine(_FakeEngine):
            def __init__(self, sim_output, workspace_vars):
                super().__init__(sim_output)
                self.workspace_vars = workspace_vars

            def eval(self, expression, nargout=0):  # type: ignore[override]
                self.eval_calls.append((expression, nargout))
                return self.workspace_vars.get(expression)

        bridge = self._make_bridge({})
        bridge._eng = _WorkspaceEngine(
            {},
            {
                "y_out": {
                    "Time": [[0.0], [0.01], [0.02]],
                    "Data": [[10.0], [15.0], [21.0]],
                }
            },
        )

        bridge.run_step()

        self.assertEqual(len(bridge.get_data()), 3)
        self.assertEqual(bridge.get_data()[0]["timestamp"], 0.0)
        self.assertEqual(bridge.get_data()[1]["timestamp"], 10.0)
        self.assertEqual(bridge.get_data()[2]["input"], 21.0)

    def test_run_step_reads_from_yout_fallback(self):
        # Case: user configured y_out but model outputs yout
        sim_output = {
            "yout": [[10.0], [15.0]]
        }
        bridge = self._make_bridge(sim_output)
        bridge.output_signal = "y_out"

        with patch("builtins.print") as print_mock:
            bridge.run_step()

        self.assertEqual(len(bridge.get_data()), 2)
        self.assertEqual(bridge.get_data()[0]["input"], 10.0)
        self.assertEqual(bridge.get_data()[1]["input"], 15.0)
        print_mock.assert_any_call(
            "[Simulink][WARN] Configured MATLAB_OUTPUT_SIGNAL='y_out', "
            "but simulation output used 'yout'. Update your config or model to match."
        )

    def test_run_step_fails_with_available_fields_in_error(self):
        sim_output = {
            "mismatched_signal": [1, 2, 3]
        }
        bridge = self._make_bridge(sim_output)
        bridge.output_signal = "y_out"

        with self.assertRaisesRegex(RuntimeError, "Available fields in simOut: mismatched_signal"):
            bridge.run_step()

    def test_apply_model_setpoint_updates_step_block(self):
        bridge = self._make_bridge({})
        bridge.setpoint = 300.0
        bridge._eng.blocks = {
            "demo/Step": {"BlockType": "Step"},
        }

        bridge._apply_model_setpoint()

        self.assertIn(("demo/Step", "After", "300.0"), bridge._eng.set_param_calls)

    def test_apply_model_setpoint_updates_constant_block(self):
        bridge = self._make_bridge({})
        bridge.setpoint = 180.0
        bridge._eng.blocks = {
            "demo/Setpoint": {"BlockType": "Constant"},
        }

        bridge._apply_model_setpoint()

        self.assertIn(
            ("demo/Setpoint", "Value", "180.0"),
            bridge._eng.set_param_calls,
        )


    def test_set_pid_supports_kp_ki_kd_parameter_names(self):
        bridge = self._make_bridge({})
        bridge._eng.blocks = {
            "demo/PID Controller": {
                "Kp": "1.0",
                "Ki": "0.1",
                "Kd": "0.05",
            },
        }
        bridge.pid_block_path = "demo/PID Controller"

        bridge.set_pid(2.5, 0.4, 0.2)

        self.assertIn(("demo/PID Controller", "Kp", "2.5"), bridge._eng.set_param_calls)
        self.assertIn(("demo/PID Controller", "Ki", "0.4"), bridge._eng.set_param_calls)
        self.assertIn(("demo/PID Controller", "Kd", "0.2"), bridge._eng.set_param_calls)

    def test_connect_reads_kp_ki_kd_parameter_names(self):
        bridge = self._make_bridge({})
        bridge._eng.blocks = {
            "demo/PID Controller": {
                "Kp": "3.0",
                "Ki": "0.2",
                "Kd": "0.1",
            }
        }
        bridge.pid_block_path = "demo/PID Controller"

        bridge.kp = bridge._read_controller_gain("p", bridge.kp)
        bridge.ki = bridge._read_controller_gain("i", bridge.ki)
        bridge.kd = bridge._read_controller_gain("d", bridge.kd)

        self.assertEqual(bridge.kp, 3.0)
        self.assertEqual(bridge.ki, 0.2)
        self.assertEqual(bridge.kd, 0.1)

    def test_set_pid_supports_proportional_integral_derivative_gain_names(self):
        bridge = self._make_bridge({})
        bridge._eng.blocks = {
            "demo/PID Controller": {
                "ProportionalGain": "1.0",
                "IntegralGain": "0.1",
                "DerivativeGain": "0.05",
            }
        }
        bridge.pid_block_path = "demo/PID Controller"

        bridge.set_pid(4.0, 0.6, 0.3)

        self.assertIn(("demo/PID Controller", "ProportionalGain", "4.0"), bridge._eng.set_param_calls)
        self.assertIn(("demo/PID Controller", "IntegralGain", "0.6"), bridge._eng.set_param_calls)
        self.assertIn(("demo/PID Controller", "DerivativeGain", "0.3"), bridge._eng.set_param_calls)

    def test_set_pid_handles_pi_style_block_without_d_gain(self):
        bridge = self._make_bridge({})
        bridge._eng.blocks = {
            "demo/PI Controller": {"Kp": "1.0", "Ki": "0.1"}
        }
        bridge.pid_block_path = "demo/PI Controller"

        bridge.set_pid(5.0, 0.8, 0.0)

        self.assertIn(("demo/PI Controller", "Kp", "5.0"), bridge._eng.set_param_calls)
        self.assertIn(("demo/PI Controller", "Ki", "0.8"), bridge._eng.set_param_calls)
        self.assertFalse(any(call[1] == "Kd" for call in bridge._eng.set_param_calls))

    def test_set_pid_uses_first_compatible_block_from_pid_block_paths(self):
        bridge = self._make_bridge({})
        bridge.pid_block_path = ""
        bridge.pid_block_paths = ["demo/Outer Loop", "demo/Inner Loop"]
        bridge._eng.blocks = {
            "demo/Outer Loop": {"Kp": "1.0", "Ki": "0.1"},
            "demo/Inner Loop": {"Kp": "2.0", "Ki": "0.2", "Kd": "0.05"},
        }

        bridge.set_pid(6.0, 0.9, 0.4)

        self.assertIn(("demo/Outer Loop", "Kp", "6.0"), bridge._eng.set_param_calls)
        self.assertIn(("demo/Outer Loop", "Ki", "0.9"), bridge._eng.set_param_calls)

    def test_autodiscover_ignores_non_controller_secondary_candidates(self):
        bridge = self._make_bridge({})
        bridge.pid_block_path = ""
        bridge.pid_block_paths = []
        bridge._eng.blocks = {
            "demo/Outer PID": {"Kp": "1.0", "Ki": "0.1", "Kd": "0.05"},
            "demo/PID Controller/Anti-windup/Passthrough/Signal Specification2": {
                "P": "1.0"
            },
        }

        with patch.object(
            bridge,
            "_find_all_blocks",
            return_value=[
                "demo/Outer PID",
                "demo/PID Controller/Anti-windup/Passthrough/Signal Specification2",
            ],
        ):
            bridge._autodiscover_controller_paths()

        self.assertEqual(bridge.pid_block_path, "demo/Outer PID")
        self.assertEqual(bridge.secondary_pid_block_path, "")

    def test_autodiscover_prefers_tagged_primary_and_secondary(self):
        bridge = self._make_bridge({})
        bridge.pid_block_path = ""
        bridge.pid_block_paths = []
        bridge.secondary_pid_block_path = ""
        bridge.secondary_pid_block_paths = []
        bridge._eng.blocks = {
            "demo/LoopA": {
                "BlockType": "PIDController",
                "Kp": "1.0",
                "Ki": "0.1",
                "Kd": "0.01",
            },
            "demo/LoopB": {
                "BlockType": "PIDController",
                "Kp": "2.0",
                "Ki": "0.2",
                "Kd": "0.02",
                "Tag": "llm_pid_tuner_primary",
            },
            "demo/LoopC": {
                "BlockType": "PIDController",
                "Kp": "3.0",
                "Ki": "0.3",
                "Kd": "0.03",
                "Tag": "llm_pid_tuner_secondary",
            },
        }

        bridge._autodiscover_controller_paths()

        self.assertEqual(bridge.pid_block_path, "demo/LoopB")
        self.assertEqual(bridge.secondary_pid_block_path, "demo/LoopC")

    def test_autodiscover_uses_pid_controller_block_type_before_scoring_fallback(self):
        bridge = self._make_bridge({})
        bridge.pid_block_path = ""
        bridge.pid_block_paths = []
        bridge._eng.blocks = {
            "demo/Loop1": {
                "BlockType": "PIDController",
                "Kp": "1.0",
                "Ki": "0.1",
                "Kd": "0.01",
            },
            "demo/SignalSpecification": {
                "BlockType": "SubSystem",
                "Kp": "9.0",
                "Ki": "0.9",
                "Kd": "0.09",
            },
        }

        bridge._autodiscover_controller_paths()

        self.assertEqual(bridge.pid_block_path, "demo/Loop1")

    def test_run_step_auto_detects_control_signal_when_not_configured(self):
        sim_output = {
            "y_out": {
                "Time": [[0.0], [1.0]],
                "Data": [[100.0], [120.0]],
            },
            "u_out": {
                "Time": [[0.0], [1.0]],
                "Data": [[10.0], [20.0]],
            },
        }
        bridge = self._make_bridge(sim_output)
        bridge.control_signal = ""

        with patch("builtins.print") as print_mock:
            bridge.run_step()

        self.assertEqual(bridge.get_data()[0]["pwm"], 10.0)
        self.assertEqual(bridge.resolved_control_signal, "u_out")
        print_mock.assert_any_call("[Simulink] Auto-detected control signal: u_out")

    def test_run_step_control_signal_fallback_uses_common_names(self):
        sim_output = {
            "y_out": {
                "Time": [[0.0], [1.0]],
                "Data": [[100.0], [120.0]],
            },
            "pwm": [[30.0], [40.0]],
            "tout": [[0.0], [1.0]],
        }
        bridge = self._make_bridge(sim_output)
        bridge.control_signal = "u_cmd"

        with patch("builtins.print") as print_mock:
            bridge.run_step()

        self.assertEqual(bridge.get_data()[0]["pwm"], 30.0)
        self.assertEqual(bridge.resolved_control_signal, "pwm")
        print_mock.assert_any_call(
            "[Simulink][WARN] Configured MATLAB_CONTROL_SIGNAL='u_cmd', "
            "but simulation output used 'pwm'. Update your config or model to match."
        )

    def test_set_pid_pair_writes_secondary_controller(self):
        bridge = self._make_bridge({})
        bridge._eng.blocks = {
            "demo/Outer PID": {"Kp": "1.0", "Ki": "0.1", "Kd": "0.05"},
            "demo/Inner PID": {"Kp": "2.0", "Ki": "0.2", "Kd": "0.06"},
        }
        bridge.pid_block_path = "demo/Outer PID"
        bridge.secondary_pid_block_path = "demo/Inner PID"
        bridge.secondary_pid_block_paths = ["demo/Inner PID"]

        bridge.set_pid_pair(
            {"p": 3.0, "i": 0.4, "d": 0.1},
            {"p": 4.0, "i": 0.5, "d": 0.2},
        )

        self.assertIn(("demo/Outer PID", "Kp", "3.0"), bridge._eng.set_param_calls)
        self.assertIn(("demo/Inner PID", "Kp", "4.0"), bridge._eng.set_param_calls)
        self.assertIn(("demo/Inner PID", "Ki", "0.5"), bridge._eng.set_param_calls)
        # Secondary PID attributes should reflect the applied values so the
        # TUI can display both controllers.
        self.assertEqual(bridge.secondary_kp, 4.0)
        self.assertEqual(bridge.secondary_ki, 0.5)
        self.assertEqual(bridge.secondary_kd, 0.2)
        self.assertTrue(bridge.has_secondary_pid)

    def test_has_secondary_pid_false_without_configuration(self):
        bridge = self._make_bridge({})
        self.assertFalse(bridge.has_secondary_pid)

    def test_set_pid_pair_skips_when_secondary_path_missing(self):
        bridge = self._make_bridge({})
        bridge._eng.blocks = {
            "demo/Outer PID": {"Kp": "1.0", "Ki": "0.1", "Kd": "0.05"},
        }
        bridge.pid_block_path = "demo/Outer PID"
        bridge.secondary_pid_block_path = ""

        notes = bridge.set_pid_pair(
            {"p": 3.0, "i": 0.4, "d": 0.1},
            {"p": 4.0, "i": 0.5, "d": 0.2},
        )

        self.assertEqual(
            notes,
            ["Controller 2 path is missing; skipped secondary update."],
        )
        self.assertIn(("demo/Outer PID", "Kp", "3.0"), bridge._eng.set_param_calls)

    def test_set_pid_pair_skips_when_secondary_path_equals_primary(self):
        bridge = self._make_bridge({})
        bridge._eng.blocks = {
            "demo/Outer PID": {"Kp": "1.0", "Ki": "0.1", "Kd": "0.05"},
        }
        bridge.pid_block_path = "demo/Outer PID"
        bridge.secondary_pid_block_path = "demo/Outer PID"
        bridge.secondary_pid_block_paths = ["demo/Outer PID"]

        notes = bridge.set_pid_pair(
            {"p": 3.0, "i": 0.4, "d": 0.1},
            {"p": 4.0, "i": 0.5, "d": 0.2},
        )

        self.assertEqual(
            notes,
            [
                "Controller 2 path equals Controller 1 path; skipped secondary update to avoid writing the same block twice."
            ],
        )
        outer_writes = [call for call in bridge._eng.set_param_calls if call[0] == "demo/Outer PID" and call[1] == "Kp"]
        self.assertEqual(len(outer_writes), 1)

    def test_set_pid_pair_preserves_existing_secondary_gains_when_fields_are_missing(self):
        bridge = self._make_bridge({})
        bridge._eng.blocks = {
            "demo/Outer PID": {"Kp": "1.0", "Ki": "0.1", "Kd": "0.05"},
            "demo/Inner PID": {"Kp": "2.0", "Ki": "0.2", "Kd": "0.06"},
        }
        bridge.pid_block_path = "demo/Outer PID"
        bridge.secondary_pid_block_path = "demo/Inner PID"
        bridge.secondary_pid_block_paths = ["demo/Inner PID"]

        bridge.set_pid_pair(
            {"p": 3.0, "i": 0.4, "d": 0.1},
            {"p": 4.0},
        )

        self.assertIn(("demo/Inner PID", "Kp", "4.0"), bridge._eng.set_param_calls)
        self.assertIn(("demo/Inner PID", "Ki", "0.2"), bridge._eng.set_param_calls)
        self.assertIn(("demo/Inner PID", "Kd", "0.06"), bridge._eng.set_param_calls)

    def test_set_pid_pair_applies_guardrails_to_secondary_controller(self):
        bridge = self._make_bridge({})
        bridge._eng.blocks = {
            "demo/Outer PID": {"Kp": "1.0", "Ki": "0.1", "Kd": "0.05"},
            "demo/Inner PID": {"Kp": "2.0", "Ki": "0.5", "Kd": "0.25"},
        }
        bridge.pid_block_path = "demo/Outer PID"
        bridge.secondary_pid_block_path = "demo/Inner PID"
        bridge.secondary_pid_block_paths = ["demo/Inner PID"]

        notes = bridge.set_pid_pair(
            {"p": 3.0, "i": 0.4, "d": 0.1},
            {"p": 50.0, "i": 10.0, "d": 10.0},
        )

        self.assertTrue(notes)
        self.assertIn(("demo/Inner PID", "Kp", "10.0"), bridge._eng.set_param_calls)
        self.assertIn(("demo/Inner PID", "Ki", "3.0"), bridge._eng.set_param_calls)
        self.assertIn(("demo/Inner PID", "Kd", "1.5"), bridge._eng.set_param_calls)

    def test_set_pid_pair_skips_mirrored_secondary_suggestion(self):
        bridge = self._make_bridge({})
        bridge._eng.blocks = {
            "demo/Outer PID": {"Kp": "1.0", "Ki": "0.1", "Kd": "0.05"},
            "demo/Inner PID": {"Kp": "2.0", "Ki": "0.2", "Kd": "0.06"},
        }
        bridge.pid_block_path = "demo/Outer PID"
        bridge.secondary_pid_block_path = "demo/Inner PID"
        bridge.secondary_pid_block_paths = ["demo/Inner PID"]

        notes = bridge.set_pid_pair(
            {"p": 3.0, "i": 0.4, "d": 0.1},
            {"p": 3.0, "i": 0.4, "d": 0.1},
        )

        self.assertIn(
            "Controller 2 suggestion mirrored Controller 1; kept existing secondary PID to avoid coupling both loops.",
            notes,
        )
        self.assertIn(("demo/Outer PID", "Kp", "3.0"), bridge._eng.set_param_calls)
        self.assertFalse(
            any(call[0] == "demo/Inner PID" for call in bridge._eng.set_param_calls)
        )

    def test_set_pid_pair_skips_incompatible_secondary_block_without_crashing(self):
        class _RaisingEngine(_FakeEngine):
            def set_param(self, *args, **kwargs):
                if args and args[0] == "demo/Inner PID":
                    raise RuntimeError("secondary block incompatible")
                return super().set_param(*args, **kwargs)

        bridge = self._make_bridge({})
        bridge._eng = _RaisingEngine({})
        bridge._eng.blocks = {
            "demo/Outer PID": {"Kp": "1.0", "Ki": "0.1", "Kd": "0.05"},
            "demo/Inner PID": {"Kp": "2.0", "Ki": "0.2", "Kd": "0.06"},
        }
        bridge.pid_block_path = "demo/Outer PID"
        bridge.secondary_pid_block_path = "demo/Inner PID"
        bridge.secondary_pid_block_paths = ["demo/Inner PID"]

        notes = bridge.set_pid_pair(
            {"p": 3.0, "i": 0.4, "d": 0.1},
            {"p": 4.0, "i": 0.5, "d": 0.2},
        )

        self.assertEqual(
            notes,
            ["Controller 2 update skipped due to incompatible block configuration."],
        )
        self.assertIn(("demo/Outer PID", "Kp", "3.0"), bridge._eng.set_param_calls)

    def test_refresh_timing_metadata_detects_discrete_domain(self):
        bridge = self._make_bridge({})
        bridge._eng.blocks = {
            "demo": {
                "SolverType": "Fixed-step",
                "Solver": "discrete",
                "FixedStep": "0.01",
            },
            "demo/Outer PID": {
                "Kp": "1.0",
                "Ki": "0.1",
                "Kd": "0.05",
                "SampleTime": "0.01",
            },
            "demo/Inner PID": {
                "Kp": "2.0",
                "Ki": "0.2",
                "Kd": "0.06",
                "SampleTime": "0.02",
            },
        }
        bridge.pid_block_path = "demo/Outer PID"
        bridge.secondary_pid_block_path = "demo/Inner PID"
        bridge.secondary_pid_block_paths = ["demo/Inner PID"]

        bridge._refresh_timing_metadata()

        self.assertEqual(bridge.model_solver_type, "Fixed-step")
        self.assertEqual(bridge.model_solver_name, "discrete")
        self.assertEqual(bridge.model_fixed_step, "0.01")
        self.assertEqual(bridge.controller_1_sample_time, "0.01")
        self.assertEqual(bridge.controller_2_sample_time, "0.02")
        self.assertEqual(bridge.control_domain, "discrete")

    def test_refresh_timing_metadata_uses_solver_type_when_sample_time_missing(self):
        bridge = self._make_bridge({})
        bridge._eng.blocks = {
            "demo": {
                "SolverType": "Variable-step",
                "Solver": "ode45",
                "FixedStep": "auto",
            },
            "demo/Outer PID": {
                "Kp": "1.0",
                "Ki": "0.1",
                "Kd": "0.05",
            },
        }
        bridge.pid_block_path = "demo/Outer PID"

        bridge._refresh_timing_metadata()

        self.assertEqual(bridge.model_solver_type, "Variable-step")
        self.assertEqual(bridge.control_domain, "continuous_like")

    def test_load_matlab_engine_prefers_configured_runtime_over_stale_modules(self):
        original_sys_path = list(sys.path)
        original_path = os.environ.get("PATH")
        original_mwe_install = os.environ.get("MWE_INSTALL")
        original_matlab_root = os.environ.get("MATLAB_ROOT")
        original_engine = simulink_bridge._MATLAB_ENGINE
        original_modules = {
            name: module
            for name, module in sys.modules.items()
            if name == "matlab" or name.startswith("matlab.")
        }
        temp_root = None

        try:
            stale_matlab = ModuleType("matlab")
            stale_matlab.__file__ = "C:/other/site-packages/matlab/__init__.py"
            stale_engine = ModuleType("matlab.engine")
            stale_engine.__file__ = "C:/other/site-packages/matlab/engine/__init__.py"
            sys.modules["matlab"] = stale_matlab
            sys.modules["matlab.engine"] = stale_engine

            temp_root = self._make_temp_matlab_root("test_load_matlab_engine")
            matlab_root = temp_root / "MATLAB" / "R2024b"
            self._populate_matlab_root(matlab_root, include_runtime_dirs=False)

            imported_engine = ModuleType("matlab.engine")

            def fake_import(module_name: str):
                self.assertEqual(module_name, "matlab.engine")
                self.assertNotIn("matlab", sys.modules)
                self.assertNotIn("matlab.engine", sys.modules)
                return imported_engine

            with patch("sim.matlab_runtime.os.add_dll_directory", Mock(), create=True):
                with patch(
                    "sim.matlab_runtime.importlib.import_module",
                    side_effect=fake_import,
                ) as import_module:
                    simulink_bridge._MATLAB_ENGINE = None
                    loaded = simulink_bridge._load_matlab_engine(str(matlab_root))

            self.assertIs(loaded, imported_engine)
            self.assertIs(simulink_bridge._MATLAB_ENGINE, imported_engine)
            import_module.assert_called_once_with("matlab.engine")
        finally:
            if temp_root is not None:
                shutil.rmtree(temp_root, ignore_errors=True)
            simulink_bridge._MATLAB_ENGINE = original_engine
            for module_name in list(sys.modules):
                if module_name == "matlab" or module_name.startswith("matlab."):
                    sys.modules.pop(module_name, None)
            sys.modules.update(original_modules)
            sys.path[:] = original_sys_path
            if original_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = original_path
            if original_mwe_install is None:
                os.environ.pop("MWE_INSTALL", None)
            else:
                os.environ["MWE_INSTALL"] = original_mwe_install
            if original_matlab_root is None:
                os.environ.pop("MATLAB_ROOT", None)
            else:
                os.environ["MATLAB_ROOT"] = original_matlab_root


if __name__ == "__main__":
    unittest.main()
