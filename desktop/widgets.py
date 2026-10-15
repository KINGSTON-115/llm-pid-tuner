"""Small native-painted widgets for the desktop workbench."""
from __future__ import annotations

from collections import deque
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


STYLE = """
QWidget { color: #233244; font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 12px; }
QMainWindow, QDialog { background: #f4f6f9; }
QFrame#sidebar { background: #142b3b; border: none; }
QFrame#sidebar QLabel { color: #9fb4c3; background: transparent; }
QFrame#sidebar QLabel#brand { color: #ffffff; font-size: 21px; font-weight: 700; }
QFrame#sidebar QPushButton { text-align: left; background: transparent; color: #b9cad6; border: none; padding: 13px 16px; }
QFrame#sidebar QPushButton:checked { background: #234657; color: #8ee0cc; }
QFrame#sidebar QPushButton:hover { background: #203c4d; }
QFrame#card { background: #ffffff; border: 1px solid #e3e8ef; border-radius: 12px; }
QFrame#card QLabel { background: transparent; border: none; }
QLabel#title { font-size: 25px; font-weight: 700; color: #172e40; }
QLabel#section { font-size: 15px; font-weight: 600; }
QLabel#muted { color: #7c8999; }
QLabel#metric { font-family: 'Segoe UI'; font-size: 28px; font-weight: 600; color: #1e3747; }
QLabel#badge { color: #167d70; background: #e3f4ef; border-radius: 6px; padding: 6px 10px; }
QPushButton { background: white; border: 1px solid #dce3eb; border-radius: 7px; padding: 9px 15px; font-weight: 500; }
QPushButton:hover { background: #edf5f4; border-color: #85bcb2; }
QPushButton:pressed { background: #d9ebe6; }
QPushButton#primary { background: #168778; border-color: #168778; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #117768; }
QPushButton#danger { color: #ba4e4e; }
QPushButton:disabled { background: #eff2f5; color: #a4aebb; border-color: #e4e8ee; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #f9fafc; border: 1px solid #dce3eb; border-radius: 6px; padding: 7px 9px; min-height: 20px; selection-background-color: #168778; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #168778; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color: #98a4b2; background: #f0f3f6; }
QComboBox QAbstractItemView { background: white; selection-background-color: #e2f2ee; selection-color: #167d70; padding: 4px; }
QPlainTextEdit { background: transparent; border: none; font-size: 12px; selection-background-color: #c7e9df; }
QTableWidget { background: white; border: none; gridline-color: #eef1f5; selection-background-color: #e5f3ef; selection-color: #233244; alternate-background-color: #f8fafc; }
QHeaderView::section { background: #f7f9fb; color: #7a8999; border: none; padding: 9px 8px; font-size: 11px; }
QTableWidget::item { padding: 5px; }
QTabWidget::pane { border: 1px solid #e3e8ef; border-radius: 8px; background: white; }
QTabBar::tab { padding: 11px 20px; color: #7c8999; }
QTabBar::tab:selected { color: #168778; background: #e6f4ef; }
QScrollArea { border: none; background: transparent; }
QWidget#controlBody { background: white; }
QScrollBar:vertical { width: 7px; background: transparent; }
QScrollBar::handle:vertical { background: #cdd6df; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QSplitter::handle { background: transparent; }
QStatusBar { background: #ffffff; border-top: 1px solid #e3e8ef; color: #7c8999; }
QProgressBar { border: none; border-radius: 3px; background: #eaf0f4; max-height: 5px; }
QProgressBar::chunk { background: #34aa94; border-radius: 3px; }
QToolTip { background: #142b3b; color: white; border: none; padding: 8px; }
"""


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(12)
    return frame, layout


class MetricCard(QFrame):
    def __init__(self, title: str, hint: str):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(3)
        layout.addWidget(label(title, "muted"))
        self.value = label("—", "metric")
        layout.addWidget(self.value)
        self.hint = label(hint, "muted")
        layout.addWidget(self.hint)


