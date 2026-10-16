"""Native desktop workbench for the shared PID tuning engine."""
from __future__ import annotations

from datetime import datetime
from collections import deque
import math
from queue import Empty
import time

from PySide6.QtCore import Qt, QTimer, QSignalBlocker
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QMainWindow, QMenu,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from core.config import CONFIG
from desktop.session import DesktopSession, RunOptions, SessionArchive
from desktop.settings import SettingsDialog, warning
from desktop.widgets import MetricCard, ResponsePlot, card, label, line_icon
from desktop.i18n import Bindings, t, token, runtime
from desktop.preferences import Preferences
from desktop.theme import PALETTES
from desktop.controls import QComboBox, QDoubleSpinBox, QSpinBox


def app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("#142b3b"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#7dddc4"), 4))
    path = QPainterPath()
    path.moveTo(8, 44)
    path.cubicTo(20, 44, 17, 14, 28, 17)
    path.cubicTo(39, 20, 31, 37, 42, 33)
    path.cubicTo(46, 31, 50, 30, 56, 30)
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


class Workbench(QMainWindow):
    def __init__(self, language=None):
        super().__init__()
        self.preferences = Preferences(self, language)
        self.copy = Bindings()
        self.copy.bind(self.setWindowTitle, "window")
        self.setWindowIcon(app_icon())
        self.resize(1440, 940)
        self.setMinimumSize(1120, 760)
        self.session: DesktopSession | None = None
        self.archive = SessionArchive()
        self._closing = False
        self._finished = False
        self._started_at = 0.
        self._round = 0
        self._last_phase = "ready"
        self._latest_pid = None
        self._last_sample = None
        self._analysis_event = {"type": "empty"}
        self._log_events = deque(maxlen=600)
        self._status = ("ready_hint", {})
        self._elapsed_seconds = 0
        root = QWidget()
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(self._sidebar())
        main = QWidget()
        content = QVBoxLayout(main)
        content.setContentsMargins(26, 22, 26, 18)
        content.setSpacing(16)
        shell.addWidget(main, 1)

        heading = QHBoxLayout()
        title = QVBoxLayout()
        title.setSpacing(5)
        title.addWidget(self._label("eyebrow", "eyebrow"))
        title.addWidget(self._label("title", "title"))
        title.addWidget(self._label("subtitle", "muted"))
        heading.addLayout(title)
        heading.addStretch()
        self.badge = label("", "badge")
        heading.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self.export_button = self._button("export")
        export_menu = QMenu(self)
        for key, kind in (("csv", "csv"), ("json", "json")):
            action = export_menu.addAction("", lambda checked=False, fmt=kind: self.export(fmt))
            self.copy.bind(action.setText, key)
        self.export_button.setMenu(export_menu)
        self.export_button.setEnabled(False)
        heading.addWidget(self.export_button, 0, Qt.AlignmentFlag.AlignVCenter)
        content.addLayout(heading)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.metric_cards = []
        for caption, hint in (("pv", "waiting"), ("mae", "round_hint"), ("overshoot", "overshoot_hint"), ("steady", "steady_hint")):
            widget = MetricCard("", "")
            self.copy.bind(widget.title.setText, caption)
            self.copy.bind(widget.hint.setText, hint)
            self.metric_cards.append(widget)
            metrics.addWidget(widget)
        content.addLayout(metrics)

        vertical = QSplitter(Qt.Orientation.Vertical)
        vertical.setChildrenCollapsible(False)
        vertical.setHandleWidth(10)
        middle = QSplitter(Qt.Orientation.Horizontal)
        middle.setChildrenCollapsible(False)
        middle.setHandleWidth(14)
        chart_card, chart_layout = card()
        chart_heading = QHBoxLayout()
        chart_heading.addWidget(self._label("response", "section"))
        chart_heading.addStretch()
        self.response_legend = self._label("actual", "muted")
        self.target_legend = self._label("target", "muted")
        chart_heading.addWidget(self.response_legend)
        chart_heading.addWidget(self.target_legend)
        self.range_select = QComboBox()
        self._choices(self.range_select, [("all_samples", 0), ("last_60", 60), ("last_120", 120)])
        self.range_select.currentIndexChanged.connect(self._range_changed)
        chart_heading.addWidget(self.range_select)
        chart_layout.addLayout(chart_heading)
        self.plot = ResponsePlot()
        chart_layout.addWidget(self.plot, 1)
        chart_footer = QHBoxLayout()
        self.sample_label = label("", "muted")
        chart_footer.addWidget(self.sample_label)
        chart_footer.addStretch()
        self.hover_hint = self._label("hover_hint", "muted")
        chart_footer.addWidget(self.hover_hint)
        chart_layout.addLayout(chart_footer)
        middle.addWidget(chart_card)
        middle.addWidget(self._controls())
        middle.setSizes([760, 310])
        middle.setStretchFactor(0, 1)
        middle.setStretchFactor(1, 0)
        vertical.addWidget(middle)

        activity_card, activity_layout = card()
        activity_layout.setContentsMargins(14, 8, 14, 10)
        self.tabs = QTabWidget()
        self.analysis = QPlainTextEdit()
        self.analysis.setReadOnly(True)
        self.analysis.setMaximumBlockCount(600)
        self.table = QTableWidget(0, 6)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(600)
        for widget, key in ((self.analysis, "analysis"), (self.table, "history"), (self.logs, "logs")):
            index = self.tabs.addTab(widget, "")
            self.copy.bind(lambda text, i=index: self.tabs.setTabText(i, text), key)
            self.copy.bind(widget.setAccessibleName, key)
        self.tabs.currentChanged.connect(self._navigate)
        activity_layout.addWidget(self.tabs)
        vertical.addWidget(activity_card)
        vertical.setSizes([450, 190])
        vertical.setStretchFactor(0, 3)
        vertical.setStretchFactor(1, 1)
        content.addWidget(vertical, 1)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        content.addWidget(self.progress)
        self.elapsed = label("", "muted")
        self.statusBar().addPermanentWidget(self.elapsed)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        self._mode_changed()
        self.preferences.language_changed.connect(self.retranslate)
        self.preferences.theme_changed.connect(self._theme_changed)
        self.retranslate()
        self._theme_changed(self.preferences.resolved_theme)

    def _label(self, key, name="", wrap=False):
        widget = label("", name, wrap)
        self.copy.bind(widget.setText, key)
        return widget

    def _button(self, key):
        widget = QPushButton()
        self.copy.bind(widget.setText, key)
        self.copy.bind(widget.setAccessibleName, key)
        return widget

    def _choices(self, combo, choices):
        for key, data in choices:
            index = combo.count()
            combo.addItem("", data)
            self.copy.bind(lambda text, c=combo, i=index: self._item_text(c, i, text), key)

    @staticmethod
    def _item_text(combo, index, text):
        with QSignalBlocker(combo):
            combo.setItemText(index, text)

    def _sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(190)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(18, 30, 18, 20)
        layout.setSpacing(10)
        logo = label("PID / LAB", "brand")
        layout.addWidget(logo)
        layout.addWidget(label("LLM PID TUNER"))
        layout.addSpacing(24)
        layout.addWidget(self._label("workspace", "navCaption"))
        self.overview = self._button("overview")
        self.overview.setCheckable(True)
        self.overview.setChecked(True)
        self.history_nav = self._button("history")
        self.history_nav.setCheckable(True)
        self.overview.clicked.connect(lambda: self._navigate(0))
        self.history_nav.clicked.connect(lambda: self._navigate(1))
        layout.addWidget(self.overview)
        layout.addWidget(self.history_nav)
        layout.addSpacing(20)
        layout.addWidget(self._label("preferences", "navCaption"))
        self.settings_button = self._button("connections")
        self.settings_button.clicked.connect(self.settings)
        layout.addWidget(self.settings_button)
        layout.addSpacing(8)
        layout.addWidget(self._label("appearance"))
        self.theme_select = QComboBox()
        self._choices(self.theme_select, [("system", "system"), ("light", "light"), ("dark", "dark")])
        self.copy.bind(self.theme_select.setAccessibleName, "appearance")
        self.theme_select.setCurrentIndex(self.theme_select.findData(self.preferences.theme))
        self.theme_select.currentIndexChanged.connect(lambda: self._preference_changed("theme"))
        layout.addWidget(self.theme_select)
        layout.addWidget(self._label("language"))
        self.language_select = QComboBox()
        self.language_select.addItem("简体中文", "zh")
        self.language_select.addItem("English", "en")
        self.copy.bind(self.language_select.setAccessibleName, "language")
        self.language_select.setCurrentIndex(self.language_select.findData(self.preferences.language))
        self.language_select.currentIndexChanged.connect(lambda: self._preference_changed("language"))
        layout.addWidget(self.language_select)
        layout.addStretch()
        layout.addWidget(self._label("local"))
        layout.addWidget(self._label("native", wrap=True))
        layout.addWidget(label("Python · Serial · Simulink"))
        return sidebar

    def _controls(self):
        panel, layout = card()
        panel.setMinimumWidth(300)
        panel.setMaximumWidth(355)
        layout.addWidget(self._label("config", "section"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("controlBody")
        form = QFormLayout(body)
        form.setContentsMargins(0, 0, 8, 0)
        form.setSpacing(10)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        self.mode = QComboBox()
        self._choices(self.mode, [("python", "python"), ("hardware", "hardware")])
        self.mode.addItem("MATLAB / Simulink", "simulink")
        self.mode.currentIndexChanged.connect(self._mode_changed)
        form.addRow(self._label("environment"), self.mode)
        self.strategy = QComboBox()
        self._choices(self.strategy, [("offline", "offline"), ("ai", "ai")])
        self.strategy.currentIndexChanged.connect(self._strategy_changed)
        form.addRow(self._label("strategy"), self.strategy)
        self.port = QComboBox()
        self.port.setEditable(True)
        self.copy.bind(self.port.setAccessibleName, "port")
        self.refresh_ports = self._button("refresh")
        self.refresh_ports.clicked.connect(self._refresh_ports)
        port_row = QHBoxLayout()
        port_row.addWidget(self.port, 1)
        port_row.addWidget(self.refresh_ports)
        self.port_container = QWidget()
        self.port_container.setLayout(port_row)
        port_row.setContentsMargins(0, 0, 0, 0)
        self.port_caption = self._label("port")
        form.addRow(self.port_caption, self.port_container)
        self._refresh_ports()
        self.setpoint = QDoubleSpinBox()
        self.setpoint.setRange(-1000000, 1000000)
        self.setpoint.setDecimals(2)
        self.setpoint.setValue(200)
        self.copy.bind(self.setpoint.setAccessibleName, "target")
        form.addRow(self._label("sp"), self.setpoint)
        self.apply_setpoint = self._button("apply_sp")
        self.apply_setpoint.clicked.connect(self._setpoint_request)
        self.apply_setpoint.setEnabled(False)
        form.addRow(self.apply_setpoint)
        self.gains = {}
        gains_row = QHBoxLayout()
        for gain, value in (("p", 1.), ("i", .1), ("d", .05)):
            col = QVBoxLayout()
            col.setSpacing(4)
            col.addWidget(label(f"K{gain}", "muted"))
            widget = QDoubleSpinBox()
            widget.setDecimals(4)
            widget.setRange(0, 5000 if gain == "p" else 500)
            widget.setValue(value)
            widget.setStyleSheet("QDoubleSpinBox { padding: 7px 3px; }")
            widget.setMinimumWidth(64)
            self.copy.bind(widget.setAccessibleName, "gain_accessible", gain=gain)
            self.gains[gain] = widget
            col.addWidget(widget)
            gains_row.addLayout(col)
        form.addRow(gains_row)
        self.current_pid = label("", "muted", True)
        form.addRow(self.current_pid)
        self.advanced_button = self._button("advanced")
        self.advanced_button.setCheckable(True)
        form.addRow(self.advanced_button)
        self.advanced = QWidget()
        advanced_form = QFormLayout(self.advanced)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.setSpacing(8)
        form.addRow(self.advanced)
        self.advanced.hide()
        self.advanced_button.toggled.connect(self.advanced.setVisible)
        self.rounds = QSpinBox()
        self.rounds.setRange(1, 500)
        self.rounds.setValue(12)
        advanced_form.addRow(self._label("rounds"), self.rounds)
        self.samples = QSpinBox()
        self.samples.setRange(50, 5000)
        self.samples.setSingleStep(50)
        self.samples.setValue(100)
        advanced_form.addRow(self._label("batch"), self.samples)
        self.speed = QComboBox()
        for factor in (1, 5, 10, 25, 50):
            self.speed.addItem(f"{factor}×", factor)
        self.speed.setCurrentIndex(2)
        advanced_form.addRow(self._label("speed"), self.speed)
        self.mode_hint = label("", "muted", True)
        form.addRow(self.mode_hint)
        self.start_button = self._button("start")
        self.start_button.setObjectName("primary")
        self.start_button.setMinimumHeight(40)
        self.start_button.clicked.connect(self.start_session)
        layout.addWidget(self.start_button)
        row = QHBoxLayout()
        self.pause_button = self._button("pause")
        self.pause_button.clicked.connect(self.pause)
        self.pause_button.setEnabled(False)
        self.stop_button = self._button("stop")
        self.stop_button.setObjectName("danger")
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.setEnabled(False)
        row.addWidget(self.pause_button)
        row.addWidget(self.stop_button)
        layout.addLayout(row)
        return panel

    def _navigate(self, index):
        self.tabs.setCurrentIndex(index)
        self.overview.setChecked(index == 0)
        self.history_nav.setChecked(index == 1)

    def _preference_changed(self, kind):
        combo = self.theme_select if kind == "theme" else self.language_select
        try:
            getattr(self.preferences, "set_" + kind)(combo.currentData())
        except (OSError, ValueError) as exc:
            with QSignalBlocker(combo):
                combo.setCurrentIndex(combo.findData(getattr(self.preferences, kind)))
            warning(self, t("save_error"), t("preference_error", error=self._safe_text(exc)))

    def _theme_changed(self, theme):
        c = PALETTES[theme]
        with QSignalBlocker(self.theme_select):
            self.theme_select.setCurrentIndex(self.theme_select.findData(self.preferences.theme))
        self.plot.set_theme(theme)
        self.response_legend.setStyleSheet(f"color: {c.accent};")
        self.target_legend.setStyleSheet(f"color: {c.target};")
        self.response_legend.setText("● " + t("actual"))
        self.target_legend.setText("┄ " + t("target"))
        for button, icon, color in ((self.overview, "response", c.sidebar_text),
                                     (self.history_nav, "history", c.sidebar_text),
                                     (self.settings_button, "settings", c.sidebar_text),
                                     (self.start_button, "play", c.accent_text),
                                     (self.stop_button, "stop", c.danger),
                                     (self.export_button, "export", c.text)):
            button.setIcon(line_icon(icon, color))
        self._render_badge()

    def _show_status(self, key, **values):
        self._status = (key, values)
        self._render_status()

    def _render_status(self):
        key, values = self._status
        if key == "runtime":
            message = runtime(values["message"])
        else:
            if key == "finished_hint":
                values = {"reason": token(values["reason"])}
            message = t(key, **values)
        self.statusBar().showMessage(message)

    def _render_badge(self):
        state = self._last_phase
        if self.session and not self._finished:
            if self.session.controller.should_stop:
                state = "stopping"
            elif self.session.controller.is_paused:
                state = "paused"
        self.badge.setText("●  " + token(state))
        self.badge.setProperty("state", state)
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)
        paused = bool(self.session and self.session.controller.is_paused)
        self.pause_button.setText(t("resume" if paused else "pause"))
        self.pause_button.setAccessibleName(self.pause_button.text())
        self.pause_button.setIcon(line_icon("resume" if paused else "pause", PALETTES[self.preferences.resolved_theme].text))

    def _render_counts(self):
        self.sample_label.setText(t("samples", count=self.archive.count, round=self._round) +
                                  (t("plot_limit") if self.archive.count > 6000 else ""))
        seconds = self._elapsed_seconds
        self.elapsed.setText(t("elapsed", time=f"{seconds // 60:02d}:{seconds % 60:02d}"))

    def _render_pid(self):
        final = self.archive.result.get("final_pid")
        pid = final or self._latest_pid
        if pid:
            text = "   ".join(f"K{k} {pid[k]:.4g}" for k in ("p", "i", "d"))
            secondary = self.archive.result.get("secondary_pid") if final else None
            if secondary is None and self._last_sample and "p2" in self._last_sample:
                secondary = {k: self._last_sample[k + "2"] for k in ("p", "i", "d")}
            if secondary:
                text += "\n" + t("controller2") + " · " + "  ".join(f"K{k} {secondary[k]:.4g}" for k in ("p", "i", "d"))
        else:
            text = t("waiting")
        self.current_pid.setText(t("final_pid" if final else "current_pid", pid=text))
        if self._last_sample:
            self.metric_cards[0].hint.setText(t("error_hint", sp=self._last_sample["setpoint"], error=self._last_sample["error"]))

    def _decision_text(self, event):
        heading = t("decision", round=event["round"], action=token(event.get("action", "")))
        # Only built-in offline/fallback summaries go through the known-copy translator.
        raw = event.get("analysis_summary", "")
        summary = runtime(raw) if event.get("fallback_used") else raw
        return heading, summary

    def _render_analysis(self):
        event = self._analysis_event
        kind = event["type"]
        if kind == "empty":
            message = t("analysis_empty")
        elif kind == "starting":
            message = t("starting_" + event["strategy"])
        elif kind == "decision":
            heading, summary = self._decision_text(event)
            notes = "\n".join(runtime(note) for note in event.get("guardrail_notes", []))
            message = heading + "\n\n" + summary + ("\n\n" + t("guardrails") + "\n" + notes if notes else "")
        elif kind == "rollback":
            message = runtime(event["reason"]) if event.get("reason") else t("rollback_text")
        elif kind == "error":
            message = t("run_error", error=runtime(event["message"]))
        else:
            message = event.get("message", "")[-40000:]
        self.analysis.setPlainText(message)

    def _log_text(self, event):
        if event["type"] == "decision":
            heading, summary = self._decision_text(event)
            return heading + " · " + summary
        message = runtime(event.get("message", ""))
        if event["type"] == "lifecycle":
            return f"{event['time']}  {token(event.get('phase', ''))} · {message}"
        return message

    def _append_log(self, event):
        self._log_events.append(event)
        self.logs.appendPlainText(self._log_text(event))

    def retranslate(self):
        self.copy.refresh()
        with QSignalBlocker(self.language_select):
            self.language_select.setCurrentIndex(self.language_select.findData(self.preferences.language))
        self.plot.retranslate()
        self.table.setHorizontalHeaderLabels([t(key) for key in ("col_round", "col_mae", "col_steady", "col_overshoot", "col_status", "col_stable")])
        for row, metrics in enumerate(self.archive.metrics):
            self.table.item(row, 4).setText(token(metrics["status"]))
        scroll = self.logs.verticalScrollBar()
        position, at_end = scroll.value(), scroll.value() == scroll.maximum()
        self.logs.setPlainText("\n".join(self._log_text(event) for event in self._log_events))
        scroll.setValue(scroll.maximum() if at_end else position)
        self._render_analysis()
        self._render_pid()
        self._render_counts()
        self._render_status()
        self._update_mode_hint()
        self._theme_changed(self.preferences.resolved_theme)

    def _refresh_ports(self):
        current = self.port.currentText() or str(CONFIG.get("SERIAL_PORT", "DEMO"))
        self._port_error = ""
        self.port.setToolTip("")
        self.port.clear()
        self.port.addItem("DEMO")
        try:
            from serial.tools.list_ports import comports
            for port in comports():
                self.port.addItem(port.device)
        except Exception as exc:
            self._port_error = self._safe_text(exc)
            self.port.setToolTip(t("scan_error", error=self._port_error))
        self.port.setCurrentText(current if current != "AUTO" else "DEMO")

    def _strategy_changed(self):
        if not hasattr(self, "mode_hint"):
            return
        self._update_mode_hint()

    def _update_mode_hint(self):
        mode = self.mode.currentData()
        if mode == "hardware":
            text = t("hardware_hint")
        elif mode == "simulink":
            text = t("simulink_hint")
        else:
            text = t("offline_hint") if self.strategy.currentData() == "offline" else t("ai_hint", model=CONFIG.get("LLM_MODEL_NAME", ""))
        self.mode_hint.setText(text)
        if self._port_error:
            self.port.setToolTip(t("scan_error", error=self._port_error))

    def _mode_changed(self):
        if not hasattr(self, "mode_hint"):
            return
        mode = self.mode.currentData()
        self.port_caption.setVisible(mode == "hardware")
        self.port_container.setVisible(mode == "hardware")
        self.speed.setEnabled(mode == "python")
        for widget in self.gains.values():
            widget.setEnabled(mode == "python")
        if mode != "python":
            self.strategy.setCurrentIndex(1)
        self.strategy.setEnabled(mode == "python")
        self._update_mode_hint()

    def _range_changed(self):
        self.plot.window_seconds = self.range_select.currentData()
        self.plot.update()

    def settings(self):
        if self.session and self.session.running:
            return
        dialog = SettingsDialog(self)
        dialog.exec()
        dialog.deleteLater()
        self._strategy_changed()

    def start_session(self):
        if self.session is not None:
            return
        options = RunOptions(mode=self.mode.currentData(), strategy=self.strategy.currentData(),
                             setpoint=self.setpoint.value(), **{k: v.value() for k, v in self.gains.items()},
                             rounds=self.rounds.value(), samples=self.samples.value(),
                             speed=self.speed.currentData(), port=self.port.currentText().strip())
        try:
            session = DesktopSession(options)
        except ValueError as exc:
            warning(self, t("check_run"), self._safe_text(exc))
            return
        self.archive.close()
        self.archive = SessionArchive()
        self.plot.clear()
        self.table.setRowCount(0)
        self.export_button.setEnabled(False)
        self.logs.clear()
        self._log_events.clear()
        self._analysis_event = {"type": "starting", "strategy": options.strategy}
        self._render_analysis()
        for metric in self.metric_cards:
            metric.value.setText("—")
        self._round = 0
        self._latest_pid = None
        self._last_sample = None
        self._last_phase = "connecting"
        self._elapsed_seconds = 0
        self._render_pid()
        self._render_counts()
        self._finished = False
        self._started_at = time.monotonic()
        self.session = session
        self.progress.setRange(0, options.rounds)
        self.progress.setValue(0)
        self._set_active(True)
        session.start()

    def _set_active(self, active):
        for widget in (self.mode, self.strategy, self.port, self.refresh_ports, self.rounds,
                       self.samples, self.speed, self.settings_button, *self.gains.values()):
            widget.setEnabled(not active)
        self.start_button.setEnabled(not active)
        self.pause_button.setEnabled(active)
        self.stop_button.setEnabled(active)
        self.apply_setpoint.setEnabled(active)
        self._render_badge()
        if not active:
            self._mode_changed()

    def pause(self):
        if not self.session:
            return
        paused = self.session.controller.toggle_pause()
        self._render_badge()
        self._show_status("paused_hint" if paused else "resumed_hint")

    def stop(self):
        if self.session:
            self.session.controller.request_stop()
            self.stop_button.setEnabled(False)
            self.pause_button.setEnabled(False)
            self.apply_setpoint.setEnabled(False)
            self._render_badge()
            self._show_status("stopping_hint")

    def _setpoint_request(self):
        if self.session:
            self.session.controller.request_setpoint(self.setpoint.value())
            self._show_status("sp_requested", value=self.setpoint.value())

    def _safe_text(self, text):
        message = str(text)
        for field in ("LLM_API_KEY", "ORCAROUTER_API_KEY"):
            key = str(CONFIG.get(field, ""))
            if key:
                message = message.replace(key, "••••")
        return message

    def _poll(self):
        if self.session is None:
            return
        changed = False
        for _ in range(1000):
            try:
                event = self.session.events.get_nowait()
            except Empty:
                break
            self._event(event)
            changed = True
        if changed:
            self.plot.update()
            self.export_button.setEnabled(self.archive.count > 0 or bool(self.archive.result))
        self._elapsed_seconds = int(time.monotonic() - self._started_at)
        self._render_counts()
        if self._finished and not self.session.running and self.session.events.empty():
            self.session = None
            self._set_active(False)
            if self._closing:
                self.close()

    def _event(self, event):
        kind = event["type"]
        if kind == "sample":
            try:
                fields = ("timestamp", "input", "setpoint", "pwm", "error", "p", "i", "d")
                if any(key in event for key in ("p2", "i2", "d2")):
                    fields += ("p2", "i2", "d2")
                values = {key: float(event[key]) for key in fields}
            except (KeyError, ValueError, TypeError, OverflowError):
                return
            if not all(math.isfinite(value) for value in values.values()):
                return
            event = {**event, **values}
            if not self.plot.add_sample(event):
                return
            self.archive.sample(event)
            self._last_sample = dict(event)
            self.metric_cards[0].value.setText(f"{event['input']:.2f}")
            self._latest_pid = {k: event[k] for k in ("p", "i", "d")}
            self._render_pid()
        elif kind == "round_metrics":
            self.archive.metrics.append(dict(event))
            self._round = event["round"]
            self.archive.round = self._round + 1
            for card_widget, key, suffix in zip(self.metric_cards[1:], ("avg_error", "overshoot", "steady_state_error"), ("", "%", "")):
                card_widget.value.setText(f"{event[key]:.2f}{suffix}")
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [str(event["round"]), f"{event['avg_error']:.3f}", f"{event['steady_state_error']:.3f}",
                      f"{event['overshoot']:.2f}", token(event["status"]), str(event["stable_rounds"])]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
            self.table.scrollToBottom()
            self.progress.setValue(event["round"])
        elif kind == "decision":
            self._analysis_event = {**event, "analysis_summary": self._safe_text(event.get("analysis_summary", "")),
                                    "guardrail_notes": [self._safe_text(note) for note in event.get("guardrail_notes", [])]}
            self._render_analysis()
            self._append_log(self._analysis_event)
        elif kind == "rollback":
            self._analysis_event = {**event, "reason": self._safe_text(event.get("reason", ""))}
            self._render_analysis()
        elif kind == "log":
            message = self._safe_text(event.get("message", ""))
            if event.get("label") == "llm_stream":
                self._analysis_event = {"type": "stream", "message": message[-40000:]}
                self._render_analysis()
                bar = self.analysis.verticalScrollBar()
                bar.setValue(bar.maximum())
            else:
                self._append_log({**event, "message": message})
        elif kind == "lifecycle":
            phase = event.get("phase", "")
            self._last_phase = phase
            self._render_badge()
            message = self._safe_text(event.get("message", ""))
            self._append_log({**event, "message": message, "time": f"{datetime.now():%H:%M:%S}"})
            if not self.session or not (self.session.controller.is_paused or self.session.controller.should_stop):
                self._show_status("runtime", message=message)
            if phase == "error":
                self._analysis_event = {"type": "error", "message": message}
                self._render_analysis()
        elif kind == "session_finished":
            self.archive.result = event.get("result", {})
            reason = self.archive.result.get("completed_reason", "")
            self._finished = True
            self._last_phase = reason or "ended"
            self._render_badge()
            self._render_pid()
            self._show_status("finished_hint", reason=reason or "ended")

    def export(self, kind):
        name = f"pid-session-{datetime.now():%Y%m%d-%H%M%S}.{kind}"
        path, _ = QFileDialog.getSaveFileName(self, t("export_title"), name, f"{kind.upper()} (*.{kind})", options=QFileDialog.Option.DontUseNativeDialog)
        if not path:
            return
        try:
            if kind == "csv":
                self.archive.export_csv(path)
            else:
                self.archive.export_json(path)
        except OSError as exc:
            warning(self, t("export_error"), self._safe_text(exc))
            return
        self._show_status("exported", path=path)

    def closeEvent(self, event):
        if self.session is not None:
            self._closing = True
            self.stop()
            event.ignore()
            return
        self.timer.stop()
        self.archive.close()
        event.accept()


def run_desktop(language=None) -> None:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("LLM PID Tuner")
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 9))
    window = Workbench(language=language)
    screen = app.primaryScreen()
    if screen is not None:
        area = screen.availableGeometry()
        window.resize(min(1440, area.width() - 50), min(940, area.height() - 70))
    window.show()
    app.exec()
