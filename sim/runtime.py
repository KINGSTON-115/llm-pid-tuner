from __future__ import annotations

from dataclasses import dataclass, field
from queue import Empty, Queue
import threading
import time
from typing import Any


EVENT_SAMPLE        = "sample"
EVENT_ROUND_METRICS = "round_metrics"
EVENT_DECISION      = "decision"
EVENT_ROLLBACK      = "rollback"
EVENT_LIFECYCLE     = "lifecycle"
EVENT_LLM_STREAM    = "llm_stream"


RuntimeEvent = dict[str, Any]


def build_event(event_type: str, **payload: Any) -> RuntimeEvent:
    return {"type": event_type, **payload}


def drain_event_queue(event_queue: Queue[RuntimeEvent]) -> list[RuntimeEvent]:
    events: list[RuntimeEvent] = []
    while True:
        try:
            events.append(event_queue.get_nowait())
        except Empty:
            return events


@dataclass(slots=True)
class QueueEventSink:
    event_queue: Queue[RuntimeEvent]
    _sequence  : int = 0
    _lock      : threading.Lock = field(default_factory=threading.Lock)

    def publish(self, event_type: str, **payload: Any) -> None:
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
            self.event_queue.put(build_event(event_type, seq=sequence, **payload))

    def snapshot_sequence(self) -> int:
        with self._lock:
            return self._sequence


@dataclass(slots=True)
class SimulationController:
    stop_event: threading.Event = field(default_factory=threading.Event)
    run_event : threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        self.run_event.set()

    @property
    def is_paused(self) -> bool:
        return not self.run_event.is_set()

    @property
    def should_stop(self) -> bool:
        return self.stop_event.is_set()

    def pause(self) -> None:
        self.run_event.clear()

    def resume(self) -> None:
        self.run_event.set()

    def toggle_pause(self) -> bool:
        if self.is_paused:
            self.resume()
        else:
            self.pause()
        return self.is_paused

    def request_stop(self) -> None:
        self.stop_event.set()
        self.run_event.set()

    def wait_until_running(self, poll_interval: float = 0.05) -> bool:
        while not self.stop_event.is_set():
            if self.run_event.wait(timeout=poll_interval):
                return True
        return False


def publish_event(
    event_sink: QueueEventSink | None, event_type: str, **payload: Any
) -> None:
    if event_sink is not None:
        event_sink.publish(event_type, **payload)


def wait_while_paused(
    controller: SimulationController | None, poll_interval: float = 0.05
) -> bool:
    if controller is None:
        return True
    return controller.wait_until_running(poll_interval=poll_interval)


def now_elapsed(start_time: float) -> float:
    return round(time.time() - start_time, 3)
