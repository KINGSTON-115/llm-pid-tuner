import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).parent.parent))

from sim.simulink_bridge import SimulinkBridge, _prepare_matlab_root


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

    def find_system(self, _model_name, *args, **kwargs):
        block_type = None
        for index in range(0, len(args), 2):
            if index + 1 >= len(args):
                break
            if args[index] == "BlockType":
                block_type = args[index + 1]
                break
        if not block_type:
            return []
        return [
            path for path, meta in self.blocks.items()
            if meta.get("BlockType") == block_type
        ]

    def get_param(self, block_path, parameter_name, nargout=1):
        return self.blocks[block_path][parameter_name]

    def eval(self, expression, nargout=0):
        self.eval_calls.append((expression, nargout))
        return None


class SimulinkBridgeCompatTests(unittest.TestCase):
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

    def test_find_blocks_by_type_temporarily_suppresses_engine_warnings(self):
        bridge = self._make_bridge({})
        bridge._eng.blocks = {
            "demo/Step": {"BlockType": "Step"},
        }

        blocks = bridge._find_blocks_by_type("Step")

        self.assertEqual(blocks, ["demo/Step"])
        self.assertEqual(
            bridge._eng.eval_calls,
            [
                ("warning('off','all');", 0),
                ("warning('on','all');", 0),
            ],
        )

    def test_prepare_matlab_root_prepends_runtime_paths(self):
        original_sys_path = list(sys.path)
        original_path = os.environ.get("PATH")
        original_mwe_install = os.environ.get("MWE_INSTALL")

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                matlab_root = Path(temp_dir) / "MATLAB" / "R2022b"
                (matlab_root / "bin" / "win64").mkdir(parents=True)
                (matlab_root / "extern" / "bin" / "win64").mkdir(parents=True)
                (
                    matlab_root
                    / "extern"
                    / "engines"
                    / "python"
                    / "dist"
                    / "matlab"
                    / "engine"
                    / "win64"
                ).mkdir(parents=True)

                with patch("sim.simulink_bridge.os.add_dll_directory", Mock(), create=True):
                    _prepare_matlab_root(str(matlab_root))

                self.assertEqual(os.environ.get("MWE_INSTALL"), str(matlab_root))
                self.assertEqual(
                    sys.path[0],
                    str(matlab_root / "extern" / "bin" / "win64"),
                )
                self.assertEqual(
                    sys.path[1],
                    str(
                        matlab_root
                        / "extern"
                        / "engines"
                        / "python"
                        / "dist"
                        / "matlab"
                        / "engine"
                        / "win64"
                    ),
                )
                self.assertEqual(
                    sys.path[2],
                    str(matlab_root / "extern" / "engines" / "python" / "dist"),
                )
                self.assertTrue(
                    os.environ.get("PATH", "").startswith(
                        str(matlab_root / "extern" / "bin" / "win64")
                    )
                )
        finally:
            sys.path[:] = original_sys_path
            if original_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = original_path
            if original_mwe_install is None:
                os.environ.pop("MWE_INSTALL", None)
            else:
                os.environ["MWE_INSTALL"] = original_mwe_install


if __name__ == "__main__":
    unittest.main()
