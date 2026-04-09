from contextlib import ExitStack
import importlib.util
import io
import sys
import threading
import time
import unittest
from pathlib import Path
from queue import Queue
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent))

TEXTUAL_AVAILABLE = importlib.util.find_spec("textual") is not None

if TEXTUAL_AVAILABLE:
    from textual.widgets import RichLog, Static

import simulator
from doctor import DoctorCheck
from sim.model import HeatingSimulator, SETPOINT
from sim.prompt_context import build_simulink_prompt_context
from sim.runtime import (
    EVENT_DECISION,
    EVENT_LIFECYCLE,
    EVENT_LOG,
    EVENT_ROLLBACK,
    EVENT_ROUND_METRICS,
    EVENT_SAMPLE,
    QueueEventSink,
    SimulationController,
    drain_event_queue,
)

DEFAULT_DOCTOR_CHECKS = [DoctorCheck("api", "PASS", "ok")]


class QueueEventSinkTests(unittest.TestCase):
    def test_collects_expected_event_shapes(self):
        event_queue = Queue()
        sink = QueueEventSink(event_queue)

        sink.publish(
            EVENT_SAMPLE,
            timestamp=1.0,
            setpoint=200.0,
            input=120.0,
            pwm=255.0,
            error=80.0,
            p=1.0,
            i=0.1,
            d=0.05,
        )
        sink.publish(
            EVENT_DECISION,
            round=1,
            action="BOOST_RESPONSE",
            analysis_summary="Increase P slightly.",
            fallback_used=False,
            guardrail_notes=[],
        )
        sink.publish(
            EVENT_ROLLBACK,
            round=2,
            target_round=1,
            pid={"p": 1.0, "i": 0.1, "d": 0.05},
            reason="Regression detected.",
        )
        sink.publish(
            EVENT_LIFECYCLE,
            phase="completed",
            message="Finished.",
            elapsed_sec=1.5,
        )

        events = drain_event_queue(event_queue)
        self.assertEqual(
            [event["type"] for event in events],
            [EVENT_SAMPLE, EVENT_DECISION, EVENT_ROLLBACK, EVENT_LIFECYCLE],
        )
        self.assertEqual(events[0]["setpoint"], 200.0)
        self.assertEqual(events[1]["action"], "BOOST_RESPONSE")
        self.assertEqual(events[2]["target_round"], 1)
        self.assertEqual(events[3]["phase"], "completed")

    def test_sequence_snapshot_tracks_latest_event(self):
        event_queue = Queue()
        sink = QueueEventSink(event_queue)
        sink.publish(EVENT_LIFECYCLE, phase="a", message="one", elapsed_sec=0.1)
        snapshot = sink.snapshot_sequence()
        sink.publish(EVENT_LIFECYCLE, phase="b", message="two", elapsed_sec=0.2)

        self.assertEqual(snapshot, 1)
        self.assertEqual(drain_event_queue(event_queue)[1]["seq"], 2)


