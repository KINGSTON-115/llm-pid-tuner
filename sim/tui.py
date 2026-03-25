from __future__ import annotations

from collections import deque
from dataclasses import field
import threading
import time
from queue import Queue
from typing import Callable

from core.compat import slotted_dataclass
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.widgets import RichLog, Static

from sim.runtime import (
    EVENT_DECISION,
    EVENT_LIFECYCLE,
    EVENT_LOG,
    EVENT_ROLLBACK,
    EVENT_ROUND_METRICS,
    EVENT_SAMPLE,
    QueueEventSink,
    RuntimeEvent,
    SimulationController,
    drain_event_queue,
)


TRANSLATIONS = {
    "zh": {
        "waiting_status": "等待仿真数据...",
        "waiting_summary": "摘要等待中...",
        "waiting_help": "快捷键说明加载中...",
        "status_line_1": (
            "模式 {mode} | 轮次 {round} | 运行 {elapsed:.1f}s | "
            "状态 {status} | 阶段 {phase} | 暂停 {paused}"
        ),
        "status_line_2": (
            "目标 {setpoint:.1f} | 当前 {input:.1f} | "
            "误差 {error:.2f} | PWM {pwm:.1f}"
        ),
        "status_line_3": "PID  P={p:.4f}  I={i:.4f}  D={d:.4f}",
        "paused_yes": "是",
        "paused_no": "否",
        "summary_title": "当前指标",
        "summary_avg_error": "平均误差      {value:.3f}",
        "summary_max_error": "最大误差      {value:.3f}",
        "summary_steady_error": "稳态误差      {value:.3f}",
        "summary_overshoot": "超调          {value:.3f}%",
        "summary_zero_cross": "过零次数      {value}",
        "summary_stable_rounds": "稳定轮数      {value}",
        "decision_title": "最近一次决策",
        "decision_action": "动作          {value}",
        "decision_flags": "标记          {value}",
        "message_title": "消息",
        "help_title": "快捷键",
        "help_line": "q 退出 | p 暂停/继续 | l 详细日志 | r 清空日志/摘要",
        "help_browse": "日志浏览：鼠标滚轮 / PgUp / PgDn / ↑↓",
        "help_done": "调参完成 → n 继续新一轮（以本次结果为起点） | q 退出",
        "flags_none": "无",
        "no_decision": "还没有决策。",
        "summary_cleared": "摘要已清空。",
        "no_events": "还没有事件。",
        "event_fallback": "兜底",
        "event_guardrail": "护栏",
        "event_rollback": "回滚",
        "event_elapsed": "耗时",
        "stopping": "正在等待后台仿真线程安全退出。",
        "paused_msg": "已暂停仿真。",
        "resumed_msg": "已恢复仿真。",
    },
    "en": {
        "waiting_status": "Waiting for simulation data...",
        "waiting_summary": "Summary pending...",
        "waiting_help": "Loading hotkeys...",
        "status_line_1": (
            "Mode {mode} | Round {round} | Elapsed {elapsed:.1f}s | "
            "Status {status} | Phase {phase} | Paused {paused}"
        ),
        "status_line_2": (
            "Setpoint {setpoint:.1f} | Input {input:.1f} | "
            "Error {error:.2f} | PWM {pwm:.1f}"
        ),
        "status_line_3": "PID  P={p:.4f}  I={i:.4f}  D={d:.4f}",
        "paused_yes": "yes",
        "paused_no": "no",
        "summary_title": "Round Metrics",
        "summary_avg_error": "avg_error      {value:.3f}",
        "summary_max_error": "max_error      {value:.3f}",
        "summary_steady_error": "steady_error   {value:.3f}",
        "summary_overshoot": "overshoot      {value:.3f}%",
        "summary_zero_cross": "zero_cross     {value}",
        "summary_stable_rounds": "stable_rounds  {value}",
        "decision_title": "Latest Decision",
        "decision_action": "action         {value}",
        "decision_flags": "flags          {value}",
        "message_title": "Message",
        "help_title": "Hotkeys",
        "help_line": "q quit | p pause/resume | l detailed log | r clear log/summary",
        "help_browse": "Log browsing: mouse wheel / PgUp / PgDn / Up / Down",
        "help_done": "Tuning done → n next round (starts from last result) | q quit",
        "flags_none": "none",
        "no_decision": "No decision yet.",
        "summary_cleared": "Summary cleared.",
        "no_events": "No events yet.",
        "event_fallback": "fallback",
        "event_guardrail": "guardrail",
        "event_rollback": "rollback",
        "event_elapsed": "elapsed",
        "stopping": "Waiting for the background simulation worker to stop.",
        "paused_msg": "Simulation paused by user.",
        "resumed_msg": "Simulation resumed by user.",
    },
}


