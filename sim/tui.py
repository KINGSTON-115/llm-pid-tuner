from __future__ import annotations

from collections import deque
from dataclasses import field
import threading
import time
from queue import Queue
from typing import Any, Callable

from rich.markup import escape as markup_escape
from core.compat import slotted_dataclass
from core.i18n import get_language
from textual.app import App, ComposeResult
from textual.containers import Horizontal
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

# ---------------------------------------------------------------------------
# Translations
# ---------------------------------------------------------------------------
# NOTE: Translation values are plain labels only — no space-padding for
# alignment.  Render functions build the display lines so that CJK full-width
# characters never cause column-misalignment.
# ---------------------------------------------------------------------------
TRANSLATIONS: dict[str, dict[str, str]] = {
    "zh": {
        # waiting placeholders
        "waiting_status": "等待仿真数据...",
        "waiting_summary": "等待数据...",
        "waiting_help": "加载中...",
        # status-bar labels
        "lbl_round": "轮",
        "lbl_elapsed": "耗时",
        "lbl_status": "状态",
        "lbl_phase": "阶段",
        "lbl_paused": "暂停",
        "lbl_setpoint": "目标",
        "lbl_input": "当前",
        "lbl_error": "误差",
        "lbl_pwm": "PWM",
        "lbl_pid": "PID",
        # panel titles (border labels)
        "title_status_panel": "状态",
        "title_metrics": "指  标",
        "title_decision": "决  策",
        "title_message": "消  息",
        "title_events": "事件日志",
        "title_help": "快捷键",
        # summary metric labels
        "lbl_avg_error": "平均误差",
        "lbl_max_error": "最大误差",
        "lbl_steady_error": "稳态误差",
        "lbl_overshoot": "超  调",
        "lbl_zero_cross": "过零次数",
        "lbl_stable_rounds": "稳定轮数",
        "lbl_action": "动  作",
        "lbl_flags": "标  记",
        # help hotkeys
        "help_q": "退出",
        "help_s": "保存并退出",
        "help_p": "暂停/继续",
        "help_l": "日志详情",
        "help_r": "清空视图",
        "help_n": "下一轮",
        "help_browse": "滚轮 / PgUp / PgDn / ↑↓  浏览日志",
        "help_done": "调参完成，按 n 可以上次结果为起点继续新一轮",
        # booleans
        "paused_yes": "是",
        "paused_no": "否",
        # event / flag labels
        "flags_none": "无",
        "no_decision": "暂无决策。",
        "summary_cleared": "摘要已清空。",
        "no_events": "暂无事件。",
        "event_fallback": "兜底",
        "event_guardrail": "护栏",
        "event_rollback": "回滚",
        "event_elapsed": "耗时",
        # system messages
        "stopping": "等待仿真线程安全退出...",
        "saving_exit": "保存当前 PID 并等待线程安全退出...",
        "paused_msg": "已暂停仿真。",
        "resumed_msg": "已恢复仿真。",
    },
    "en": {
        # waiting placeholders
        "waiting_status": "Waiting for simulation data...",
        "waiting_summary": "Pending...",
        "waiting_help": "Loading...",
        # status-bar labels
        "lbl_round": "Round",
        "lbl_elapsed": "Elapsed",
        "lbl_status": "Status",
        "lbl_phase": "Phase",
        "lbl_paused": "Paused",
        "lbl_setpoint": "SP",
        "lbl_input": "In",
        "lbl_error": "Err",
        "lbl_pwm": "PWM",
        "lbl_pid": "PID",
        # panel titles
        "title_status_panel": "Status",
        "title_metrics": "Metrics",
        "title_decision": "Decision",
        "title_message": "Message",
        "title_events": "Event Log",
        "title_help": "Hotkeys",
        # summary metric labels
        "lbl_avg_error": "avg_error",
        "lbl_max_error": "max_error",
        "lbl_steady_error": "ss_error",
        "lbl_overshoot": "overshoot",
        "lbl_zero_cross": "zero_x",
        "lbl_stable_rounds": "stable",
        "lbl_action": "action",
        "lbl_flags": "flags",
        # help hotkeys
        "help_q": "Quit",
        "help_s": "Save & Exit",
        "help_p": "Pause/Resume",
        "help_l": "Log Detail",
        "help_r": "Reset View",
        "help_n": "Next Round",
        "help_browse": "Wheel / PgUp / PgDn / ↑↓  browse log",
        "help_done": "Tuning done. Press n to start another round from the last result.",
        # booleans
        "paused_yes": "yes",
        "paused_no": "no",
        # event / flag labels
        "flags_none": "none",
        "no_decision": "No decision yet.",
        "summary_cleared": "Summary cleared.",
        "no_events": "No events yet.",
        "event_fallback": "fallback",
        "event_guardrail": "guardrail",
        "event_rollback": "rollback",
        "event_elapsed": "elapsed",
        # system messages
        "stopping": "Waiting for worker to stop...",
        "saving_exit": "Saving the current PID and waiting for the worker to stop...",
        "paused_msg": "Simulation paused.",
        "resumed_msg": "Simulation resumed.",
    },
}


