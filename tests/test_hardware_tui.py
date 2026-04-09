import sys
import unittest
from pathlib import Path
from queue import Queue
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent))

import tuner
from sim.runtime import (
    EVENT_DECISION,
    EVENT_LIFECYCLE,
    EVENT_LOG,
    EVENT_ROUND_METRICS,
    EVENT_SAMPLE,
    QueueEventSink,
    SimulationController,
    drain_event_queue,
)


def _make_csv_line(timestamp: int, temp: float, pwm: float = 200.0) -> str:
    setpoint = 200.0
    error = setpoint - temp
    return f"{timestamp},{setpoint},{temp},{pwm},{error},1.0,0.1,0.05"


class HardwareTuiLoopTests(unittest.TestCase):
    def test_hardware_loop_applies_initial_pid_before_tuning(self):
        sent_commands: list[str] = []

        class FakeBridge:
            def __init__(self, _port, _baudrate, emit_console=True):
                self.emit_console = emit_console
                self.last_error = ""

            def connect(self):
                return True

            def disconnect(self):
                return None

            def read_line(self):
                return None

            def parse_data(self, line):
                return None

            def send_command(self, cmd):
                sent_commands.append(cmd)

        with patch.object(tuner, "SerialBridge", FakeBridge):
            with patch.dict(
                tuner.CONFIG,
                {"BUFFER_SIZE": 3, "MAX_TUNING_ROUNDS": 0},
                clear=False,
            ):
                tuner._run_hardware_tuning_loop(
                    "COM9",
                    emit_console=False,
                    initial_pid={"p": 2.5, "i": 0.4, "d": 0.1},
                )

        self.assertEqual(sent_commands[:2], ["STATUS", "SET P:2.5 I:0.4 D:0.1"])

    def test_hardware_loop_emits_stream_and_decision_events(self):
        event_queue = Queue()
        event_sink = QueueEventSink(event_queue)
        controller = SimulationController()
        sent_commands: list[str] = []
        captured = {}

        class FakeBridge:
            def __init__(self, _port, _baudrate, emit_console=True):
                self.emit_console = emit_console
                self.last_error = ""
                self._lines = iter(
                    [
                        _make_csv_line(0, 100.0),
                        _make_csv_line(1, 120.0),
                        _make_csv_line(2, 150.0),
                    ]
                )

            def connect(self):
                return True

            def disconnect(self):
                return None

            def read_line(self):
                return next(self._lines, None)

            def parse_data(self, line):
                parts = line.split(",")
                return {
                    "timestamp": float(parts[0]),
                    "setpoint": float(parts[1]),
                    "input": float(parts[2]),
                    "pwm": float(parts[3]),
                    "error": float(parts[4]),
                    "p": float(parts[5]),
                    "i": float(parts[6]),
                    "d": float(parts[7]),
                }

            def send_command(self, cmd):
                sent_commands.append(cmd)

        class FakeTuner:
            def __init__(
                self,
                *_args,
                stream_callback=None,
                log_callback=None,
                emit_console=True,
                **_kwargs,
            ):
                self.stream_callback = stream_callback
                self.log_callback = log_callback
                self.emit_console = emit_console

            def analyze(
                self,
                _prompt_data,
                _history_text,
                tuning_mode="generic",
                prompt_context=None,
            ):
                captured["tuning_mode"] = tuning_mode
                captured["prompt_context"] = prompt_context
                if self.log_callback:
                    self.log_callback("llm", "  LLM 正在思考...")
                if self.stream_callback:
                    self.stream_callback('{"thought_process":"he', False)
                    self.stream_callback('{"thought_process":"hello"}', True)
                return {
                    "analysis_summary": "Stop after one hardware round.",
                    "tuning_action": "HOLD",
                    "p": 1.2,
                    "i": 0.1,
                    "d": 0.05,
                    "status": "DONE",
                }

        with patch.object(tuner, "SerialBridge", FakeBridge):
            with patch.object(tuner, "LLMTuner", FakeTuner):
                with patch.dict(
                    tuner.CONFIG,
                    {"BUFFER_SIZE": 3, "MAX_TUNING_ROUNDS": 2},
                    clear=False,
                ):
                    result = tuner._run_hardware_tuning_loop(
                        "COM9",
                        event_sink=event_sink,
                        controller=controller,
                        emit_console=False,
                    )

        events = drain_event_queue(event_queue)
        event_types = {event["type"] for event in events}
        self.assertIn(EVENT_SAMPLE, event_types)
        self.assertIn(EVENT_ROUND_METRICS, event_types)
        self.assertIn(EVENT_DECISION, event_types)
        self.assertIn(EVENT_LOG, event_types)
        self.assertIn(EVENT_LIFECYCLE, event_types)
        self.assertGreaterEqual(result["rounds_completed"], 1)
        self.assertTrue(any(event.get("label") == "llm_stream" for event in events))
        self.assertTrue(any(cmd.startswith("SET P:") for cmd in sent_commands))
        self.assertEqual(captured["tuning_mode"], "hardware")
        self.assertEqual(captured["prompt_context"]["serial_port"], "COM9")

    def test_hardware_loop_sends_set2_when_llm_returns_dual_controller_result(self):
        sent_commands: list[str] = []
        captured = {}

        class FakeBridge:
            def __init__(self, _port, _baudrate, emit_console=True):
                self.emit_console = emit_console
                self.last_error = ""
                self._lines = iter(
                    [
                        "0,200,100,200,100,1.0,0.1,0.05,2.0,0.2,0.02",
                        "1,200,120,200,80,1.0,0.1,0.05,2.0,0.2,0.02",
                        "2,200,150,200,50,1.0,0.1,0.05,2.0,0.2,0.02",
                    ]
                )

            def connect(self):
                return True

            def disconnect(self):
                return None

            def read_line(self):
                return next(self._lines, None)

            def parse_data(self, line):
                parts = line.split(",")
                return {
                    "timestamp": float(parts[0]),
                    "setpoint": float(parts[1]),
                    "input": float(parts[2]),
                    "pwm": float(parts[3]),
                    "error": float(parts[4]),
                    "p": float(parts[5]),
                    "i": float(parts[6]),
                    "d": float(parts[7]),
                    "p2": float(parts[8]),
                    "i2": float(parts[9]),
                    "d2": float(parts[10]),
                }

            def send_command(self, cmd):
                sent_commands.append(cmd)

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
                captured["prompt_context"] = prompt_context
                return {
                    "analysis_summary": "Dual loop adjustment.",
                    "tuning_action": "ADJUST_PID",
                    "controller_1": {"p": 1.3, "i": 0.15, "d": 0.06},
                    "controller_2": {"p": 2.4, "i": 0.25, "d": 0.03},
                    "status": "DONE",
                }

        with patch.object(tuner, "SerialBridge", FakeBridge):
            with patch.object(tuner, "LLMTuner", FakeTuner):
                with patch.dict(
                    tuner.CONFIG,
                    {"BUFFER_SIZE": 3, "MAX_TUNING_ROUNDS": 2},
                    clear=False,
                ):
                    tuner._run_hardware_tuning_loop(
                        "COM9",
                        emit_console=False,
                    )

        self.assertIn("SET P:1.3 I:0.15 D:0.06", sent_commands)
        self.assertIn("SET2 P:2.4 I:0.25 D:0.03", sent_commands)
        self.assertEqual(captured["prompt_context"]["controller_count"], 2)

    def test_hardware_loop_guardrails_secondary_controller_before_set2(self):
        sent_commands: list[str] = []

        class FakeBridge:
            def __init__(self, _port, _baudrate, emit_console=True):
                self.emit_console = emit_console
                self.last_error = ""
                self._lines = iter(
                    [
                        "0,200,100,200,100,1.0,0.1,0.05,2.0,0.2,0.02",
                        "1,200,120,200,80,1.0,0.1,0.05,2.0,0.2,0.02",
                        "2,200,150,200,50,1.0,0.1,0.05,2.0,0.2,0.02",
                    ]
                )

            def connect(self):
                return True

            def disconnect(self):
                return None

            def read_line(self):
                return next(self._lines, None)

            def parse_data(self, line):
                parts = line.split(",")
                return {
                    "timestamp": float(parts[0]),
                    "setpoint": float(parts[1]),
                    "input": float(parts[2]),
                    "pwm": float(parts[3]),
                    "error": float(parts[4]),
                    "p": float(parts[5]),
                    "i": float(parts[6]),
                    "d": float(parts[7]),
                    "p2": float(parts[8]),
                    "i2": float(parts[9]),
                    "d2": float(parts[10]),
                }

            def send_command(self, cmd):
                sent_commands.append(cmd)

        class FakeTuner:
            def __init__(self, *_args, **_kwargs):
                pass

            def analyze(self, *_args, **_kwargs):
                return {
                    "analysis_summary": "Dual loop adjustment.",
                    "tuning_action": "ADJUST_PID",
                    "controller_1": {"p": 1.3, "i": 0.15, "d": 0.06},
                    "controller_2": {"p": 99.0, "i": 5.0, "d": 3.0},
                    "status": "DONE",
                }

        with patch.object(tuner, "SerialBridge", FakeBridge):
            with patch.object(tuner, "LLMTuner", FakeTuner):
                with patch.dict(
                    tuner.CONFIG,
                    {"BUFFER_SIZE": 3, "MAX_TUNING_ROUNDS": 2},
                    clear=False,
                ):
                    tuner._run_hardware_tuning_loop(
                        "COM9",
                        emit_console=False,
                    )

        self.assertIn("SET2 P:6.0 I:0.8 D:0.08", sent_commands)

    def test_hardware_connection_failure_reports_error_result(self):
        event_queue = Queue()
        event_sink = QueueEventSink(event_queue)

        class FailingBridge:
            def __init__(self, _port, _baudrate, emit_console=True):
                self.emit_console = emit_console
                self.last_error = "port busy"

            def connect(self):
                return False

            def disconnect(self):
                return None

        with patch.object(tuner, "SerialBridge", FailingBridge):
            with patch.dict(tuner.CONFIG, {"BUFFER_SIZE": 3}, clear=False):
                result = tuner._run_hardware_tuning_loop(
                    "COM9",
                    event_sink=event_sink,
                    controller=None,
                    emit_console=False,
                )

        events = drain_event_queue(event_queue)
        self.assertEqual(result["completed_reason"], "error")
        self.assertTrue(any(event.get("phase") == "error" for event in events))

    def test_run_hardware_tuner_tui_failure_falls_back_to_plain_runner(self):
        with patch.object(tuner, "initialize_runtime_config"):
            with patch.object(tuner, "resolve_serial_port", return_value="COM9"):
                with patch.dict(tuner.CONFIG, {"LLM_DEBUG_OUTPUT": False}, clear=False):
                    with patch.object(
                        tuner,
                        "_run_hardware_tuning_with_tui",
                        side_effect=RuntimeError("tui boom"),
                    ):
                        with patch.object(
                            tuner,
                            "_run_hardware_tuning_plain",
                            return_value={"mode": "plain"},
                        ) as plain:
                            result = tuner.run_hardware_tuner(force_plain=False)

        self.assertEqual(result, {"mode": "plain"})
        plain.assert_called_once_with("COM9", initial_pid=None)


if __name__ == "__main__":
    unittest.main()
