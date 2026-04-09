import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

from sim.controller_io import SimulinkControllerIO


class SimulinkControllerIOTests(unittest.TestCase):
    def _make_controller_io(self, *, sim_output=None, params=None):
        sim_output = sim_output or {}
        params = params or {}
        set_param_calls: list[tuple[str, str, str]] = []

        def try_engine_method(method_name, *args, **kwargs):
            if method_name == "get_param":
                return params.get((args[0], args[1]))
            if method_name == "get":
                obj, field_name = args
                if isinstance(obj, dict):
                    return obj.get(field_name)
            if method_name == "isa":
                obj, class_name = args
                return bool(
                    class_name == "timeseries"
                    and isinstance(obj, dict)
                    and "Time" in obj
                    and "Data" in obj
                )
            return None

        def call_engine_method(method_name, *args, **kwargs):
            if method_name == "set_param":
                set_param_calls.append((args[0], args[1], args[2]))
                return None
            if method_name == "fieldnames":
                obj = args[0]
                return list(obj.keys()) if isinstance(obj, dict) else []
            if method_name == "get":
                obj, field_name = args
                if isinstance(obj, dict):
                    return obj.get(field_name)
            raise RuntimeError(method_name)

        def get_field_or_none(obj, field_name, allow_get=False):
            if isinstance(obj, dict) and field_name in obj:
                return obj[field_name]
            return None

        controller_io = SimulinkControllerIO(
            try_engine_method=try_engine_method,
            call_engine_method=call_engine_method,
            get_field_or_none=get_field_or_none,
            is_timeseries_object=lambda obj: bool(
                isinstance(obj, dict) and "Time" in obj and "Data" in obj
            ),
            to_float_series=lambda raw: [float(item[0]) if isinstance(item, list) else float(item) for item in raw] if isinstance(raw, list) else [],
            to_string_list=lambda raw: [str(item) for item in raw],
        )
        return controller_io, set_param_calls

    def test_read_controller_gain_supports_kp_ki_kd(self):
        controller_io, _set_param_calls = self._make_controller_io(
            params={
                ("demo/PID", "Kp"): "1.0",
                ("demo/PID", "Ki"): "0.2",
                ("demo/PID", "Kd"): "0.05",
            }
        )

        value = controller_io.read_controller_gain(
            gain_key="p",
            default=0.0,
            separate_gain_paths={"p": "", "i": "", "d": ""},
            pid_block_path="demo/PID",
            pid_block_paths=["demo/PID"],
        )

        self.assertEqual(value, 1.0)

    def test_write_controller_gain_uses_split_gain_paths(self):
        controller_io, set_param_calls = self._make_controller_io(
            params={("demo/P", "Gain"): "1.0"}
        )

        controller_io.write_controller_gain(
            gain_key="p",
            value=2.5,
            separate_gain_paths={"p": "demo/P", "i": "", "d": ""},
            pid_block_path="",
            pid_block_paths=[],
        )

        self.assertEqual(set_param_calls, [("demo/P", "Gain", "2.5")])

    def test_resolve_named_signal_reads_logsout_dataset(self):
        controller_io, _set_param_calls = self._make_controller_io()
        sim_out = {
            "logsout": {
                "y_out": {
                    "Time": [[0.0], [1.0]],
                    "Data": [[10.0], [20.0]],
                }
            }
        }

        resolved = controller_io.resolve_named_signal(
            sim_out,
            "y_out",
            candidates=["y_out"],
        )

        self.assertEqual(resolved.name, "y_out")
        self.assertIn("Data", resolved.container)

    def test_extract_signal_series_falls_back_to_tout_for_array_data(self):
        controller_io, _set_param_calls = self._make_controller_io()
        sim_out = {
            "out": {
                "y_out": [[10.0], [20.0]],
                "tout": [[0.0], [2.0]],
            }
        }
        signal_container = sim_out["out"]["y_out"]

        time_values, output_values = controller_io.extract_signal_series(
            signal_container,
            sim_out,
        )

        self.assertEqual(time_values, [0.0, 2.0])
        self.assertEqual(output_values, [10.0, 20.0])


if __name__ == "__main__":
    unittest.main()
