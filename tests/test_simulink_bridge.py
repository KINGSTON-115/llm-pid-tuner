import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent))

from sim.simulink_bridge import SimulinkBridge


class _FakeEngine:
    def __init__(self, sim_output):
        self._sim_output = sim_output
        self.blocks = {}
        self.set_param_calls = []

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


class SimulinkBridgeCompatTests(unittest.TestCase):
    def _make_bridge(self, sim_output):
        with patch("sim.simulink_bridge._MATLAB_AVAILABLE", True):
            bridge = SimulinkBridge(
                model_path="C:/models/demo.slx",
                setpoint=200.0,
                pid_block_path="demo/PID Controller",
                output_signal="y_out",
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


if __name__ == "__main__":
    unittest.main()