@slotted_dataclass
class PanelState:
    max_events: int = 100
    detailed_events: bool = False
    mode_label: str = "Python"
    language: str = "zh"
    current_round: int = 0
    elapsed_sec: float = 0.0
    current_status: str = "IDLE"
    current_phase: str = "idle"
    phase_message: str = "Waiting to start"
    stable_rounds: int = 0
    paused: bool = False
    tuning_done: bool = False
    current_input: float = 0.0
    current_setpoint: float = 0.0
    current_pwm: float = 0.0
    current_error: float = 0.0
    current_pid: dict[str, float] = field(
        default_factory=lambda: {"p": 1.0, "i": 0.1, "d": 0.05}
    )
    metrics: dict[str, float | int] = field(
        default_factory=lambda: {
            "avg_error": 0.0,
            "max_error": 0.0,
            "steady_state_error": 0.0,
            "overshoot": 0.0,
            "zero_crossings": 0,
        }
    )
    latest_action: str = "-"
    latest_analysis: str = ""
    latest_flags: list[str] = field(default_factory=list)
    event_history: deque[RuntimeEvent] = field(init=False)

    def __post_init__(self) -> None:
        self.event_history = deque(maxlen=self.max_events)
        self.latest_analysis = self.tr("no_decision")

    def tr(self, key: str) -> str:
        language = self.language if self.language in TRANSLATIONS else "en"
        return TRANSLATIONS[language][key]

    def apply_event(self, event: RuntimeEvent) -> None:
        event_type = event.get("type")

        if event_type == EVENT_SAMPLE:
            self.current_input = float(event.get("input", 0.0))
            self.current_setpoint = float(event.get("setpoint", 0.0))
            self.current_pwm = float(event.get("pwm", 0.0))
            self.current_error = float(event.get("error", 0.0))
            self.current_pid = {
                "p": float(event.get("p", self.current_pid["p"])),
                "i": float(event.get("i", self.current_pid["i"])),
                "d": float(event.get("d", self.current_pid["d"])),
            }
            return

        if event_type == EVENT_ROUND_METRICS:
            self.current_round = int(event.get("round", self.current_round))
            self.current_status = str(event.get("status", self.current_status))
            self.stable_rounds = int(event.get("stable_rounds", self.stable_rounds))
            self.metrics = {
                "avg_error": float(event.get("avg_error", 0.0)),
                "max_error": float(event.get("max_error", 0.0)),
                "steady_state_error": float(event.get("steady_state_error", 0.0)),
                "overshoot": float(event.get("overshoot", 0.0)),
                "zero_crossings": int(event.get("zero_crossings", 0)),
            }
            return

        if event_type == EVENT_DECISION:
            self.latest_action = str(event.get("action", "UNKNOWN"))
            self.latest_analysis = str(
                event.get("analysis_summary", self.tr("no_decision"))
            )
            flags: list[str] = []
            if event.get("fallback_used"):
                flags.append(self.tr("event_fallback"))
            if event.get("guardrail_notes"):
                flags.append(self.tr("event_guardrail"))
            self.latest_flags = flags

        elif event_type == EVENT_ROLLBACK:
            pid = event.get("pid", {})
            self.current_pid = {
                "p": float(pid.get("p", self.current_pid["p"])),
                "i": float(pid.get("i", self.current_pid["i"])),
                "d": float(pid.get("d", self.current_pid["d"])),
            }
            self.latest_flags = [self.tr("event_rollback")]
            self.latest_analysis = str(event.get("reason", "Rollback applied."))

        elif event_type == EVENT_LIFECYCLE:
            self.current_phase = str(event.get("phase", self.current_phase))
            self.phase_message = str(event.get("message", self.phase_message))
            self.elapsed_sec = float(event.get("elapsed_sec", self.elapsed_sec))

        elif event_type == EVENT_LOG:
            if (
                event.get("replace_last")
                and self.event_history
                and self.event_history[-1].get("type") == EVENT_LOG
                and self.event_history[-1].get("label") == event.get("label")
                and self.event_history[-1].get("stream_id") == event.get("stream_id")
            ):
                self.event_history[-1] = dict(event)
            else:
                self.event_history.append(dict(event))
            return

        if event_type in {EVENT_DECISION, EVENT_ROLLBACK, EVENT_LIFECYCLE}:
            self.event_history.append(dict(event))

    def reset_view(self) -> None:
        self.event_history.clear()
        self.latest_action = "-"
        self.latest_analysis = self.tr("summary_cleared")
        self.latest_flags.clear()

    def render_status_text(self) -> str:
        paused_text = self.tr("paused_yes") if self.paused else self.tr("paused_no")
        return "\n".join(
            [
                self.tr("status_line_1").format(
                    mode=self.mode_label,
                    round=self.current_round,
                    elapsed=self.elapsed_sec,
                    status=self.current_status,
                    phase=self.current_phase,
                    paused=paused_text,
                ),
                self.tr("status_line_2").format(
                    setpoint=self.current_setpoint,
                    input=self.current_input,
                    error=self.current_error,
                    pwm=self.current_pwm,
                ),
                self.tr("status_line_3").format(
                    p=self.current_pid["p"],
                    i=self.current_pid["i"],
                    d=self.current_pid["d"],
                ),
            ]
        )

    def render_summary_text(self) -> str:
        flags_text = ", ".join(self.latest_flags) if self.latest_flags else self.tr("flags_none")
        return "\n".join(
            [
                self.tr("summary_title"),
                self.tr("summary_avg_error").format(value=float(self.metrics["avg_error"])),
                self.tr("summary_max_error").format(value=float(self.metrics["max_error"])),
                self.tr("summary_steady_error").format(
                    value=float(self.metrics["steady_state_error"])
                ),
                self.tr("summary_overshoot").format(value=float(self.metrics["overshoot"])),
                self.tr("summary_zero_cross").format(
                    value=int(self.metrics["zero_crossings"])
                ),
                self.tr("summary_stable_rounds").format(value=self.stable_rounds),
                "",
                self.tr("decision_title"),
                self.tr("decision_action").format(value=self.latest_action),
                self.tr("decision_flags").format(value=flags_text),
                self.latest_analysis,
                "",
                self.tr("message_title"),
                self.phase_message,
            ]
        )

    def render_help_text(self) -> str:
        if self.tuning_done:
            return "\n".join(
                [
                    self.tr("help_title"),
                    self.tr("help_done"),
                    self.tr("help_browse"),
                ]
            )
        return "\n".join(
            [
                self.tr("help_title"),
                self.tr("help_line"),
                self.tr("help_browse"),
            ]
        )

    def render_event_lines(self) -> list[str]:
        return [
            self._format_event(event, detailed=self.detailed_events)
            for event in self.event_history
        ]

    def _format_event(self, event: RuntimeEvent, detailed: bool) -> str:
        event_type = event.get("type")
        if event_type == EVENT_DECISION:
            line = (
                f"R{event.get('round', '?')} {event.get('action', 'UNKNOWN')}: "
                f"{event.get('analysis_summary', '')}"
            )
            if event.get("fallback_used"):
                line += f" [{self.tr('event_fallback')}]"
            if detailed and event.get("guardrail_notes"):
                notes = "; ".join(str(note) for note in event.get("guardrail_notes", []))
                line += f" | {self.tr('event_guardrail')}: {notes}"
            return line

        if event_type == EVENT_ROLLBACK:
            line = f"R{event.get('round', '?')} {self.tr('event_rollback')}"
            if detailed:
                line += f" | {event.get('reason', '')}"
            return line

        if event_type == EVENT_LIFECYCLE:
            line = f"[{event.get('phase', 'info')}] {event.get('message', '')}"
            if detailed:
                line += f" | {self.tr('event_elapsed')}: {float(event.get('elapsed_sec', 0.0)):.1f}s"
            return line

        if event_type == EVENT_LOG:
            label = str(event.get("label", "log"))
            message = str(event.get("message", ""))
            if not detailed:
                message = message.replace("\r", "").replace("\n", " ")
            return f"[{label}] {message}".rstrip()

        return str(event)


