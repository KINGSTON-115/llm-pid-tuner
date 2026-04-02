import os
import shutil
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).parent.parent))

import sim.simulink_bridge as simulink_bridge
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
        if include_runtime_dirs:
            (matlab_root / "runtime" / "win64").mkdir(parents=True)
            (matlab_root / "sys" / "os" / "win64").mkdir(parents=True)

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
        original_matlab_root = os.environ.get("MATLAB_ROOT")
        temp_root = None

        try:
            temp_root = self._make_temp_matlab_root("test_prepare_matlab_root")
            matlab_root = temp_root / "MATLAB" / "R2022b"
            self._populate_matlab_root(matlab_root, include_runtime_dirs=True)

            with patch("sim.simulink_bridge.os.add_dll_directory", Mock(), create=True):
                _prepare_matlab_root(str(matlab_root))

            self.assertEqual(os.environ.get("MWE_INSTALL"), str(matlab_root))
            self.assertEqual(os.environ.get("MATLAB_ROOT"), str(matlab_root))
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
            matlab_paths = [
                path
                for path in os.environ.get("PATH", "").split(os.pathsep)
                if path
            ]
            self.assertEqual(matlab_paths[0], str(matlab_root / "extern" / "bin" / "win64"))
            self.assertIn(
                str(matlab_root / "extern" / "engines" / "python" / "dist"),
                matlab_paths,
            )
            self.assertIn(
                str(matlab_root / "extern" / "engines" / "python" / "dist" / "matlab"),
                matlab_paths,
            )
            self.assertIn(
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
                matlab_paths,
            )
            self.assertIn(str(matlab_root / "bin" / "win64"), matlab_paths)
            self.assertIn(str(matlab_root / "runtime" / "win64"), matlab_paths)
            self.assertIn(str(matlab_root / "sys" / "os" / "win64"), matlab_paths)
            self.assertIn(str(matlab_root / "bin"), matlab_paths)
        finally:
            if temp_root is not None:
                shutil.rmtree(temp_root, ignore_errors=True)
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

            with patch("sim.simulink_bridge.os.add_dll_directory", Mock(), create=True):
                with patch(
                    "sim.simulink_bridge.importlib.import_module",
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
