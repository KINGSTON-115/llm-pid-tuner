import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

from sim.simulink_setup import (
    SimulinkRuntimeConfig,
    _normalized_string_list,
    load_simulink_runtime_config,
    validate_simulink_runtime_config,
)


def _minimal_valid_config() -> dict:
    return {
        "MATLAB_MODEL_PATH": "model.slx",
        "MATLAB_PID_BLOCK_PATH": "model/PID",
        "MATLAB_OUTPUT_SIGNAL": "temperature",
        "MATLAB_SIM_STEP_TIME": 10.0,
        "MATLAB_SETPOINT": 200.0,
    }


class NormalizedStringListTests(unittest.TestCase):
    def test_returns_list_of_stripped_non_empty(self):
        self.assertEqual(
            _normalized_string_list(["a", "  b  ", "", "c"]),
            ["a", "b", "c"],
        )

    def test_non_list_returns_empty(self):
        self.assertEqual(_normalized_string_list("not a list"), [])
        self.assertEqual(_normalized_string_list(None), [])
        self.assertEqual(_normalized_string_list(42), [])


class LoadSimulinkRuntimeConfigTests(unittest.TestCase):
    def test_minimal_config_loads(self):
        settings = load_simulink_runtime_config(_minimal_valid_config())
        self.assertIsInstance(settings, SimulinkRuntimeConfig)
        self.assertEqual(settings.model_path, "model.slx")
        self.assertEqual(settings.pid_block_path, "model/PID")
        self.assertEqual(settings.output_signal, "temperature")
        self.assertEqual(settings.sim_step_time, 10.0)
        self.assertEqual(settings.setpoint, 200.0)

    def test_strips_string_fields(self):
        config = _minimal_valid_config()
        config["MATLAB_MODEL_PATH"] = "  model.slx  "
        settings = load_simulink_runtime_config(config)
        self.assertEqual(settings.model_path, "model.slx")

    def test_invalid_numeric_raises_value_error(self):
        config = _minimal_valid_config()
        config["MATLAB_SIM_STEP_TIME"] = "not-a-number"
        with self.assertRaises(ValueError):
            load_simulink_runtime_config(config)

    def test_missing_defaults_applied(self):
        settings = load_simulink_runtime_config(
            {"MATLAB_PID_BLOCK_PATH": "m/PID", "MATLAB_OUTPUT_SIGNAL": "out"}
        )
        self.assertEqual(settings.model_path, "")
        self.assertEqual(settings.sim_step_time, 10.0)
        self.assertEqual(settings.setpoint, 200.0)

    def test_pid_block_paths_normalized(self):
        config = _minimal_valid_config()
        config["MATLAB_PID_BLOCK_PATHS"] = ["a/PID", "  b/PID  ", ""]
        settings = load_simulink_runtime_config(config)
        self.assertEqual(settings.pid_block_paths, ["a/PID", "b/PID"])


class ValidateSimulinkRuntimeConfigTests(unittest.TestCase):
    def test_valid_config_returns_none(self):
        settings = load_simulink_runtime_config(_minimal_valid_config())
        self.assertIsNone(validate_simulink_runtime_config(settings))

    def test_missing_controller_path_allows_runtime_auto_discovery(self):
        config = _minimal_valid_config()
        config.pop("MATLAB_PID_BLOCK_PATH")
        settings = load_simulink_runtime_config(config)
        error = validate_simulink_runtime_config(settings)
        self.assertIsNone(error)

    def test_missing_output_signal_returns_error(self):
        config = _minimal_valid_config()
        config.pop("MATLAB_OUTPUT_SIGNAL")
        settings = load_simulink_runtime_config(config)
        error = validate_simulink_runtime_config(settings)
        self.assertIsNotNone(error)
        self.assertIn("OUTPUT_SIGNAL", error)

    def test_p_block_path_alone_is_valid(self):
        config = {
            "MATLAB_MODEL_PATH": "model.slx",
            "MATLAB_P_BLOCK_PATH": "model/P",
            "MATLAB_OUTPUT_SIGNAL": "out",
        }
        settings = load_simulink_runtime_config(config)
        self.assertIsNone(validate_simulink_runtime_config(settings))


if __name__ == "__main__":
    unittest.main()
