"""Native desktop workbench for the shared PID tuning engine."""
from __future__ import annotations

from datetime import datetime
from queue import Empty
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from core.config import CONFIG
from desktop.session import DesktopSession, RunOptions, SessionArchive
from desktop.settings import SettingsDialog
from desktop.widgets import MetricCard, ResponsePlot, STYLE, card, label


PHASES = {
    "connecting": "正在连接", "connected": "已连接", "collecting": "正在采样",
    "llm_request": "分析响应", "observing": "稳定性观察", "observing_pending": "继续优化",
    "fallback": "使用保守兜底", "rollback": "回退到最佳参数", "completed": "调参完成",
    "stopped": "已停止", "error": "运行出错", "setpoint": "目标值已更新",
    "setpoint_warning": "目标值未生效", "warning": "运行提示",
}
REASONS = {
    "max_rounds_reached": "已完成设定轮数", "stopped_by_user": "已停止",
    "stable_rounds_reached": "已达到稳定标准", "low_error_converged": "低误差收敛",
    "llm_marked_done": "AI 已结束调参", "rollback_to_best": "已回退到最佳参数", "error": "运行出错",
}
STATUSES = {"STABLE": "稳定", "SLOW_RESPONSE": "响应偏慢", "OVERSHOOTING": "存在超调", "OSCILLATING": "存在振荡"}


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
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LLM PID Tuner · 桌面工作台")
        self.setWindowIcon(app_icon())
        self.resize(1440, 940)
        self.setMinimumSize(1120, 760)
        self.session: DesktopSession | None = None
        self.archive = SessionArchive()
        self._closing = False
        self._finished = False
        self._started_at = 0.
        self._round = 0
        self._last_phase = "准备就绪"
        self._latest_pid = None
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
        title.addWidget(label("WORKSPACE  /  CONTROL LAB", "muted"))
        title.addWidget(label("PID 调参工作台", "title"))
        title.addWidget(label("观察响应，理解每一次调整。", "muted"))
        heading.addLayout(title)
        heading.addStretch()
        self.badge = label("●  准备就绪", "badge")
        heading.addWidget(self.badge)
        self.export_button = QPushButton("导出结果  ↓")
        export_menu = QMenu(self)
        export_menu.addAction("采样数据 · CSV", lambda: self.export("csv"))
        export_menu.addAction("调参报告 · JSON", lambda: self.export("json"))
        self.export_button.setMenu(export_menu)
        self.export_button.setEnabled(False)
        heading.addWidget(self.export_button)
        content.addLayout(heading)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.metric_cards = [MetricCard("当前响应  /  PV", "等待采样"),
                             MetricCard("平均绝对误差", "每轮完成后更新"),
                             MetricCard("超调量", "相对当前目标值"),
                             MetricCard("稳态误差", "最后 20% 采样的平均误差")]
        for widget in self.metric_cards:
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
        chart_heading.addWidget(label("系统响应", "section"))
        chart_heading.addStretch()
        chart_heading.addWidget(label("● 实际响应", "muted"))
        chart_heading.addWidget(label("┄ 目标值", "muted"))
        self.range_select = QComboBox()
        for caption, seconds in [("全部采样", 0), ("最近 60 秒", 60), ("最近 120 秒", 120)]:
            self.range_select.addItem(caption, seconds)
        self.range_select.currentIndexChanged.connect(self._range_changed)
        chart_heading.addWidget(self.range_select)
        chart_layout.addLayout(chart_heading)
        self.plot = ResponsePlot()
        chart_layout.addWidget(self.plot, 1)
        chart_footer = QHBoxLayout()
        self.sample_label = label("0 个采样点", "muted")
        chart_footer.addWidget(self.sample_label)
        chart_footer.addStretch()
        chart_footer.addWidget(label("悬停查看数据  ·  下方为控制输出", "muted"))
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
        self.analysis.setPlainText("从一次仿真实验开始\n\n选择初始 PID 与目标值，点击「开始调参」。\n默认使用离线保守规则，无需 API Key；切换到 AI 模式可查看模型的实时分析。")
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["轮次", "平均误差", "稳态误差", "超调 %", "系统状态", "连续稳定轮数"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(600)
        self.tabs.addTab(self.analysis, "调参分析")
        self.tabs.addTab(self.table, "轮次记录")
        self.tabs.addTab(self.logs, "运行日志")
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
        self.statusBar().showMessage("本地仿真已就绪。配置初始参数后即可开始。")
        self.elapsed = label("LOCAL SESSION  ·  00:00", "muted")
        self.statusBar().addPermanentWidget(self.elapsed)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        self._mode_changed()

    def _sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(182)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(18, 30, 18, 20)
        layout.setSpacing(10)
        logo = label("∿  PID LAB", "brand")
        layout.addWidget(logo)
        layout.addWidget(label("LLM PID TUNER"))
        layout.addSpacing(38)
        layout.addWidget(label("工作空间"))
        self.overview = QPushButton("◉   实时调参")
        self.overview.setCheckable(True)
        self.overview.setChecked(True)
        self.history_nav = QPushButton("▤   轮次记录")
        self.history_nav.setCheckable(True)
        self.overview.clicked.connect(lambda: self._navigate(0))
        self.history_nav.clicked.connect(lambda: self._navigate(1))
        layout.addWidget(self.overview)
        layout.addWidget(self.history_nav)
        layout.addSpacing(20)
        layout.addWidget(label("偏好设置"))
        self.settings_button = QPushButton("⚙   连接设置")
        self.settings_button.clicked.connect(self.settings)
        layout.addWidget(self.settings_button)
        layout.addStretch()
        layout.addWidget(label("●  本机运行"))
        layout.addWidget(label("原生桌面工作台\nPython · Serial · Simulink", wrap=True))
        return sidebar

    def _controls(self):
        panel, layout = card()
        panel.setMinimumWidth(290)
        panel.setMaximumWidth(365)
        layout.addWidget(label("实验配置", "section"))
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
        for caption, key in [("本地热系统仿真", "python"), ("串口硬件", "hardware"), ("MATLAB / Simulink", "simulink")]:
            self.mode.addItem(caption, key)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        form.addRow("运行环境", self.mode)
        self.strategy = QComboBox()
        self.strategy.addItem("离线规则体验", "offline")
        self.strategy.addItem("AI 自动调参", "ai")
        self.strategy.currentIndexChanged.connect(self._strategy_changed)
        form.addRow("调参策略", self.strategy)
        self.port = QComboBox()
        self.port.setEditable(True)
        self.port.setAccessibleName("串口")
        self.refresh_ports = QPushButton("刷新")
        self.refresh_ports.clicked.connect(self._refresh_ports)
        port_row = QHBoxLayout()
        port_row.addWidget(self.port, 1)
        port_row.addWidget(self.refresh_ports)
        self.port_container = QWidget()
        self.port_container.setLayout(port_row)
        port_row.setContentsMargins(0, 0, 0, 0)
        self.port_caption = label("串口")
        form.addRow(self.port_caption, self.port_container)
        self._refresh_ports()
        self.setpoint = QDoubleSpinBox()
        self.setpoint.setRange(-1000000, 1000000)
        self.setpoint.setDecimals(2)
        self.setpoint.setValue(200)
        self.setpoint.setAccessibleName("目标值")
        form.addRow("目标值 SP", self.setpoint)
        self.apply_setpoint = QPushButton("应用运行时目标值")
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
            widget.setAccessibleName(f"初始 K{gain}")
            self.gains[gain] = widget
            col.addWidget(widget)
            gains_row.addLayout(col)
        form.addRow(gains_row)
        self.current_pid = label("当前 PID  ·  等待采样", "muted", True)
        form.addRow(self.current_pid)
        self.advanced_button = QPushButton("高级参数  ·  轮数 / 采样 / 速度")
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
        advanced_form.addRow("最大轮数", self.rounds)
        self.samples = QSpinBox()
        self.samples.setRange(50, 5000)
        self.samples.setSingleStep(50)
        self.samples.setValue(100)
        advanced_form.addRow("每轮采样", self.samples)
        self.speed = QComboBox()
        for factor in (1, 5, 10, 25, 50):
            self.speed.addItem(f"{factor}×", factor)
        self.speed.setCurrentIndex(2)
        advanced_form.addRow("仿真速度", self.speed)
        self.mode_hint = label("", "muted", True)
        form.addRow(self.mode_hint)
        self.start_button = QPushButton("▶   开始调参")
        self.start_button.setObjectName("primary")
        self.start_button.setMinimumHeight(40)
        self.start_button.clicked.connect(self.start_session)
        layout.addWidget(self.start_button)
        row = QHBoxLayout()
        self.pause_button = QPushButton("暂停")
        self.pause_button.clicked.connect(self.pause)
        self.pause_button.setEnabled(False)
        self.stop_button = QPushButton("停止")
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

    def _refresh_ports(self):
        current = self.port.currentText() or str(CONFIG.get("SERIAL_PORT", "DEMO"))
        self.port.clear()
        self.port.addItem("DEMO")
        try:
            from serial.tools.list_ports import comports
            for port in comports():
                self.port.addItem(port.device)
        except Exception as exc:
            self.port.setToolTip(f"串口扫描失败：{exc}")
        self.port.setCurrentText(current if current != "AUTO" else "DEMO")

    def _strategy_changed(self):
        if not hasattr(self, "mode_hint"):
            return
        if self.mode.currentData() == "python":
            self.mode_hint.setText("本地离线仿真，不调用模型。" if self.strategy.currentData() == "offline"
                                   else f"使用配置的 AI 模型：{CONFIG.get('LLM_MODEL_NAME', '')}")

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
        if mode == "hardware":
            self.mode_hint.setText("读取设备当前 PID 和目标值。开始后 AI 会写回参数；停止仅结束上位机调参。")
        elif mode == "simulink":
            self.mode_hint.setText("使用连接设置中的模型，读取模型当前 PID；曲线按仿真步返回。")
        else:
            self._strategy_changed()

    def _range_changed(self):
        self.plot.window_seconds = self.range_select.currentData()
        self.plot.update()

    def settings(self):
        if self.session and self.session.running:
            return
        SettingsDialog(self).exec()
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
            QMessageBox.warning(self, "检查实验配置", str(exc))
            return
        self.archive.close()
        self.archive = SessionArchive()
        self.plot.clear()
        self.table.setRowCount(0)
        self.logs.clear()
        self.analysis.setPlainText("正在启动实验…\n" + ("离线规则体验 · 不调用 AI 模型。" if options.strategy == "offline" else "等待采样完成后请求模型。"))
        for metric in self.metric_cards:
            metric.value.setText("—")
        self.current_pid.setText("当前 PID  ·  等待采样")
        self._round = 0
        self._latest_pid = None
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
        self.pause_button.setText("暂停")
        if not active:
            self._mode_changed()

    def pause(self):
        if not self.session:
            return
        paused = self.session.controller.toggle_pause()
        self.pause_button.setText("继续" if paused else "暂停")
        self.badge.setText("●  已暂停" if paused else f"●  {self._last_phase}")
        self.statusBar().showMessage("已暂停。进行中的模型请求会完成，但参数将在继续后应用。" if paused else "已继续运行。")

    def stop(self):
        if self.session:
            self.session.controller.request_stop()
            self.stop_button.setEnabled(False)
            self.pause_button.setEnabled(False)
            self.apply_setpoint.setEnabled(False)
            self.badge.setText("●  正在停止")
            self.statusBar().showMessage("正在结束后台任务并释放连接；进行中的网络或 MATLAB 调用可能需要等待。")

    def _setpoint_request(self):
        if self.session:
            self.session.controller.request_setpoint(self.setpoint.value())
            self.statusBar().showMessage(f"已请求目标值 {self.setpoint.value():g}，在下一次采样时应用。")

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
            self.sample_label.setText(f"{self.archive.count:,} 个采样点  ·  已评估 {self._round} 轮" +
                                      ("  ·  图中保留最近 6,000 点" if self.archive.count > 6000 else ""))
            self.export_button.setEnabled(self.archive.count > 0 or bool(self.archive.result))
        elapsed = int(time.monotonic() - self._started_at)
        self.elapsed.setText(f"LOCAL SESSION  ·  {elapsed // 60:02d}:{elapsed % 60:02d}")
        if self._finished and not self.session.running and self.session.events.empty():
            self.session = None
            self._set_active(False)
            if self._closing:
                self.close()

    def _event(self, event):
        kind = event["type"]
        if kind == "sample":
            self.archive.sample(event)
            self.plot.add_sample(event)
            self.metric_cards[0].value.setText(f"{event['input']:.2f}")
            self.metric_cards[0].hint.setText(f"SP {event['setpoint']:g}  ·  误差 {event['error']:.2f}")
            self._latest_pid = {k: event[k] for k in ("p", "i", "d")}
            pid = "   ".join(f"K{k} {event[k]:.4g}" for k in ("p", "i", "d"))
            if "p2" in event:
                pid += "\n控制器 2  " + "  ".join(f"K{k} {event[k + '2']:.4g}" for k in ("p", "i", "d"))
            self.current_pid.setText("当前 PID  ·  " + pid)
        elif kind == "round_metrics":
            self.archive.metrics.append(dict(event))
            self._round = event["round"]
            self.archive.round = self._round + 1
            for card_widget, key, suffix in zip(self.metric_cards[1:], ("avg_error", "overshoot", "steady_state_error"), ("", "%", "")):
                card_widget.value.setText(f"{event[key]:.2f}{suffix}")
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [str(event["round"]), f"{event['avg_error']:.3f}", f"{event['steady_state_error']:.3f}",
                      f"{event['overshoot']:.2f}", STATUSES.get(event["status"], event["status"]), str(event["stable_rounds"])]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
            self.table.scrollToBottom()
            self.progress.setValue(event["round"])
        elif kind == "decision":
            summary = self._safe_text(event.get("analysis_summary", ""))
            notes = self._safe_text("\n".join(event.get("guardrail_notes", [])))
            self.analysis.setPlainText(f"第 {event['round']} 轮 · {event.get('action', '')}\n\n{summary}" +
                                       (f"\n\n安全护栏\n{notes}" if notes else ""))
            self.logs.appendPlainText(f"第 {event['round']} 轮 · {summary}")
        elif kind == "rollback":
            self.analysis.setPlainText(self._safe_text(event.get("reason", "已回退到最佳参数。")))
        elif kind == "log":
            message = self._safe_text(event.get("message", ""))
            if event.get("label") == "llm_stream":
                self.analysis.setPlainText(message[-40000:])
                bar = self.analysis.verticalScrollBar()
                bar.setValue(bar.maximum())
            else:
                self.logs.appendPlainText(message)
        elif kind == "lifecycle":
            phase = event.get("phase", "")
            self._last_phase = PHASES.get(phase, phase)
            if not self.session.controller.is_paused and not self.session.controller.should_stop:
                self.badge.setText(f"●  {self._last_phase}")
            message = self._safe_text(event.get("message", ""))
            self.logs.appendPlainText(f"{datetime.now():%H:%M:%S}  {self._last_phase}  ·  {message}")
            self.statusBar().showMessage(message)
            if phase == "error":
                self.analysis.setPlainText(f"运行遇到问题\n\n{message}\n\n检查连接设置后可以重新开始。")
        elif kind == "session_finished":
            self.archive.result = event.get("result", {})
            final_pid = self.archive.result.get("final_pid")
            if final_pid:
                self.current_pid.setText("最终 PID  ·  " + "   ".join(f"K{k} {final_pid[k]:.4g}" for k in ("p", "i", "d")))
            reason = self.archive.result.get("completed_reason", "")
            text = REASONS.get(reason, reason or "已结束")
            self.badge.setText(f"●  {text}")
            self.statusBar().showMessage(f"{text}。可以导出数据，或调整配置开始新的实验。")
            self._finished = True

    def export(self, kind):
        name = f"pid-session-{datetime.now():%Y%m%d-%H%M%S}.{kind}"
        path, _ = QFileDialog.getSaveFileName(self, "导出本次实验", name, f"{kind.upper()} (*.{kind})")
        if not path:
            return
        try:
            if kind == "csv":
                self.archive.export_csv(path)
            else:
                self.archive.export_json(path)
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        self.statusBar().showMessage(f"已导出：{path}")

    def closeEvent(self, event):
        if self.session is not None:
            self._closing = True
            self.stop()
            event.ignore()
            return
        self.timer.stop()
        self.archive.close()
        event.accept()


def run_desktop() -> None:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("LLM PID Tuner")
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setStyleSheet(STYLE)
    window = Workbench()
    screen = app.primaryScreen()
    if screen is not None:
        area = screen.availableGeometry()
        window.resize(min(1440, area.width() - 50), min(940, area.height() - 70))
    window.show()
    app.exec()