# ---------------------------------------------------------------------------
# PanelState — pure data + rendering logic, no Textual dependency
# ---------------------------------------------------------------------------
@slotted_dataclass
class PanelState:
    max_events: int = 100
    detailed_events: bool = False
    mode_label: str = "Python"
    language: str = field(default_factory=get_language)
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
    secondary_pid: dict[str, float] | None = None
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
        lang = self.language if self.language in TRANSLATIONS else "en"
        return TRANSLATIONS[lang][key]

    # ------------------------------------------------------------------
    # Event processing
    # ------------------------------------------------------------------
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
            if "p2" in event:
                self.secondary_pid = {
                    "p": float(event.get("p2", 0.0)),
                    "i": float(event.get("i2", 0.0)),
                    "d": float(event.get("d2", 0.0)),
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

    # ------------------------------------------------------------------
    # Rendering — produces Rich markup strings (markup=True panels)
    # ------------------------------------------------------------------
    def render_status_text(self) -> str:
        """Render a compact status bar that fits both single and dual-controller runs."""
        indicator = "⏸" if self.paused else "▶"
        paused_text = self.tr("paused_yes") if self.paused else self.tr("paused_no")

        status_color = {
            "STABLE": "green",
            "UNSTABLE": "yellow",
            "DIVERGED": "red",
            "IDLE": "dim",
            "TUNING": "cyan",
        }.get(self.current_status.upper(), "white")

        abs_err = abs(self.current_error)
        err_color = "red" if abs_err > 5 else "yellow" if abs_err > 1 else "green"

        line1 = (
            f"[bold cyan]{indicator}[/bold cyan] "
            f"[bold]{markup_escape(self.mode_label)}[/bold]  "
            f"[dim]{self.tr('lbl_round')}[/dim] [cyan]{self.current_round}[/cyan]  "
            f"[dim]{self.tr('lbl_elapsed')}[/dim] [cyan]{self.elapsed_sec:.1f}s[/cyan]  "
            f"[dim]{self.tr('lbl_status')}[/dim] "
            f"[{status_color}]{markup_escape(self.current_status)}[/{status_color}]  "
            f"[dim]{self.tr('lbl_phase')}[/dim] "
            f"[yellow]{markup_escape(self.current_phase)}[/yellow]  "
            f"[dim]{self.tr('lbl_paused')}[/dim] [dim]{paused_text}[/dim]"
        )
        line2 = (
            f"[dim]{self.tr('lbl_setpoint')}[/dim] {self.current_setpoint:.1f}  "
            f"[dim]{self.tr('lbl_input')}[/dim] {self.current_input:.1f}  "
            f"[dim]{self.tr('lbl_error')}[/dim] "
            f"[{err_color}]{self.current_error:+.2f}[/{err_color}]  "
            f"[dim]{self.tr('lbl_pwm')}[/dim] {self.current_pwm:.1f}"
        )
        if self.secondary_pid is None:
            line3 = (
                f"[dim]{self.tr('lbl_pid')}[/dim]  "
                f"P [bold]{self.current_pid['p']:.4f}[/bold]  "
                f"I [bold]{self.current_pid['i']:.4f}[/bold]  "
                f"D [bold]{self.current_pid['d']:.4f}[/bold]"
            )
            return f"{line1}\n[dim]{'─' * 60}[/dim]\n{line2}\n{line3}"

        line3 = (
            f"[dim]{self.tr('lbl_pid')} C1[/dim]  "
            f"P [bold]{self.current_pid['p']:.4f}[/bold]  "
            f"I [bold]{self.current_pid['i']:.4f}[/bold]  "
            f"D [bold]{self.current_pid['d']:.4f}[/bold]"
        )
        line4 = (
            f"[dim]{self.tr('lbl_pid')} C2[/dim]  "
            f"P [bold]{self.secondary_pid['p']:.4f}[/bold]  "
            f"I [bold]{self.secondary_pid['i']:.4f}[/bold]  "
            f"D [bold]{self.secondary_pid['d']:.4f}[/bold]"
        )
        return "\n".join((line1, line2, line3, line4))

    def render_summary_text(self) -> str:
        """Left-panel summary with Rich colour highlights.

        Labels are plain (no space-padding), so CJK full-width characters
        never mis-align values.
        """
        avg_err = float(self.metrics["avg_error"])
        max_err = float(self.metrics["max_error"])
        ss_err = float(self.metrics["steady_state_error"])
        overshoot = float(self.metrics["overshoot"])
        zero_x = int(self.metrics["zero_crossings"])

        def _ec(v: float) -> str:
            return "red" if v > 5 else "yellow" if v > 1 else "green"

        ov_color = (
            "magenta" if overshoot > 20 else "yellow" if overshoot > 10 else "white"
        )
        sr_color = "green" if self.stable_rounds > 0 else "dim"

        flags_raw = (
            ", ".join(self.latest_flags) if self.latest_flags else self.tr("flags_none")
        )
        flags_text = markup_escape(flags_raw)
        flags_color = "yellow" if self.latest_flags else "dim"

        analysis = markup_escape(self.latest_analysis)

        phase_msg = markup_escape(self.phase_message)

        def metric(label: str, value: str, color: str) -> str:
            return f"  [dim]{label}:[/dim] [{color}]{value}[/{color}]"

        lines = [
            f"[bold cyan]◆ {self.tr('title_metrics')}[/bold cyan]",
            metric(self.tr("lbl_avg_error"), f"{avg_err:.3f}", _ec(avg_err)),
            metric(self.tr("lbl_max_error"), f"{max_err:.3f}", _ec(max_err)),
            metric(self.tr("lbl_steady_error"), f"{ss_err:.3f}", _ec(ss_err)),
            metric(self.tr("lbl_overshoot"), f"{overshoot:.2f}%", ov_color),
            metric(self.tr("lbl_zero_cross"), str(zero_x), "white"),
            metric(self.tr("lbl_stable_rounds"), str(self.stable_rounds), sr_color),
            "",
            f"[bold cyan]◆ {self.tr('lbl_pid')}[/bold cyan]",
            f"  [dim]C1:[/dim] P [bold]{self.current_pid['p']:.4f}[/bold]  "
            f"I [bold]{self.current_pid['i']:.4f}[/bold]  "
            f"D [bold]{self.current_pid['d']:.4f}[/bold]",
            "",
            f"[bold cyan]◆ {self.tr('title_decision')}[/bold cyan]",
            metric(
                self.tr("lbl_action"), markup_escape(self.latest_action), "bold white"
            ),
            f"  [dim]{self.tr('lbl_flags')}:[/dim] [{flags_color}]{flags_text}[/{flags_color}]",
            f"  [dim]{analysis}[/dim]",
            "",
            f"[bold cyan]◆ {self.tr('title_message')}[/bold cyan]",
            f"  [dim]{phase_msg}[/dim]",
        ]
        if self.secondary_pid is not None:
            lines.insert(
                9,
                f"  [dim]C2:[/dim] P [bold]{self.secondary_pid['p']:.4f}[/bold]  "
                f"I [bold]{self.secondary_pid['i']:.4f}[/bold]  "
                f"D [bold]{self.secondary_pid['d']:.4f}[/bold]",
            )
        return "\n".join(lines)

    def render_help_text(self) -> str:
        """One-line hotkey strip with reverse-video key labels."""
        sep = "  [dim]│[/dim]  "

        def hk(key: str, desc: str) -> str:
            # Use reverse video for the key character (no square brackets needed)
            return f"[reverse bold] {key} [/reverse bold] {desc}"

        if self.tuning_done:
            line1 = (
                hk("n", self.tr("help_n"))
                + sep
                + hk("s", self.tr("help_s"))
                + sep
                + hk("q", self.tr("help_q"))
            )
            return f"{line1}\n[dim]{self.tr('help_done')}[/dim]"

        line1 = (
            hk("s", self.tr("help_s"))
            + sep
            + hk("q", self.tr("help_q"))
            + sep
            + hk("p", self.tr("help_p"))
            + sep
            + hk("l", self.tr("help_l"))
            + sep
            + hk("r", self.tr("help_r"))
        )
        return f"{line1}\n[dim]{self.tr('help_browse')}[/dim]"

    def render_event_lines(self) -> list[str]:
        return [
            self._format_event(event, detailed=self.detailed_events)
            for event in self.event_history
        ]

    def _format_event(self, event: RuntimeEvent, detailed: bool) -> str:  # noqa: C901
        event_type = event.get("type")

        if event_type == EVENT_DECISION:
            rnd = event.get("round", "?")
            action = markup_escape(str(event.get("action", "UNKNOWN")))
            analysis = markup_escape(str(event.get("analysis_summary", "")))
            # Round number bold-green, then action + analysis together in cyan
            line = f"[bold green]R{rnd}[/bold green] [cyan]{action}: {analysis}[/cyan]"
            if event.get("fallback_used"):
                label = markup_escape(self.tr("event_fallback"))
                line += f" [yellow]\\[{label}][/yellow]"
            if detailed and event.get("guardrail_notes"):
                notes = markup_escape(
                    "; ".join(str(n) for n in event.get("guardrail_notes", []))
                )
                gr = markup_escape(self.tr("event_guardrail"))
                line += f" | {gr}: {notes}"
            return line

        if event_type == EVENT_ROLLBACK:
            rnd = event.get("round", "?")
            rb = markup_escape(self.tr("event_rollback"))
            line = f"[bold yellow]R{rnd} {rb}[/bold yellow]"
            if detailed:
                reason = markup_escape(str(event.get("reason", "")))
                line += f" | {reason}"
            return line

        if event_type == EVENT_LIFECYCLE:
            phase = str(event.get("phase", "info"))
            message = markup_escape(str(event.get("message", "")))
            phase_color = {
                "collecting": "blue",
                "llm_request": "magenta",
                "completed": "bright_green",
                "finished": "bright_green",
                "best_result": "bright_green",
                "stopped": "yellow",
                "error": "red",
                "warm_start": "cyan",
                "doctor": "cyan",
            }.get(phase.lower(), "dim")
            phase_esc = markup_escape(phase)
            # \\[ in f-string → \[ in string → Rich renders literal [
            line = f"[{phase_color}]\\[{phase_esc}] {message}[/{phase_color}]"
            if detailed:
                elapsed = float(event.get("elapsed_sec", 0.0))
                el = markup_escape(self.tr("event_elapsed"))
                line += f" | {el}: {elapsed:.1f}s"
            return line

        if event_type == EVENT_LOG:
            label = str(event.get("label", "log"))
            message = str(event.get("message", ""))
            if not detailed:
                message = message.replace("\r", "").replace("\n", " ")
            message_esc = markup_escape(message)
            label_color = {
                "llm_stream": "bright_magenta",
                "llm_log": "magenta",
                "warm_start": "cyan",
                "info": "cyan",
                "doctor": "cyan",
            }.get(label.lower(), "dim")
            label_esc = markup_escape(label)
            line = f"[{label_color}]\\[{label_esc}] {message_esc}[/{label_color}]"
            if event.get("fallback_used"):
                fb = markup_escape(self.tr("event_fallback"))
                line += f" [yellow]\\[{fb}][/yellow]"
            return line.rstrip()

        return markup_escape(str(event))


# ---------------------------------------------------------------------------
# TUI Application
# ---------------------------------------------------------------------------
class SimulationTUIApp(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }

    #status {
        height: 6;
        border: round $accent;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 0 1;
    }

    #main {
        height: 1fr;
    }

    #summary {
        width: 50;
        border: round $success;
        border-title-color: $success;
        border-title-style: bold;
        padding: 1 1;
    }

    #events {
        border: round $accent;
        border-title-color: $accent;
        border-title-style: bold;
    }

    #help {
        height: 4;
        border: round $primary;
        border-title-color: $primary;
        border-title-style: bold;
        padding: 0 1;
    }
    """

    BINDINGS = [
        ("q", "request_quit", "Quit"),
        ("s", "save_and_exit", "Save and exit"),
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
        next_round_factory: Callable[[dict[str, Any]], Callable[[], None]] | None = None,
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
        yield Static(self.state.tr("waiting_status"), id="status", markup=True)
        with Horizontal(id="main"):
            yield Static(self.state.tr("waiting_summary"), id="summary", markup=True)
            yield RichLog(
                id="events", wrap=True, highlight=False, markup=True, auto_scroll=True
            )
        yield Static(self.state.tr("waiting_help"), id="help", markup=True)

    def on_mount(self) -> None:
        # Set translated border titles
        try:
            self.query_one(
                "#status"
            ).border_title = f" {self.state.tr('title_status_panel')} "
            self.query_one(
                "#summary"
            ).border_title = f" {self.state.tr('title_metrics')} "
            self.query_one(
                "#events", RichLog
            ).border_title = f" {self.state.tr('title_events')} "
            self.query_one("#help").border_title = f" {self.state.tr('title_help')} "
        except NoMatches:
            pass

        if self.worker_target is not None:
            self._worker_thread = threading.Thread(
                target=self.worker_target, name="simulation-tui-worker"
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
                if event.get("type") == EVENT_LIFECYCLE and str(
                    event.get("phase", "")
                ).lower() in {"completed", "finished", "stopped", "error"}:
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

        # Determine whether the user is at the bottom before any changes.
        at_bottom = log.max_scroll_y <= 0 or log.scroll_y >= log.max_scroll_y - 1

        # Dynamically control auto_scroll on the append path (not overriding
        # _enable_history_browsing which sets it to False unconditionally).
        if not self._history_browsing_enabled:
            if log.auto_scroll != at_bottom:
                log.auto_scroll = at_bottom

        if self._log_requires_full_refresh:
            saved_y = log.scroll_y
            log.clear()
            self._rendered_event_count = 0
            self._placeholder_visible = False
            self._log_requires_full_refresh = False

            if not lines:
                log.write(self.state.tr("no_events"))
                self._placeholder_visible = True
                return

            for line in lines:
                log.write(line)
            self._rendered_event_count = len(lines)

            # Restore scroll position if the user had scrolled up.
            if not at_bottom and saved_y > 0:
                self.call_after_refresh(
                    lambda _l=log, _y=saved_y: _l.scroll_to(y=_y, animate=False)
                )
            return

        if not lines:
            if not self._placeholder_visible:
                log.clear()
                log.write(self.state.tr("no_events"))
                self._placeholder_visible = True
                self._rendered_event_count = 0
            return

        if self._rendered_event_count > len(lines):
            saved_y = log.scroll_y
            log.clear()
            self._rendered_event_count = 0
            self._placeholder_visible = False
            for line in lines:
                log.write(line)
            self._rendered_event_count = len(lines)
            if not at_bottom and saved_y > 0:
                self.call_after_refresh(
                    lambda _l=log, _y=saved_y: _l.scroll_to(y=_y, animate=False)
                )
            return

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

    def _request_shutdown(self, *, phase: str, message_key: str) -> None:
        self.controller.request_stop()
        if not self._worker_is_running():
            self.exit()
            return
        self._shutdown_requested = True
        self.state.apply_event(
            {
                "type": EVENT_LIFECYCLE,
                "phase": phase,
                "message": self.state.tr(message_key),
                "elapsed_sec": self.state.elapsed_sec,
            }
        )
        self._log_requires_full_refresh = True
        self._refresh_all()

    def action_request_quit(self) -> None:
        self._request_shutdown(phase="stopping", message_key="stopping")

    def action_save_and_exit(self) -> None:
        # 触发保存最佳 PID 的逻辑
        if self._last_result and hasattr(self._last_result, "best_pid"):
            best = self._last_result.best_pid
            if best:
                self.state.apply_event(
                    {
                        "type": EVENT_LIFECYCLE,
                        "phase": "saving",
                        "message": f"Saved best PID: P={best.get('p', 0):.3f}, I={best.get('i', 0):.3f}, D={best.get('d', 0):.3f}",
                        "elapsed_sec": self.state.elapsed_sec,
                    }
                )
        self._request_shutdown(phase="saving", message_key="saving_exit")

    def action_next_round(self) -> None:
        if (
            not self.state.tuning_done
            or self.next_round_factory is None
            or self._worker_is_running()
        ):
            return

        if self.event_sink is not None:
            self._ignore_events_before_seq = self.event_sink.snapshot_sequence()
        else:
            drain_event_queue(self.event_queue)

        self.controller = SimulationController()
        new_worker = self.next_round_factory(self._last_result)
        self.state.tuning_done = False
        self.state.reset_view()
        self._history_browsing_enabled = False
        self._rendered_event_count = 0
        self._log_requires_full_refresh = True
        self._placeholder_visible = False
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
        self.state.tuning_done = False
        self._rendered_event_count = 0
        self._log_requires_full_refresh = True
        self._placeholder_visible = False
        self._refresh_all()

    def _worker_is_running(self) -> bool:
        return self._worker_thread is not None and self._worker_thread.is_alive()
