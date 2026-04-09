import sys
import unittest
from pathlib import Path
from queue import Queue


sys.path.insert(0, str(Path(__file__).parent.parent))

from core.tuning_loop import (
    flatten_controller_result,
    publish_decision,
    publish_rollback,
    publish_round_metrics,
)
from core.tuning_session import DecisionOutcome, RoundEvaluation
from sim.runtime import (
    EVENT_DECISION,
    EVENT_ROLLBACK,
    EVENT_ROUND_METRICS,
    QueueEventSink,
)


def _make_sink() -> tuple[QueueEventSink, Queue]:
    queue: Queue = Queue()
    return QueueEventSink(event_queue=queue), queue


def _drain(queue: Queue) -> list[dict]:
    events: list[dict] = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


def _make_evaluation(**overrides) -> RoundEvaluation:
    defaults = dict(
        round_index=3,
        metrics={
            "avg_error": 1.5,
            "max_error": 3.0,
            "steady_state_error": 0.2,
            "overshoot": 0.05,
            "zero_crossings": 2,
            "status": "STABLE",
        },
        current_pid={"p": 1.0, "i": 0.1, "d": 0.01},
        stable_rounds=2,
    )
    defaults.update(overrides)
    return RoundEvaluation(**defaults)


def _make_decision(**overrides) -> DecisionOutcome:
    defaults = dict(
        safe_pid={"p": 2.0, "i": 0.2, "d": 0.02},
        action="ADJUST_PID",
        analysis="moving up",
        thought="reasoning",
        guardrail_notes=["clamped P"],
        fallback_used=False,
        status="TUNING",
    )
    defaults.update(overrides)
    return DecisionOutcome(**defaults)


class PublishRoundMetricsTests(unittest.TestCase):
    def test_publishes_all_fields(self):
        sink, queue = _make_sink()
        publish_round_metrics(sink, _make_evaluation(), round_index=3)
        events = _drain(queue)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["type"], EVENT_ROUND_METRICS)
        self.assertEqual(event["round"], 3)
        self.assertEqual(event["avg_error"], 1.5)
        self.assertEqual(event["max_error"], 3.0)
        self.assertEqual(event["steady_state_error"], 0.2)
        self.assertEqual(event["overshoot"], 0.05)
        self.assertEqual(event["zero_crossings"], 2)
        self.assertEqual(event["status"], "STABLE")
        self.assertEqual(event["stable_rounds"], 2)

    def test_none_sink_is_noop(self):
        publish_round_metrics(None, _make_evaluation(), round_index=1)

    def test_coerces_numeric_types(self):
        sink, queue = _make_sink()
        eval_ = _make_evaluation(
            metrics={
                "avg_error": "1.5",
                "max_error": "3",
                "steady_state_error": "0.1",
                "overshoot": "0.02",
                "zero_crossings": 4.0,
                "status": "TUNING",
            }
        )
        publish_round_metrics(sink, eval_, round_index=1)
        event = _drain(queue)[0]
        self.assertIsInstance(event["avg_error"], float)
        self.assertIsInstance(event["zero_crossings"], int)


class PublishDecisionTests(unittest.TestCase):
    def test_publishes_all_fields(self):
        sink, queue = _make_sink()
        publish_decision(sink, 5, _make_decision())
        event = _drain(queue)[0]
        self.assertEqual(event["type"], EVENT_DECISION)
        self.assertEqual(event["round"], 5)
        self.assertEqual(event["action"], "ADJUST_PID")
        self.assertEqual(event["analysis_summary"], "moving up")
        self.assertFalse(event["fallback_used"])
        self.assertEqual(event["guardrail_notes"], ["clamped P"])

    def test_guardrail_notes_copied(self):
        sink, queue = _make_sink()
        decision = _make_decision()
        publish_decision(sink, 1, decision)
        event = _drain(queue)[0]
        decision.guardrail_notes.append("new note")
        self.assertEqual(event["guardrail_notes"], ["clamped P"])

    def test_none_sink_is_noop(self):
        publish_decision(None, 1, _make_decision())


