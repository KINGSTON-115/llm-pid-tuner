import importlib.util
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

        class FakeTuner:
            def __init__(self, *_args, **_kwargs):
                pass

            def analyze(self, _prompt_data, _history_text):
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


class TuiModeTests(unittest.TestCase):
    def test_non_tty_terminal_falls_back_to_plain_mode(self):
        with patch.object(sys.stdin, "isatty", return_value=False):
            with patch.object(sys.stdout, "isatty", return_value=False):
                use_tui, message = simulator.determine_tui_mode(False, "")

        self.assertFalse(use_tui)
        self.assertIn("interactive terminal", message)

    def test_simulink_mode_falls_back_to_plain_mode(self):
        use_tui, message = simulator.determine_tui_mode(False, "model.slx")
        self.assertFalse(use_tui)
        self.assertIn("Simulink mode", message)

    def test_missing_textual_falls_back_to_plain_mode(self):
        with patch.object(sys.stdin, "isatty", return_value=True):
            with patch.object(sys.stdout, "isatty", return_value=True):
                with patch(
                    "simulator.importlib.import_module",
                    side_effect=ModuleNotFoundError("No module named 'textual'"),
                ):
                    use_tui, message = simulator.determine_tui_mode(False, "")

        self.assertFalse(use_tui)
        self.assertIn("dependencies are missing", message)

    def test_tui_mode_uses_import_probe(self):
        with patch.object(sys.stdin, "isatty", return_value=True):
            with patch.object(sys.stdout, "isatty", return_value=True):
                with patch("simulator.importlib.import_module") as import_module:
                    use_tui, message = simulator.determine_tui_mode(False, "")

        self.assertTrue(use_tui)
        self.assertIsNone(message)
        import_module.assert_called_once_with("sim.tui")

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

    def test_default_mode_uses_doctor_and_warm_start(self):
        doctor_checks = [DoctorCheck("api", "PASS", "ok")]
        with patch.object(simulator, "ensure_runtime_config"):
            with patch.object(simulator, "collect_doctor_checks", return_value=doctor_checks):
                with patch.dict(simulator.CONFIG, {"MATLAB_MODEL_PATH": ""}, clear=False):
                    with patch.object(simulator, "determine_tui_mode", return_value=(True, None)):
                        with patch.object(simulator, "_run_python_simulation_with_tui", return_value={"mode": "tui"}) as tui:
                            result = simulator.run_simulation(force_plain=False)

        self.assertEqual(result, {"mode": "tui"})
        tui.assert_called_once_with(warm_start=True, doctor_checks=doctor_checks)

    def test_tui_failure_falls_back_to_plain_runner(self):
        doctor_checks = [DoctorCheck("api", "PASS", "ok")]
        with patch.object(simulator, "ensure_runtime_config"):
            with patch.object(simulator, "collect_doctor_checks", return_value=doctor_checks):
                with patch.object(simulator, "print_doctor_report") as doctor_report:
                    with patch.dict(
                        simulator.CONFIG,
                        {"MATLAB_MODEL_PATH": "", "LLM_DEBUG_OUTPUT": False},
                        clear=False,
                    ):
                        with patch.object(
                            simulator, "determine_tui_mode", return_value=(True, None)
                        ):
                            with patch.object(
                                simulator,
                                "_run_python_simulation_with_tui",
                                side_effect=RuntimeError("tui boom"),
                            ):
                                with patch.object(
                                    simulator,
                                    "_run_python_simulation_plain",
                                    return_value={"mode": "plain"},
                                ) as plain:
                                    result = simulator.run_simulation(force_plain=False)

        self.assertEqual(result, {"mode": "plain"})
        doctor_report.assert_called_once_with(doctor_checks)
        plain.assert_called_once_with(warm_start=True, doctor_checks=doctor_checks)


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


if __name__ == "__main__":
    unittest.main()
