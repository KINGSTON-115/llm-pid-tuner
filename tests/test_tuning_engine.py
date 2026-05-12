import sys
import unittest
from pathlib import Path
from queue import Queue
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent))

from core.tuning_engine import run_tuning_engine
from sim.runtime import EVENT_LIFECYCLE, QueueEventSink, drain_event_queue


class FakeEnv:
    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.applied = []
        self.current_pid = {"p": 1.0, "i": 0.1, "d": 0.05}

    def collect_samples(self):
        if not self.rounds:
            return []
        return self.rounds.pop(0)

    def apply_pid(self, primary_pid, secondary_pid=None):
        self.applied.append((dict(primary_pid), secondary_pid))
        self.current_pid = dict(primary_pid)

    def get_current_pid(self):
        return dict(self.current_pid), None

    def get_setpoint(self):
        return 100.0

    def get_prompt_context(self):
        return {}

    def shutdown(self):
        pass

    def reset_buffer_state(self):
        pass


class CountingTuner:
    def __init__(self):
        self.calls = 0

    def analyze(self, *_args, **_kwargs):
        self.calls += 1
        return {
            "analysis_summary": "Adjust after a non-good-enough round.",
            "tuning_action": "ADJUST_PID",
            "p": 1.2,
            "i": 0.1,
            "d": 0.05,
            "status": "TUNING",
        }


def good_samples(pid=None):
    pid = pid or {"p": 1.0, "i": 0.1, "d": 0.05}
    return [
        {
            "timestamp": 0.0,
            "setpoint": 100.0,
            "input": 99.9,
            "pwm": 10.0,
            "error": 0.1,
            **pid,
        },
        {
            "timestamp": 1.0,
            "setpoint": 100.0,
            "input": 100.0,
            "pwm": 10.0,
            "error": 0.0,
            **pid,
        },
        {
            "timestamp": 2.0,
            "setpoint": 100.0,
            "input": 99.95,
            "pwm": 10.0,
            "error": 0.05,
            **pid,
        },
    ]


def slow_samples(pid=None):
    pid = pid or {"p": 1.0, "i": 0.1, "d": 0.05}
    return [
        {
            "timestamp": 0.0,
            "setpoint": 100.0,
            "input": 60.0,
            "pwm": 10.0,
            "error": 40.0,
            **pid,
        },
        {
            "timestamp": 1.0,
            "setpoint": 100.0,
            "input": 62.0,
            "pwm": 10.0,
            "error": 38.0,
            **pid,
        },
        {
            "timestamp": 2.0,
            "setpoint": 100.0,
            "input": 64.0,
            "pwm": 10.0,
            "error": 36.0,
            **pid,
        },
    ]


class TuningEngineObservationTests(unittest.TestCase):
    def _run(self, rounds, **config_overrides):
        tuner = CountingTuner()
        sink = QueueEventSink(Queue())
        config = {
            "BUFFER_SIZE": 3,
            "MAX_TUNING_ROUNDS": 3,
            "MIN_ERROR_THRESHOLD": 0.0,
            "REQUIRED_STABLE_ROUNDS": 2,
            "GOOD_ENOUGH_AVG_ERROR": 1.0,
            "GOOD_ENOUGH_STEADY_STATE_ERROR": 0.5,
            "GOOD_ENOUGH_OVERSHOOT": 2.0,
        }
        config.update(config_overrides)
        with patch.dict("core.tuning_engine.CONFIG", config, clear=False):
            result = run_tuning_engine(
                FakeEnv(rounds),
                tuner,
                "python_sim",
                event_sink=sink,
                emit_console=False,
            )
        return result, tuner, drain_event_queue(sink.event_queue)

    def test_good_enough_round_observes_without_llm_until_stable_count_reached(self):
        result, tuner, events = self._run([good_samples(), good_samples()])

        self.assertEqual(tuner.calls, 0)
        self.assertEqual(result["rounds_completed"], 2)
        self.assertEqual(result["completed_reason"], "stable_rounds_reached")
        self.assertTrue(
            any(
                event.get("type") == EVENT_LIFECYCLE
                and event.get("phase") == "observing"
                for event in events
            )
        )

    def test_observation_mode_resumes_llm_when_next_round_is_not_good_enough(self):
        result, tuner, _events = self._run(
            [good_samples(), slow_samples(), good_samples({"p": 1.2, "i": 0.1, "d": 0.05})]
        )

        self.assertEqual(tuner.calls, 1)
        self.assertEqual(result["rounds_completed"], 3)
        self.assertEqual(result["completed_reason"], "max_rounds_reached")


if __name__ == "__main__":
    unittest.main()
