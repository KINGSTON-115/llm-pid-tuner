import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent))

import launcher
import tuner


class LauncherDispatchTests(unittest.TestCase):
    def test_main_initializes_config_before_dispatch(self):
        with patch.object(launcher, "initialize_runtime_config") as init_config:
            with patch.object(launcher, "dispatch") as dispatch:
                launcher.main([])

        init_config.assert_called_once_with(create_if_missing=True, verbose=True)
        dispatch.assert_called_once()

    def test_sim_mode_routes_to_simulator(self):
        with patch.object(launcher, "run_simulation") as run_simulation:
            with patch.object(launcher, "run_tuner") as run_tuner:
                launcher.main(["sim"])

        run_simulation.assert_called_once_with(False, lang=None)
        run_tuner.assert_not_called()

    def test_plain_flag_without_mode_runs_plain_simulator(self):
        with patch.object(launcher, "run_simulation") as run_simulation:
            with patch.object(launcher, "run_tuner") as run_tuner:
                launcher.main(["--plain"])

        run_simulation.assert_called_once_with(True, lang=None)
        run_tuner.assert_not_called()

    def test_lang_flag_is_forwarded_to_simulator(self):
        with patch.object(launcher, "run_simulation") as run_simulation:
            launcher.main(["sim", "--lang", "en"])

        run_simulation.assert_called_once_with(False, lang="en")

    def test_legacy_serial_port_routes_to_tuner(self):
        with patch.object(launcher, "run_tuner") as run_tuner:
            with patch.object(launcher, "run_simulation") as run_simulation:
                launcher.main(["COM7"])

        run_tuner.assert_called_once_with(["COM7"])
        run_simulation.assert_not_called()

    def test_interactive_prompt_can_start_hardware_mode(self):
        with patch.object(launcher, "can_prompt", return_value=True):
            with patch.object(
                launcher, "prompt_launch_mode", return_value=launcher.MODE_TUNE
            ):
                with patch.object(launcher, "run_tuner") as run_tuner:
                    with patch.object(launcher, "run_simulation") as run_simulation:
                        launcher.main([])

        run_tuner.assert_called_once_with([])
        run_simulation.assert_not_called()


class TunerArgTests(unittest.TestCase):
    def test_cli_serial_port_wins_over_config_prompt(self):
        with patch.dict(tuner.CONFIG, {"SERIAL_PORT": "COM9"}, clear=False):
            with patch.object(tuner, "select_serial_port") as select_serial_port:
                serial_port = tuner.resolve_serial_port("COM5")

        self.assertEqual(serial_port, "COM5")
        select_serial_port.assert_not_called()

    def test_config_serial_port_is_reused_when_user_accepts(self):
        with patch.dict(tuner.CONFIG, {"SERIAL_PORT": "COM9"}, clear=False):
            with patch("builtins.input", return_value=""):
                serial_port = tuner.resolve_serial_port(None)

        self.assertEqual(serial_port, "COM9")

    def test_auto_serial_port_falls_back_to_selector(self):
        with patch.dict(tuner.CONFIG, {"SERIAL_PORT": "AUTO"}, clear=False):
            with patch.object(
                tuner, "select_serial_port", return_value="COM11"
            ) as select_serial_port:
                serial_port = tuner.resolve_serial_port(None)

        self.assertEqual(serial_port, "COM11")
        select_serial_port.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