class SimulationTUIApp(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }

    #status {
        height: 5;
        border: round $accent;
        padding: 0 1;
    }

    #help {
        height: 4;
        border: round $accent;
        padding: 0 1;
    }

    #summary {
        height: 14;
        border: round $accent;
        padding: 1;
    }

    #events {
        height: 1fr;
        border: round $accent;
    }
    """

    BINDINGS = [
        ("q", "request_quit", "Quit"),
        ("p", "toggle_pause", "Pause"),
        ("l", "toggle_event_detail", "Log detail"),
        ("r", "reset_view", "Reset view"),
        ("n", "next_round", "Next round"),
    ]

    def __init__(
        self,
        event_queue: Queue[RuntimeEvent],
        controller: SimulationController,
        worker_target: Callable[[], None] | None,
        event_sink: QueueEventSink | None = None,
        mode_label: str = "Python",
        language: str = "zh",
        next_round_factory: Callable[[], Callable[[], None]] | None = None,
    ) -> None:
        super().__init__()
        self.event_queue = event_queue
        self.controller = controller
        self.worker_target = worker_target
        self.event_sink = event_sink
        self.state = PanelState(mode_label=mode_label, language=language)
        self.next_round_factory = next_round_factory
        self._worker_thread: threading.Thread | None = None
        self._started_at = time.time()
        self._shutdown_requested = False
        self._ignore_events_before_seq: int | None = None
        self._rendered_event_count = 0
        self._log_requires_full_refresh = True
        self._placeholder_visible = False
        self._history_browsing_enabled = False
        self._last_result: dict[str, Any] = {}

    def compose(self) -> ComposeResult:
        yield Static(self.state.tr("waiting_status"), id="status")
        yield Static(self.state.tr("waiting_help"), id="help")
        yield Static(self.state.tr("waiting_summary"), id="summary")
        yield RichLog(
            id="events",
            wrap=True,
            highlight=False,
            markup=False,
            auto_scroll=True,
        )

    def on_mount(self) -> None:
        if self.worker_target is not None:
            self._worker_thread = threading.Thread(
                target=self.worker_target,
                name="simulation-tui-worker",
            )
            self._worker_thread.start()
        self.set_interval(0.1, self._poll_events)
        self._refresh_all()
        self.call_after_refresh(self._focus_log)

    def on_unmount(self) -> None:
        self.controller.request_stop()

    def _focus_log(self) -> None:
        try:
            self.query_one("#events", RichLog).focus()
        except NoMatches:
            return

    def _poll_events(self) -> None:
        events = drain_event_queue(self.event_queue)
        terminal_event_seen = False

        if events:
            for event in events:
                event_seq = event.get("seq")
                if (
                    self._ignore_events_before_seq is not None
                    and isinstance(event_seq, int)
                    and event_seq <= self._ignore_events_before_seq
                ):
                    continue
                self.state.apply_event(event)
                if (
                    event.get("type") == EVENT_LIFECYCLE
                    and str(event.get("phase", "")).lower()
                    in {"completed", "finished", "stopped", "error"}
                ):
                    terminal_event_seen = True
                if event.get("type") == EVENT_LOG and event.get("replace_last"):
                    self._log_requires_full_refresh = True

        self.state.paused = self.controller.is_paused
        if self._worker_thread is not None and self._worker_thread.is_alive():
            self.state.elapsed_sec = max(
                self.state.elapsed_sec, round(time.time() - self._started_at, 3)
            )
        self._refresh_all()

        if terminal_event_seen:
            self._enable_history_browsing()

        if (
            self._shutdown_requested
            and not self._worker_is_running()
            and self.event_queue.empty()
        ):
            self.exit()

    def _refresh_all(self) -> None:
        try:
            self.query_one("#status", Static).update(self.state.render_status_text())
            self.query_one("#help", Static).update(self.state.render_help_text())
            self.query_one("#summary", Static).update(self.state.render_summary_text())
            self._refresh_events()
        except NoMatches:
            return

    def _refresh_events(self) -> None:
        log = self.query_one("#events", RichLog)
        lines = self.state.render_event_lines()

        if self._log_requires_full_refresh:
            log.clear()
            self._rendered_event_count = 0
            self._placeholder_visible = False
            self._log_requires_full_refresh = False

        if not lines:
            if not self._placeholder_visible:
                log.clear()
                log.write(self.state.tr("no_events"))
                self._placeholder_visible = True
                self._rendered_event_count = 0
            return

        if self._rendered_event_count > len(lines):
            log.clear()
            self._rendered_event_count = 0
            self._placeholder_visible = False

        if self._placeholder_visible:
            log.clear()
            self._placeholder_visible = False

        for line in lines[self._rendered_event_count :]:
            log.write(line)
        self._rendered_event_count = len(lines)

    def _enable_history_browsing(self) -> None:
        if self._history_browsing_enabled:
            return
        try:
            log = self.query_one("#events", RichLog)
        except NoMatches:
            return
        log.auto_scroll = False
        log.focus()
        self._history_browsing_enabled = True
        if self.next_round_factory is not None:
            self.state.tuning_done = True
            self._refresh_all()

    def action_request_quit(self) -> None:
        self.controller.request_stop()
        if not self._worker_is_running():
            self.exit()
            return
        self._shutdown_requested = True
        self.state.apply_event(
            {
                "type": EVENT_LIFECYCLE,
                "phase": "stopping",
                "message": self.state.tr("stopping"),
                "elapsed_sec": self.state.elapsed_sec,
            }
        )
        self._log_requires_full_refresh = True
        self._refresh_all()

    def action_next_round(self) -> None:
        if not self.state.tuning_done or self.next_round_factory is None:
            return
        new_worker = self.next_round_factory(self._last_result)
        self.state.tuning_done = False
        self._history_browsing_enabled = False
        self.state.reset_view()
        self._rendered_event_count = 0
        self._log_requires_full_refresh = True
        self.controller = SimulationController()
        self._shutdown_requested = False
        self._started_at = time.time()
        self._worker_thread = threading.Thread(
            target=new_worker,
            name="simulation-tui-worker",
        )
        self._worker_thread.start()
        try:
            log = self.query_one("#events", RichLog)
            log.auto_scroll = True
            log.clear()
        except NoMatches:
            pass
        self._refresh_all()

    def action_toggle_pause(self) -> None:
        paused = self.controller.toggle_pause()
        self.state.paused = paused
        self.state.apply_event(
            {
                "type": EVENT_LIFECYCLE,
                "phase": "paused" if paused else "running",
                "message": self.state.tr("paused_msg")
                if paused
                else self.state.tr("resumed_msg"),
                "elapsed_sec": self.state.elapsed_sec,
            }
        )
        self._log_requires_full_refresh = True
        self._refresh_all()

    def action_toggle_event_detail(self) -> None:
        self.state.detailed_events = not self.state.detailed_events
        self._log_requires_full_refresh = True
        self._refresh_events()

    def action_reset_view(self) -> None:
        if self.event_sink is not None:
            self._ignore_events_before_seq = self.event_sink.snapshot_sequence()
        else:
            drain_event_queue(self.event_queue)
        self.state.reset_view()
        self._rendered_event_count = 0
        self._log_requires_full_refresh = True
        self._placeholder_visible = False
        self._refresh_all()

    def _worker_is_running(self) -> bool:
        return self._worker_thread is not None and self._worker_thread.is_alive()