class PublishRollbackTests(unittest.TestCase):
    def test_target_round_uses_best_result(self):
        sink, queue = _make_sink()
        eval_ = _make_evaluation(
            best_result={"round": 2, "pid": {"p": 1}, "metrics": {}},
        )
        rollback_pid = {"p": 1.0, "i": 0.1, "d": 0.01}
        publish_rollback(sink, 5, eval_, rollback_pid, reason="regressed")
        event = _drain(queue)[0]
        self.assertEqual(event["type"], EVENT_ROLLBACK)
        self.assertEqual(event["round"], 5)
        self.assertEqual(event["target_round"], 2)
        self.assertEqual(event["pid"], rollback_pid)
        self.assertEqual(event["reason"], "regressed")

    def test_target_round_falls_back_to_round_index(self):
        sink, queue = _make_sink()
        publish_rollback(
            sink,
            7,
            _make_evaluation(best_result=None),
            {"p": 1.0, "i": 0.1, "d": 0.01},
            reason="no best",
        )
        event = _drain(queue)[0]
        self.assertEqual(event["target_round"], 7)

    def test_pid_copied_not_aliased(self):
        sink, queue = _make_sink()
        rollback_pid = {"p": 1.0, "i": 0.1, "d": 0.01}
        publish_rollback(sink, 1, _make_evaluation(), rollback_pid, "r")
        event = _drain(queue)[0]
        rollback_pid["p"] = 999.0
        self.assertEqual(event["pid"]["p"], 1.0)

    def test_none_sink_is_noop(self):
        publish_rollback(None, 1, _make_evaluation(), {"p": 1, "i": 1, "d": 1}, "r")


class FlattenControllerResultTests(unittest.TestCase):
    def test_no_controller_1_returns_input_unchanged(self):
        result = {"p": 1.0, "analysis_summary": "a"}
        out, primary, secondary = flatten_controller_result(result, {"p": 0, "i": 0, "d": 0})
        self.assertIs(out, result)
        self.assertIsNone(primary)
        self.assertIsNone(secondary)

    def test_controller_1_dict_promoted(self):
        result = {
            "controller_1": {"p": 2.0, "i": 0.5, "d": 0.05},
            "analysis_summary": "moving up",
        }
        out, primary, secondary = flatten_controller_result(result, {"p": 1, "i": 0, "d": 0})
        self.assertEqual(out["p"], 2.0)
        self.assertEqual(out["i"], 0.5)
        self.assertEqual(out["analysis_summary"], "moving up")
        self.assertEqual(out["tuning_action"], "ADJUST_PID")
        self.assertEqual(out["status"], "TUNING")
        self.assertIsNotNone(primary)

    def test_missing_pid_fields_use_current_pid(self):
        # controller_1 is non-empty but missing i/d -> fall back to current_pid
        result = {"controller_1": {"p": 9.0}}
        out, _, _ = flatten_controller_result(result, {"p": 1.5, "i": 0.3, "d": 0.01})
        self.assertEqual(out["p"], 9.0)
        self.assertEqual(out["i"], 0.3)
        self.assertEqual(out["d"], 0.01)

    def test_controller_2_returned_as_secondary(self):
        result = {
            "controller_1": {"p": 1, "i": 1, "d": 1},
            "controller_2": {"p": 2, "i": 2, "d": 2},
        }
        _, _, secondary = flatten_controller_result(result, {"p": 0, "i": 0, "d": 0})
        self.assertEqual(secondary, {"p": 2, "i": 2, "d": 2})

    def test_non_dict_controllers_ignored(self):
        result = {"controller_1": "oops", "controller_2": None}
        _, primary, secondary = flatten_controller_result(result, {"p": 0, "i": 0, "d": 0})
        self.assertIsNone(primary)
        self.assertIsNone(secondary)


if __name__ == "__main__":
    unittest.main()