class SimulationControllerTests(unittest.TestCase):
    def test_wait_until_running_blocks_until_resume(self):
        controller = SimulationController()
        controller.pause()
        resumed = []

        def worker():
            resumed.append(controller.wait_until_running(poll_interval=0.01))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=0.05)
        self.assertTrue(thread.is_alive())

        controller.resume()
        thread.join(timeout=0.2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(resumed, [True])


class SimulatorLoopTests(unittest.TestCase):
    def test_run_tuning_loop_emits_core_events(self):
        event_queue = Queue()
        event_sink = QueueEventSink(event_queue)
        controller = SimulationController()
        captured = {}

        class FakeTuner:
            def __init__(self, *_args, **_kwargs):
                pass

            def analyze(
                self,
                _prompt_data,
                _history_text,
                tuning_mode="generic",
                prompt_context=None,
            ):
                captured["tuning_mode"] = tuning_mode
                captured["prompt_context"] = prompt_context
                return {
                    "analysis_summary": "Stop after one round.",
                    "tuning_action": "HOLD",
                    "p": 1.2,
                    "i": 0.1,
                    "d": 0.05,
                    "status": "DONE",
                }

        with patch.object(simulator, "LLMTuner", FakeTuner):
            with patch.dict(
                simulator.CONFIG,
                {"BUFFER_SIZE": 5, "MAX_TUNING_ROUNDS": 2},
                clear=False,
            ):
                result = simulator._run_tuning_loop(
                    HeatingSimulator(random_seed=9),
                    SETPOINT,
                    "Python",
                    event_sink=event_sink,
                    controller=controller,
                    emit_console=False,
                )

        events = drain_event_queue(event_queue)
        event_types = {event["type"] for event in events}
        self.assertIn(EVENT_SAMPLE, event_types)
        self.assertIn(EVENT_ROUND_METRICS, event_types)
        self.assertIn(EVENT_DECISION, event_types)
        self.assertIn(EVENT_LIFECYCLE, event_types)
        self.assertGreaterEqual(result["rounds_completed"], 1)
        self.assertEqual(captured["tuning_mode"], "python_sim")
        self.assertEqual(
            captured["prompt_context"]["source"],
            "built_in_python_heating_simulator",
        )

    def test_run_tuning_loop_ignores_llm_done_when_early_exit_disabled(self):
        class FakeTuner:
            def __init__(self, *_args, **_kwargs):
                pass

            def analyze(
                self,
                _prompt_data,
                _history_text,
                tuning_mode="generic",
                prompt_context=None,
            ):
                return {
                    "analysis_summary": "Keep tuning despite DONE flag.",
                    "tuning_action": "HOLD",
                    "p": 1.0,
                    "i": 0.1,
                    "d": 0.05,
                    "status": "DONE",
                }

        with patch.object(simulator, "LLMTuner", FakeTuner):
            with patch.dict(
                simulator.CONFIG,
                {"BUFFER_SIZE": 5, "MAX_TUNING_ROUNDS": 2},
                clear=False,
            ):
                result = simulator._run_tuning_loop(
                    HeatingSimulator(random_seed=7),
                    SETPOINT,
                    "Simulink",
                    llm_mode="simulink",
                    emit_console=False,
                    disable_early_exit=True,
                )

        self.assertEqual(result["rounds_completed"], 2)
        self.assertEqual(result["completed_reason"], "max_rounds_reached")

    def test_run_tuning_loop_refreshes_simulink_prompt_context_before_llm_request(self):
        captured = {}

        class FakeSim:
            def __init__(self):
                self.kp = 1.0
                self.ki = 0.1
                self.kd = 0.05
                self.model_path = "C:/models/demo.slx"
                self.pid_block_path = "demo/Outer PID"
                self.output_signal = "y_out"
                self.sim_step_time = 5.0
                self.control_signal = "u_cmd"
                self.setpoint_block = ""
                self.resolved_output_signal = ""
                self.resolved_control_signal = ""
                self.has_control_signal = False
                self.secondary_pid_block_path = ""
                self.control_domain = "discrete"
                self.model_solver_type = "Fixed-step"
                self.model_solver_name = "discrete"
                self.model_fixed_step = "0.01"
                self.controller_1_sample_time = "0.01"
                self.controller_2_sample_time = ""
                self._last_data = []

            def run_step(self):
                self.secondary_pid_block_path = "demo/Inner PID"
                self.setpoint_block = "demo/Resolved Setpoint"
                self.resolved_output_signal = "plant_y"
                self.resolved_control_signal = "u_out"
                self.has_control_signal = True
                self.controller_2_sample_time = "0.02"
                self._last_data = [
                    {
                        "timestamp": 0.0,
                        "setpoint": SETPOINT,
                        "input": 120.0,
                        "pwm": 0.0,
                        "error": 80.0,
                        "p": self.kp,
                        "i": self.ki,
                        "d": self.kd,
                    },
                    {
                        "timestamp": 100.0,
                        "setpoint": SETPOINT,
                        "input": 135.0,
                        "pwm": 10.0,
                        "error": 65.0,
                        "p": self.kp,
                        "i": self.ki,
                        "d": self.kd,
                    },
                    {
                        "timestamp": 200.0,
                        "setpoint": SETPOINT,
                        "input": 150.0,
                        "pwm": 20.0,
                        "error": 50.0,
                        "p": self.kp,
                        "i": self.ki,
                        "d": self.kd,
                    },
                ]

            def get_data(self):
                return list(self._last_data)

            def set_pid(self, p, i, d):
                self.kp = p
                self.ki = i
                self.kd = d

        class FakeTuner:
            def __init__(self, *_args, **_kwargs):
                pass

            def analyze(
                self,
                _prompt_data,
                _history_text,
                tuning_mode="generic",
                prompt_context=None,
            ):
                captured["tuning_mode"] = tuning_mode
                captured["prompt_context"] = prompt_context
                return {
                    "analysis_summary": "Stop after verifying prompt context.",
                    "tuning_action": "HOLD",
                    "p": 1.0,
                    "i": 0.1,
                    "d": 0.05,
                    "status": "DONE",
                }

        prompt_context = build_simulink_prompt_context(
            model_path="C:/models/demo.slx",
            pid_block_path="demo/Outer PID",
            output_signal="y_out",
            sim_step_time=5.0,
            control_signal="u_cmd",
            output_signal_candidates=["plant_y"],
            pwm_signal_available=False,
        )

        with patch.object(simulator, "LLMTuner", FakeTuner):
            with patch.dict(
                simulator.CONFIG,
                {"BUFFER_SIZE": 3, "MAX_TUNING_ROUNDS": 2},
                clear=False,
            ):
                simulator._run_tuning_loop(
                    FakeSim(),
                    SETPOINT,
                    "Simulink",
                    llm_mode="simulink",
                    prompt_context=prompt_context,
                    emit_console=False,
                    disable_early_exit=True,
                )

        self.assertEqual(captured["tuning_mode"], "simulink")
        self.assertEqual(captured["prompt_context"]["resolved_output_signal"], "plant_y")
        self.assertEqual(captured["prompt_context"]["resolved_control_signal"], "u_out")
        self.assertEqual(captured["prompt_context"]["setpoint_block"], "demo/Resolved Setpoint")
        self.assertEqual(captured["prompt_context"]["controller_2_path"], "demo/Inner PID")
        self.assertEqual(captured["prompt_context"]["controller_count"], 2)
        self.assertEqual(captured["prompt_context"]["output_signal_candidates"], ["plant_y"])
        self.assertTrue(captured["prompt_context"]["pwm_signal_available"])
        self.assertEqual(captured["prompt_context"]["control_domain"], "discrete")
        self.assertEqual(captured["prompt_context"]["model_solver_type"], "Fixed-step")
        self.assertEqual(captured["prompt_context"]["model_fixed_step"], "0.01")
        self.assertEqual(captured["prompt_context"]["controller_1_sample_time"], "0.01")
        self.assertEqual(captured["prompt_context"]["controller_2_sample_time"], "0.02")

    def test_run_simulink_simulation_passes_special_prompt_context(self):
        captured = {}

        class FakeBridge:
            def __init__(
                self,
                model_path,
                setpoint,
                pid_block_path,
                output_signal,
                sim_step_time,
                matlab_root="",
                control_signal="",
                output_signal_candidates=None,
                setpoint_block="",
                pid_block_paths=None,
                p_block_path="",
                i_block_path="",
                d_block_path="",
            ):
                self.model_path = model_path
                self.setpoint = setpoint
                self.pid_block_path = pid_block_path
                self.output_signal = output_signal
                self.matlab_root = matlab_root
                self.sim_step_time = sim_step_time
                self.kp = 1.0
                self.ki = 0.1
                self.kd = 0.05

            def connect(self):
                return None

            def disconnect(self):
                return None

        def fake_run_tuning_loop(
            sim,
            setpoint,
            mode_label,
            llm_mode="generic",
            prompt_context=None,
            **_kwargs,
        ):
            captured["sim"] = sim
            captured["setpoint"] = setpoint
            captured["mode_label"] = mode_label
            captured["llm_mode"] = llm_mode
            captured["prompt_context"] = prompt_context
            return {"mode": "simulink"}

        with patch("sim.simulink_bridge.SimulinkBridge", FakeBridge):
            with patch.object(simulator, "_run_tuning_loop", side_effect=fake_run_tuning_loop):
                with patch.dict(
                    simulator.CONFIG,
                    {
                        "MATLAB_MODEL_PATH": "C:/models/demo.slx",
                        "MATLAB_PID_BLOCK_PATH": "demo/PID Controller",
                        "MATLAB_OUTPUT_SIGNAL": "y_out",
                        "MATLAB_SIM_STEP_TIME": 12.5,
                        "MATLAB_SETPOINT": 180.0,
                    },
                    clear=False,
                ):
                    result = simulator._run_simulink_simulation()

        self.assertEqual(result, {"mode": "simulink"})
        self.assertEqual(captured["mode_label"], "Simulink")
        self.assertEqual(captured["llm_mode"], "simulink")
        self.assertEqual(captured["prompt_context"]["model_path"], "C:/models/demo.slx")
        self.assertFalse(captured["prompt_context"]["pwm_signal_available"])
        self.assertIn("placeholder 0.0", captured["prompt_context"]["pwm_field_note"])

    def test_run_simulink_simulation_passes_dual_controller_context(self):
        captured = {}

        class FakeBridge:
            def __init__(
                self,
                model_path,
                setpoint,
                pid_block_path,
                output_signal,
                sim_step_time,
                matlab_root="",
                control_signal="",
                output_signal_candidates=None,
                setpoint_block="",
                pid_block_paths=None,
                p_block_path="",
                i_block_path="",
                d_block_path="",
            ):
                self.model_path = model_path
                self.setpoint = setpoint
                self.pid_block_path = pid_block_path
                self.output_signal = output_signal
                self.matlab_root = matlab_root
                self.sim_step_time = sim_step_time
                self.kp = 1.0
                self.ki = 0.1
                self.kd = 0.05
                self.resolved_output_signal = output_signal
                self.resolved_control_signal = control_signal
                self.has_control_signal = bool(control_signal)
                self.secondary_pid_block_path = "demo/Inner PID"

            def connect(self):
                return None

            def disconnect(self):
                return None

        def fake_run_tuning_loop(sim, setpoint, mode_label, llm_mode="generic", prompt_context=None, **_kwargs):
            captured["prompt_context"] = prompt_context
            return {"mode": "simulink"}

        with patch("sim.simulink_bridge.SimulinkBridge", FakeBridge):
            with patch.object(simulator, "_run_tuning_loop", side_effect=fake_run_tuning_loop):
                with patch.dict(
                    simulator.CONFIG,
                    {
                        "MATLAB_MODEL_PATH": "C:/models/demo.slx",
                        "MATLAB_PID_BLOCK_PATH": "demo/Outer PID",
                        "MATLAB_PID_BLOCK_PATH_2": "demo/Inner PID",
                        "MATLAB_OUTPUT_SIGNAL": "y_out",
                        "MATLAB_SIM_STEP_TIME": 12.5,
                        "MATLAB_SETPOINT": 180.0,
                    },
                    clear=False,
                ):
                    result = simulator._run_simulink_simulation()

        self.assertEqual(result, {"mode": "simulink"})
        self.assertEqual(captured["prompt_context"]["controller_count"], 2)
        self.assertEqual(captured["prompt_context"]["controller_1_path"], "demo/Outer PID")
        self.assertEqual(captured["prompt_context"]["controller_2_path"], "demo/Inner PID")

    def test_run_simulink_simulation_uses_resolved_bridge_configuration(self):
        captured = {}

        class FakeBridge:
            def __init__(
                self,
                model_path,
                setpoint,
                pid_block_path,
                output_signal,
                sim_step_time,
                matlab_root="",
                control_signal="",
                output_signal_candidates=None,
                setpoint_block="",
                pid_block_paths=None,
                p_block_path="",
                i_block_path="",
                d_block_path="",
            ):
                self.model_path = model_path
                self.setpoint = setpoint
                self.pid_block_path = pid_block_path
                self.output_signal = output_signal
                self.matlab_root = matlab_root
                self.sim_step_time = sim_step_time
                self.kp = 1.0
                self.ki = 0.1
                self.kd = 0.05
                self.resolved_output_signal = output_signal
                self.resolved_control_signal = control_signal
                self.has_control_signal = False
                self.secondary_pid_block_path = ""
                self.setpoint_block = ""

            def connect(self):
                self.pid_block_path = "demo/Resolved Outer PID"
                self.secondary_pid_block_path = "demo/Resolved Inner PID"
                self.setpoint_block = "demo/Resolved Setpoint"
                self.resolved_output_signal = "plant_y"
                self.resolved_control_signal = "u_out"
                self.has_control_signal = True
                return None

            def disconnect(self):
                return None

        def fake_run_tuning_loop(sim, setpoint, mode_label, llm_mode="generic", prompt_context=None, **_kwargs):
            captured["prompt_context"] = prompt_context
            return {"mode": "simulink"}

        with patch("sim.simulink_bridge.SimulinkBridge", FakeBridge):
            with patch.object(simulator, "_run_tuning_loop", side_effect=fake_run_tuning_loop):
                with patch.dict(
                    simulator.CONFIG,
                    {
                        "MATLAB_MODEL_PATH": "C:/models/demo.slx",
                        "MATLAB_PID_BLOCK_PATHS": ["demo/Candidate Outer PID"],
                        "MATLAB_OUTPUT_SIGNAL": "y_out",
                        "MATLAB_OUTPUT_SIGNAL_CANDIDATES": ["plant_y"],
                        "MATLAB_CONTROL_SIGNAL": "u_out",
                        "MATLAB_SIM_STEP_TIME": 12.5,
                        "MATLAB_SETPOINT": 180.0,
                    },
                    clear=False,
                ):
                    result = simulator._run_simulink_simulation()

        self.assertEqual(result, {"mode": "simulink"})
        self.assertEqual(captured["prompt_context"]["controller_count"], 2)
        self.assertEqual(captured["prompt_context"]["pid_block_path"], "demo/Resolved Outer PID")
        self.assertEqual(captured["prompt_context"]["controller_1_path"], "demo/Resolved Outer PID")
        self.assertEqual(captured["prompt_context"]["controller_2_path"], "demo/Resolved Inner PID")
        self.assertEqual(captured["prompt_context"]["setpoint_block"], "demo/Resolved Setpoint")
        self.assertEqual(captured["prompt_context"]["resolved_output_signal"], "plant_y")
        self.assertEqual(captured["prompt_context"]["resolved_control_signal"], "u_out")
        self.assertTrue(captured["prompt_context"]["pwm_signal_available"])

    def test_run_simulink_simulation_emits_error_event_on_connect_failure(self):
        event_queue = Queue()
        event_sink = QueueEventSink(event_queue)

        class FakeBridge:
            def __init__(
                self,
                model_path,
                setpoint,
                pid_block_path,
                output_signal,
                sim_step_time,
                matlab_root="",
                control_signal="",
                output_signal_candidates=None,
                setpoint_block="",
                pid_block_paths=None,
                p_block_path="",
                i_block_path="",
                d_block_path="",
            ):
                pass

            def connect(self):
                raise RuntimeError("connect boom")

            def disconnect(self):
                return None

        with patch("sim.simulink_bridge.SimulinkBridge", FakeBridge):
            with patch.dict(
                simulator.CONFIG,
                {
                    "MATLAB_MODEL_PATH": "C:/models/demo.slx",
                    "MATLAB_PID_BLOCK_PATH": "demo/PID Controller",
                    "MATLAB_OUTPUT_SIGNAL": "y_out",
                    "MATLAB_SIM_STEP_TIME": 12.5,
                    "MATLAB_SETPOINT": 180.0,
                },
                clear=False,
            ):
                result = simulator._run_simulink_simulation(
                    event_sink=event_sink,
                    emit_console=False,
                )

        self.assertIsNone(result)
        events = drain_event_queue(event_queue)
        self.assertTrue(events)
        self.assertEqual(events[-1]["type"], EVENT_LIFECYCLE)
        self.assertEqual(events[-1]["phase"], "error")
        self.assertIn("connect boom", events[-1]["message"])

    def test_run_simulink_simulation_suppresses_plain_output_in_tui_mode(self):
        captured = {}

        class FakeBridge:
            def __init__(
                self,
                model_path,
                setpoint,
                pid_block_path,
                output_signal,
                sim_step_time,
                matlab_root="",
                control_signal="",
                output_signal_candidates=None,
                setpoint_block="",
                pid_block_paths=None,
                p_block_path="",
                i_block_path="",
                d_block_path="",
            ):
                self.kp = 1.0
                self.ki = 0.1
                self.kd = 0.05

            def connect(self):
                print("bridge-connect")

            def disconnect(self):
                print("bridge-disconnect")

        def fake_run_tuning_loop(*_args, **kwargs):
            captured["emit_console"] = kwargs.get("emit_console")
            return {"mode": "simulink"}

        stdout = io.StringIO()
        with patch("sim.simulink_bridge.SimulinkBridge", FakeBridge):
            with patch.object(simulator, "_run_tuning_loop", side_effect=fake_run_tuning_loop):
                with patch.dict(
                    simulator.CONFIG,
                    {
                        "MATLAB_MODEL_PATH": "C:/models/demo.slx",
                        "MATLAB_PID_BLOCK_PATH": "demo/PID Controller",
                        "MATLAB_OUTPUT_SIGNAL": "y_out",
                        "MATLAB_SIM_STEP_TIME": 12.5,
                        "MATLAB_SETPOINT": 180.0,
                    },
                    clear=False,
                ):
                    with patch("sys.stdout", stdout):
                        result = simulator._run_simulink_simulation(emit_console=False)

        self.assertEqual(result, {"mode": "simulink"})
        self.assertFalse(captured["emit_console"])


class TuiModeTests(unittest.TestCase):
    def test_simulink_prompt_defaults_to_plain(self):
        with patch("builtins.input", return_value=""):
            use_tui = simulator.choose_simulink_ui_mode(False)

        self.assertFalse(use_tui)

    def test_simulink_prompt_can_choose_plain_mode(self):
        with patch("builtins.input", return_value="2"):
            use_tui = simulator.choose_simulink_ui_mode(False)

        self.assertFalse(use_tui)

    def test_simulink_prompt_can_choose_tui_mode(self):
        with patch("builtins.input", return_value="1"):
            use_tui = simulator.choose_simulink_ui_mode(False)

        self.assertTrue(use_tui)

    def test_simulink_prompt_uses_plain_on_eof(self):
        with patch("builtins.input", side_effect=EOFError):
            use_tui = simulator.choose_simulink_ui_mode(False)

        self.assertFalse(use_tui)

    def test_plain_mode_uses_plain_runner(self):
        doctor_checks = [DoctorCheck("api", "PASS", "ok")]
        with patch.object(simulator, "ensure_runtime_config"):
            with patch.object(simulator, "collect_doctor_checks", return_value=doctor_checks):
                with patch.object(simulator, "print_doctor_report") as doctor_report:
                    with patch.dict(simulator.CONFIG, {"MATLAB_MODEL_PATH": ""}, clear=False):
                        with patch.object(simulator, "_run_python_simulation_plain", return_value={"mode": "plain"}) as plain:
                            with patch.object(simulator, "_run_python_simulation_with_tui") as tui:
                                result = simulator.run_simulation(force_plain=True)

        self.assertEqual(result, {"mode": "plain"})
        doctor_report.assert_called_once_with(doctor_checks)
        plain.assert_called_once_with(warm_start=True, doctor_checks=doctor_checks)

    def test_python_plain_runner_uses_configured_setpoint_and_initial_pid(self):
        captured = {}

        def fake_run_tuning_loop(
            sim,
            setpoint,
            mode_label,
            **_kwargs,
        ):
            captured["sim"] = sim
            captured["setpoint"] = setpoint
            captured["mode_label"] = mode_label
            return {"mode": "plain"}

        with patch.object(simulator, "_run_tuning_loop", side_effect=fake_run_tuning_loop):
            with patch.dict(
                simulator.CONFIG,
                {"MATLAB_SETPOINT": 180.0, "LLM_MODEL_NAME": "demo-model"},
                clear=False,
            ):
                result = simulator._run_python_simulation_plain(
                    warm_start=True,
                    doctor_checks=[],
                    initial_pid={"p": 2.0, "i": 0.3, "d": 0.1},
                )

        self.assertEqual(result, {"mode": "plain"})
        self.assertEqual(captured["setpoint"], 180.0)
        self.assertEqual(captured["mode_label"], "Python")
        self.assertEqual(captured["sim"].setpoint, 180.0)
        self.assertEqual(captured["sim"].kp, 2.0)
        self.assertEqual(captured["sim"].ki, 0.3)
        self.assertEqual(captured["sim"].kd, 0.1)

    def test_python_plain_runner_merges_pre_tuning_preferences(self):
        captured = {}
        preference_context = {
            "user_preference_summary": "Priority=Low overshoot; Max overshoot=2.0%; Aggressiveness=Conservative",
            "user_goal_priority": "low_overshoot",
        }

        def fake_run_tuning_loop(
            sim,
            setpoint,
            mode_label,
            prompt_context=None,
            **_kwargs,
        ):
            captured["prompt_context"] = prompt_context
            return {"mode": "plain"}

        with patch.object(simulator, "_run_tuning_loop", side_effect=fake_run_tuning_loop):
            result = simulator._run_python_simulation_plain(
                warm_start=False,
                doctor_checks=[],
                prompt_context_overrides=preference_context,
            )

        self.assertEqual(result, {"mode": "plain"})
        self.assertEqual(
            captured["prompt_context"]["user_preference_summary"],
            preference_context["user_preference_summary"],
        )
        self.assertEqual(
            captured["prompt_context"]["user_goal_priority"],
            "low_overshoot",
        )
        self.assertEqual(
            captured["prompt_context"]["source"],
            "built_in_python_heating_simulator",
        )


class RunSimulationDispatchTests(unittest.TestCase):
    def _run_with_runtime_patches(
        self,
        *,
        config_updates: dict[str, object],
        patches: dict[str, dict[str, object]],
        force_plain: bool = False,
    ) -> tuple[dict[str, object] | None, object, dict[str, object]]:
        with ExitStack() as stack:
            stack.enter_context(patch.object(simulator, "ensure_runtime_config"))
            stack.enter_context(
                patch.object(
                    simulator,
                    "collect_doctor_checks",
                    return_value=DEFAULT_DOCTOR_CHECKS,
                )
            )
            doctor_report = stack.enter_context(
                patch.object(simulator, "print_doctor_report")
            )
            stack.enter_context(patch.dict(simulator.CONFIG, config_updates, clear=False))

            patched: dict[str, object] = {}
            for name, patch_kwargs in patches.items():
                patched[name] = stack.enter_context(
                    patch.object(simulator, name, **patch_kwargs)
                )

            result = simulator.run_simulation(force_plain=force_plain)

        return result, doctor_report, patched

    def test_default_mode_uses_doctor_and_warm_start(self):
        result, _doctor_report, patched = self._run_with_runtime_patches(
            config_updates={"MATLAB_MODEL_PATH": ""},
            patches={
                "_run_python_simulation_with_tui": {"return_value": {"mode": "tui"}},
            },
        )

        self.assertEqual(result, {"mode": "tui"})
        patched["_run_python_simulation_with_tui"].assert_called_once_with(
            warm_start=True,
            doctor_checks=DEFAULT_DOCTOR_CHECKS,
        )

    def test_simulink_mode_prefers_tui_when_available(self):
        result, doctor_report, patched = self._run_with_runtime_patches(
            config_updates={"MATLAB_MODEL_PATH": "C:/models/demo.slx"},
            patches={
                "choose_simulink_ui_mode": {"return_value": True},
                "_run_simulink_simulation_with_tui": {
                    "return_value": {"mode": "simulink_tui"}
                },
            },
        )

        self.assertEqual(result, {"mode": "simulink_tui"})
        patched["_run_simulink_simulation_with_tui"].assert_called_once_with(
            doctor_checks=DEFAULT_DOCTOR_CHECKS
        )
        doctor_report.assert_not_called()

    def test_simulink_mode_can_choose_plain_runner(self):
        result, doctor_report, patched = self._run_with_runtime_patches(
            config_updates={"MATLAB_MODEL_PATH": "C:/models/demo.slx"},
            patches={
                "choose_simulink_ui_mode": {"return_value": False},
                "_run_simulink_simulation": {
                    "return_value": {"mode": "simulink_plain"}
                },
            },
        )

        self.assertEqual(result, {"mode": "simulink_plain"})
        doctor_report.assert_called_once_with(DEFAULT_DOCTOR_CHECKS)
        patched["_run_simulink_simulation"].assert_called_once_with(
            doctor_checks=DEFAULT_DOCTOR_CHECKS
        )

    def test_simulink_mode_forwards_pre_tuning_preferences(self):
        preference_context = {
            "user_preference_summary": "Priority=Fast response; Max overshoot=10.0%; Aggressiveness=Aggressive",
        }
        with patch.object(simulator, "ensure_runtime_config"):
            with patch.object(
                simulator,
                "collect_doctor_checks",
                return_value=DEFAULT_DOCTOR_CHECKS,
            ):
                with patch.object(simulator, "collect_pre_tuning_preferences", return_value=preference_context):
                    with patch.object(simulator, "choose_simulink_ui_mode", return_value=False):
                        with patch.object(simulator, "print_doctor_report"):
                            with patch.dict(
                                simulator.CONFIG,
                                {"MATLAB_MODEL_PATH": "C:/models/demo.slx"},
                                clear=False,
                            ):
                                with patch.object(
                                    simulator,
                                    "_run_simulink_simulation",
                                    return_value={"mode": "simulink_plain"},
                                ) as plain:
                                    result = simulator.run_simulation(force_plain=False)

        self.assertEqual(result, {"mode": "simulink_plain"})
        plain.assert_called_once_with(
            doctor_checks=DEFAULT_DOCTOR_CHECKS,
            prompt_context_overrides=preference_context,
        )

    def test_python_plain_mode_forwards_pre_tuning_preferences(self):
        doctor_checks = [DoctorCheck("api", "PASS", "ok")]
        preference_context = {
            "user_preference_summary": "Priority=Balanced; Max overshoot=5.0%; Aggressiveness=Normal",
        }
        with patch.object(simulator, "ensure_runtime_config"):
            with patch.object(simulator, "collect_doctor_checks", return_value=doctor_checks):
                with patch.object(simulator, "collect_pre_tuning_preferences", return_value=preference_context):
                    with patch.object(simulator, "print_doctor_report"):
                        with patch.dict(simulator.CONFIG, {"MATLAB_MODEL_PATH": ""}, clear=False):
                            with patch.object(
                                simulator,
                                "_run_python_simulation_plain",
                                return_value={"mode": "plain"},
                            ) as plain:
                                result = simulator.run_simulation(force_plain=True)

        self.assertEqual(result, {"mode": "plain"})
        plain.assert_called_once_with(
            warm_start=True,
            doctor_checks=doctor_checks,
            prompt_context_overrides=preference_context,
        )

    def test_tui_failure_falls_back_to_plain_runner(self):
        result, doctor_report, patched = self._run_with_runtime_patches(
            config_updates={"MATLAB_MODEL_PATH": "", "LLM_DEBUG_OUTPUT": False},
            patches={
                "_run_python_simulation_with_tui": {
                    "side_effect": RuntimeError("tui boom")
                },
                "_run_python_simulation_plain": {"return_value": {"mode": "plain"}},
            },
        )

        self.assertEqual(result, {"mode": "plain"})
        doctor_report.assert_called_once_with(DEFAULT_DOCTOR_CHECKS)
        patched["_run_python_simulation_plain"].assert_called_once_with(
            warm_start=True,
            doctor_checks=DEFAULT_DOCTOR_CHECKS,
        )

    def test_simulink_tui_failure_falls_back_to_plain_runner(self):
        result, doctor_report, patched = self._run_with_runtime_patches(
            config_updates={
                "MATLAB_MODEL_PATH": "C:/models/demo.slx",
                "LLM_DEBUG_OUTPUT": False,
            },
            patches={
                "choose_simulink_ui_mode": {"return_value": True},
                "_run_simulink_simulation_with_tui": {
                    "side_effect": RuntimeError("tui boom")
                },
                "_run_simulink_simulation": {
                    "return_value": {"mode": "simulink_plain"}
                },
            },
        )

        self.assertEqual(result, {"mode": "simulink_plain"})
        doctor_report.assert_called_once_with(DEFAULT_DOCTOR_CHECKS)
        patched["_run_simulink_simulation"].assert_called_once_with(
            doctor_checks=DEFAULT_DOCTOR_CHECKS
        )


class PanelStateTests(unittest.TestCase):
    def test_replace_last_log_event_updates_existing_stream_line(self):
        from sim.tui import PanelState

        state = PanelState()
        state.apply_event(
            {
                "type": EVENT_LOG,
                "label": "llm_stream",
                "message": '{"thought_process":"hel',
                "replace_last": True,
                "stream_id": 1,
            }
        )
        state.apply_event(
            {
                "type": EVENT_LOG,
                "label": "llm_stream",
                "message": '{"thought_process":"hello"}',
                "replace_last": True,
                "stream_id": 1,
            }
        )

        self.assertEqual(len(state.event_history), 1)
        self.assertIn("hello", state.render_event_lines()[0])

    def test_sample_event_without_p2_keeps_secondary_pid_none(self):
        from sim.tui import PanelState

        state = PanelState()
        state.apply_event(
            {
                "type": EVENT_SAMPLE,
                "timestamp": 0.0,
                "setpoint": 200.0,
                "input": 150.0,
                "pwm": 128.0,
                "error": 50.0,
                "p": 1.2,
                "i": 0.3,
                "d": 0.05,
            }
        )
        self.assertIsNone(state.secondary_pid)
        rendered = state.render_status_text()
        # Single-loop render must not show C1/C2 labels
        self.assertNotIn(" C1", rendered)
        self.assertNotIn(" C2", rendered)

    def test_sample_event_with_p2_populates_secondary_pid(self):
        from sim.tui import PanelState

        state = PanelState()
        state.apply_event(
            {
                "type": EVENT_SAMPLE,
                "timestamp": 0.0,
                "setpoint": 200.0,
                "input": 150.0,
                "pwm": 128.0,
                "error": 50.0,
                "p": 1.2,
                "i": 0.3,
                "d": 0.05,
                "p2": 3.4,
                "i2": 0.7,
                "d2": 0.09,
            }
        )
        self.assertEqual(state.current_pid, {"p": 1.2, "i": 0.3, "d": 0.05})
        self.assertEqual(state.secondary_pid, {"p": 3.4, "i": 0.7, "d": 0.09})
        rendered = state.render_status_text()
        # Dual-loop render shows both controllers
        self.assertIn(" C1", rendered)
        self.assertIn(" C2", rendered)
        self.assertIn("3.4000", rendered)
        self.assertIn("0.0900", rendered)
        summary = state.render_summary_text()
        self.assertIn("C2:", summary)
        self.assertIn("3.4000", summary)

    def test_sample_event_dual_then_single_keeps_secondary_sticky(self):
        """Once dual PID is seen, a later single-PID sample must not clobber it.

        Rationale: in a dual-controller run, the bridge emits samples with
        p2/i2/d2 on every step. If for any reason one sample omits the
        secondary fields (e.g. a transient read error), the TUI should not
        suddenly drop the whole C2 line - it should keep the last known
        values. We achieve stickiness by only updating secondary_pid when
        p2 is present in the event.
        """
        from sim.tui import PanelState

        state = PanelState()
        state.apply_event(
            {"type": EVENT_SAMPLE, "p": 1.0, "i": 0.1, "d": 0.01,
             "p2": 2.0, "i2": 0.2, "d2": 0.02}
        )
        state.apply_event(
            {"type": EVENT_SAMPLE, "p": 1.1, "i": 0.11, "d": 0.011}
        )
        self.assertEqual(state.current_pid, {"p": 1.1, "i": 0.11, "d": 0.011})
        self.assertEqual(state.secondary_pid, {"p": 2.0, "i": 0.2, "d": 0.02})


@unittest.skipUnless(TEXTUAL_AVAILABLE, "textual is required")
class TextualDashboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_textual_app_updates_and_handles_shortcuts(self):
        from sim.tui import SimulationTUIApp

        event_queue = Queue()
        event_sink = QueueEventSink(event_queue)
        controller = SimulationController()
        app = SimulationTUIApp(
            event_queue=event_queue,
            controller=controller,
            worker_target=None,
            event_sink=event_sink,
            mode_label="Python",
        )

        async with app.run_test() as pilot:
            event_sink.publish(
                EVENT_SAMPLE,
                timestamp=1.0,
                setpoint=200.0,
                input=100.0,
                pwm=255.0,
                error=100.0,
                p=1.0,
                i=0.1,
                d=0.05,
            )
            event_sink.publish(
                EVENT_ROUND_METRICS,
                round=1,
                avg_error=2.0,
                max_error=3.0,
                steady_state_error=1.0,
                overshoot=0.5,
                zero_crossings=0,
                status="TUNING",
                stable_rounds=0,
            )
            event_sink.publish(
                EVENT_DECISION,
                round=1,
                action="BOOST_RESPONSE",
                analysis_summary="Increase P slightly.",
                fallback_used=False,
                guardrail_notes=[],
            )
            await pilot.pause()
            await pilot.press("l")
            await pilot.press("p")
            await pilot.press("r")

            help_text = str(app.query_one("#help", Static).content)
            self.assertTrue(app.state.paused)
            self.assertEqual(len(app.state.event_history), 0)
            self.assertEqual(app.state.latest_action, "-")
            self.assertTrue(app.state.detailed_events)
            self.assertGreater(app.state.current_setpoint, 0.0)
            self.assertIn("q", help_text)
            self.assertIn("p", help_text)

    async def test_textual_app_expands_status_panel_for_dual_controller(self):
        from sim.tui import SimulationTUIApp

        event_queue = Queue()
        event_sink = QueueEventSink(event_queue)
        controller = SimulationController()
        app = SimulationTUIApp(
            event_queue=event_queue,
            controller=controller,
            worker_target=None,
            event_sink=event_sink,
            mode_label="Simulink",
        )

        async with app.run_test() as pilot:
            event_sink.publish(
                EVENT_SAMPLE,
                timestamp=1.0,
                setpoint=200.0,
                input=100.0,
                pwm=255.0,
                error=100.0,
                p=1.0,
                i=0.1,
                d=0.05,
                p2=2.0,
                i2=0.2,
                d2=0.02,
            )
            await pilot.pause()
            app._poll_events()

            status = app.query_one("#status", Static)
            self.assertIn("C2", str(status.content))

    async def test_reset_view_ignores_queued_pre_reset_events(self):
        from sim.tui import SimulationTUIApp

        event_queue = Queue()
        event_sink = QueueEventSink(event_queue)
        controller = SimulationController()
        app = SimulationTUIApp(
            event_queue=event_queue,
            controller=controller,
            worker_target=None,
            event_sink=event_sink,
            mode_label="Python",
        )

        async with app.run_test() as _pilot:
            event_sink.publish(
                EVENT_SAMPLE,
                timestamp=1.0,
                setpoint=200.0,
                input=100.0,
                pwm=255.0,
                error=100.0,
                p=1.0,
                i=0.1,
                d=0.05,
            )
            event_sink.publish(
                EVENT_DECISION,
                round=1,
                action="BOOST_RESPONSE",
                analysis_summary="Queued before reset.",
                fallback_used=False,
                guardrail_notes=[],
            )

            app.action_reset_view()
            app._poll_events()

            event_sink.publish(
                EVENT_SAMPLE,
                timestamp=2.0,
                setpoint=210.0,
                input=120.0,
                pwm=200.0,
                error=90.0,
                p=1.1,
                i=0.2,
                d=0.06,
            )
            app._poll_events()

            self.assertEqual(len(app.state.event_history), 0)
            self.assertEqual(app.state.latest_action, "-")
            self.assertEqual(app.state.current_setpoint, 210.0)

    async def test_quit_waits_for_worker_to_finish(self):
        from sim.tui import SimulationTUIApp

        event_queue = Queue()
        event_sink = QueueEventSink(event_queue)
        controller = SimulationController()
        worker_finished = threading.Event()

        def worker() -> None:
            while not controller.should_stop:
                time.sleep(0.01)
            worker_finished.set()

        app = SimulationTUIApp(
            event_queue=event_queue,
            controller=controller,
            worker_target=worker,
            event_sink=event_sink,
            mode_label="Python",
        )

        async with app.run_test() as pilot:
            await pilot.press("q")
            for _ in range(10):
                if worker_finished.wait(timeout=0.05):
                    break
                await pilot.pause()

            self.assertTrue(controller.should_stop)
            self.assertTrue(worker_finished.is_set())
            self.assertIsNotNone(app._worker_thread)
            self.assertFalse(app._worker_thread.is_alive())

    async def test_completed_phase_disables_log_auto_scroll(self):
        from sim.tui import SimulationTUIApp

        event_queue = Queue()
        event_sink = QueueEventSink(event_queue)
        controller = SimulationController()
        app = SimulationTUIApp(
            event_queue=event_queue,
            controller=controller,
            worker_target=None,
            event_sink=event_sink,
            mode_label="Python",
        )

        async with app.run_test() as _pilot:
            event_sink.publish(
                EVENT_LIFECYCLE,
                phase="completed",
                message="Finished.",
                elapsed_sec=10.0,
            )
            app._poll_events()

            log = app.query_one("#events", RichLog)
            self.assertFalse(log.auto_scroll)

    async def test_next_round_restarts_worker_from_last_result(self):
        from sim.tui import SimulationTUIApp

        event_queue = Queue()
        event_sink = QueueEventSink(event_queue)
        controller = SimulationController()
        next_round_calls = []
        worker_started = threading.Event()

        def next_round_factory(last_result):
            next_round_calls.append(last_result)

            def worker() -> None:
                worker_started.set()

            return worker

        app = SimulationTUIApp(
            event_queue=event_queue,
            controller=controller,
            worker_target=None,
            event_sink=event_sink,
            mode_label="Python",
            next_round_factory=next_round_factory,
        )
        app._last_result = {"final_pid": {"p": 2.0, "i": 0.3, "d": 0.1}}

        async with app.run_test() as pilot:
            event_sink.publish(
                EVENT_LIFECYCLE,
                phase="completed",
                message="Finished.",
                elapsed_sec=10.0,
            )
            app._poll_events()
            self.assertTrue(app.state.tuning_done)

            await pilot.press("n")
            for _ in range(10):
                if worker_started.wait(timeout=0.05):
                    break
                await pilot.pause()

            help_text = str(app.query_one("#help", Static).content)
            self.assertTrue(worker_started.is_set())
            self.assertEqual(
                next_round_calls,
                [{"final_pid": {"p": 2.0, "i": 0.3, "d": 0.1}}],
            )
            self.assertFalse(app.state.tuning_done)
            self.assertFalse(app._history_browsing_enabled)
            self.assertIn("q", help_text)


if __name__ == "__main__":
    unittest.main()