class ResponsePlot(QWidget):
    """Two synchronized plots with bounded data, a time window and hover inspection."""
    def __init__(self):
        super().__init__()
        self.samples = deque(maxlen=6000)
        self.window_seconds = 0
        self.hover_x: float | None = None
        self.setMinimumSize(380, 240)
        self.setMouseTracking(True)
        self.setAccessibleName("实时响应与控制输出曲线")

    def add_sample(self, sample: dict) -> None:
        if all(math.isfinite(float(sample.get(k, 0))) for k in ("timestamp", "input", "setpoint", "pwm")):
            self.samples.append(dict(sample))

    def clear(self) -> None:
        self.samples.clear()
        self.hover_x = None
        self.update()

    def mouseMoveEvent(self, event):
        self.hover_x = event.position().x()
        self.update()

    def leaveEvent(self, _event):
        self.hover_x = None
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont("Segoe UI", 9))
        width, height = self.width(), self.height()
        top = QRectF(52, 17, width - 67, height * .62 - 26)
        bottom = QRectF(52, height * .69, width - 67, height * .24 - 6)
        samples = list(self.samples)
        if samples and self.window_seconds:
            cutoff = samples[-1]["timestamp"] - self.window_seconds * 1000
            samples = [s for s in samples if s["timestamp"] >= cutoff]
        xmin = samples[0]["timestamp"] / 1000 if samples else 0
        xmax = samples[-1]["timestamp"] / 1000 if samples else 60
        xmax = max(xmin + 1, xmax)
        values = [float(s[k]) for s in samples for k in ("input", "setpoint")]
        low, high = (min(values), max(values)) if values else (0, 250)
        margin = max((high - low) * .12, 5)
        ymin, ymax = low - margin, high + margin
        outputs = [float(s["pwm"]) for s in samples] or [0, 255]
        umin, umax = min(0., min(outputs)), max(1., max(outputs))
        for rect, vmin, vmax, ticks in ((top, ymin, ymax, 4), (bottom, umin, umax, 2)):
            for i in range(ticks + 1):
                y = rect.bottom() - rect.height() * i / ticks
                painter.setPen(QPen(QColor("#edf1f5"), 1))
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                painter.setPen(QColor("#8995a3"))
                painter.drawText(QRectF(0, y - 9, 43, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                                 f"{vmin + (vmax - vmin) * i / ticks:.0f}")
            for i in range(6):
                x = rect.left() + rect.width() * i / 5
                painter.setPen(QPen(QColor("#f0f3f6"), 1))
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        painter.setPen(QColor("#8995a3"))
        for i in range(6):
            x = bottom.left() + bottom.width() * i / 5
            painter.drawText(QRectF(x - 25, bottom.bottom() + 5, 50, 20), Qt.AlignmentFlag.AlignCenter,
                             f"{xmin + (xmax - xmin) * i / 5:.0f}s")
        painter.drawText(QPointF(3, 11), "PV / SP")
        painter.drawText(QPointF(3, bottom.top() - 8), "OUT")
        if not samples:
            painter.setPen(QColor("#8b99a8"))
            painter.drawText(top, Qt.AlignmentFlag.AlignCenter, "等待开始实验\n点击「开始调参」，在这里观察真实响应")
            return

        def point(sample, key, rect, lo, hi):
            return QPointF(rect.left() + (sample["timestamp"] / 1000 - xmin) / (xmax - xmin) * rect.width(),
                           rect.bottom() - (sample[key] - lo) / max(hi - lo, 1e-9) * rect.height())

        for key, color, rect, lo, hi in (("pwm", "#d5a650", bottom, umin, umax),
                                        ("setpoint", "#90a4ba", top, ymin, ymax),
                                        ("input", "#199d89", top, ymin, ymax)):
            path = QPainterPath()
            path.moveTo(point(samples[0], key, rect, lo, hi))
            for sample in samples[1:]:
                path.lineTo(point(sample, key, rect, lo, hi))
            painter.save()
            painter.setClipRect(rect.adjusted(-1, -1, 1, 1))
            pen = QPen(QColor(color), 2 if key == "input" else 1.5)
            if key == "setpoint":
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawPath(path)
            painter.restore()
        if self.hover_x is not None and top.left() <= self.hover_x <= top.right():
            target = xmin + (self.hover_x - top.left()) / top.width() * (xmax - xmin)
            sample = min(samples, key=lambda s: abs(s["timestamp"] / 1000 - target))
            p = point(sample, "input", top, ymin, ymax)
            painter.setPen(QPen(QColor("#78909f"), 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(p.x(), top.top()), QPointF(p.x(), bottom.bottom()))
            text = f"{sample['timestamp'] / 1000:.1f}s    PV {sample['input']:.2f}    SP {sample['setpoint']:.2f}"
            painter.setPen(QColor("#335267"))
            painter.drawText(QRectF(top.left(), top.top(), top.width(), 20), Qt.AlignmentFlag.AlignCenter, text)
