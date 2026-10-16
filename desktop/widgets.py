"""Small native-painted widgets for the desktop workbench."""
from __future__ import annotations

from collections import deque
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


from desktop.i18n import t
from desktop.theme import PALETTES, stylesheet

STYLE = stylesheet(PALETTES["light"])


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


def line_icon(kind: str, color: str) -> QIcon:
    """Native vector strokes, independent of platform symbol fonts."""
    pixmap = QPixmap(40, 40)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    path = QPainterPath()
    if kind in ("play", "resume"):
        path.moveTo(6, 4); path.lineTo(16, 10); path.lineTo(6, 16); path.closeSubpath()
    elif kind == "pause":
        path.moveTo(7, 5); path.lineTo(7, 15); path.moveTo(13, 5); path.lineTo(13, 15)
    elif kind == "stop":
        path.addRoundedRect(QRectF(5, 5, 10, 10), 2, 2)
    elif kind == "export":
        path.moveTo(10, 2); path.lineTo(10, 12); path.moveTo(6, 8); path.lineTo(10, 12); path.lineTo(14, 8)
        path.moveTo(3, 13); path.lineTo(3, 17); path.lineTo(17, 17); path.lineTo(17, 13)
    elif kind == "history":
        for y in (5, 10, 15):
            path.moveTo(4, y); path.lineTo(5, y); path.moveTo(9, y); path.lineTo(17, y)
    elif kind == "settings":
        for x, y in ((5, 7), (10, 13), (15, 8)):
            path.moveTo(x, 3); path.lineTo(x, y - 2); path.addEllipse(QPointF(x, y), 2, 2)
            path.moveTo(x, y + 2); path.lineTo(x, 17)
    else:
        path.moveTo(2, 12); path.lineTo(5, 12); path.lineTo(8, 4); path.lineTo(12, 16); path.lineTo(15, 8); path.lineTo(18, 8)
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


class MetricCard(QFrame):
    def __init__(self, title: str, hint: str):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(5)
        self.title = label(title, "muted")
        layout.addWidget(self.title)
        self.value = label("—", "metric")
        layout.addWidget(self.value)
        self.hint = label(hint, "muted", True)
        layout.addWidget(self.hint)


class ResponsePlot(QWidget):
    """Two synchronized plots with bounded data, a time window and hover inspection."""
    def __init__(self):
        super().__init__()
        self.samples = deque(maxlen=6000)
        self.window_seconds = 0
        self.hover_x: float | None = None
        self.colors = PALETTES["light"]
        self.setMinimumSize(320, 220)
        self.setMouseTracking(True)
        self.retranslate()

    def retranslate(self):
        self.setAccessibleName(t("plot_accessible"))
        self.update()

    def set_theme(self, theme):
        self.colors = PALETTES[theme]
        self.update()

    def add_sample(self, sample: dict) -> bool:
        try:
            numeric = {k: float(sample[k]) for k in ("timestamp", "input", "setpoint", "pwm")}
        except (KeyError, ValueError, TypeError, OverflowError):
            return False
        if not all(math.isfinite(v) and abs(v) <= 1e15 for v in numeric.values()):
            return False
        if self.samples and numeric["timestamp"] < self.samples[-1]["timestamp"]:
            return False
        self.samples.append({**sample, **numeric})
        return True

    def visible_samples(self):
        samples = list(self.samples)
        if samples and self.window_seconds:
            cutoff = samples[-1]["timestamp"] - self.window_seconds * 1000
            samples = [s for s in samples if s["timestamp"] >= cutoff]
        return samples

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
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter):
        c = self.colors
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        width, height = self.width(), self.height()
        top = QRectF(52, 17, width - 67, height * .62 - 26)
        bottom = QRectF(52, height * .69, width - 67, max(25, height * .31 - 34))
        samples = self.visible_samples()
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
                painter.setPen(QPen(QColor(c.grid), 1))
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                painter.setPen(QColor(c.muted))
                painter.drawText(QRectF(0, y - 9, 43, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                                 f"{vmin + (vmax - vmin) * i / ticks:.0f}")
            for i in range(6):
                x = rect.left() + rect.width() * i / 5
                painter.setPen(QPen(QColor(c.grid), 1, Qt.PenStyle.DotLine))
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        painter.setPen(QColor(c.muted))
        for i in range(6):
            x = bottom.left() + bottom.width() * i / 5
            painter.drawText(QRectF(x - 25, bottom.bottom() + 5, 50, 20), Qt.AlignmentFlag.AlignCenter,
                             f"{xmin + (xmax - xmin) * i / 5:.0f}s")
        painter.drawText(QPointF(3, 11), "PV / SP")
        painter.drawText(QPointF(3, bottom.top() - 8), "OUT")
        if not samples:
            painter.setPen(QColor(c.muted))
            painter.drawText(top, Qt.AlignmentFlag.AlignCenter, t("plot_empty"))
            return

        def point(sample, key, rect, lo, hi):
            return QPointF(rect.left() + (sample["timestamp"] / 1000 - xmin) / (xmax - xmin) * rect.width(),
                           rect.bottom() - (sample[key] - lo) / max(hi - lo, 1e-9) * rect.height())

        for key, color, rect, lo, hi in (("pwm", c.output, bottom, umin, umax),
                                        ("setpoint", c.target, top, ymin, ymax),
                                        ("input", c.accent, top, ymin, ymax)):
            path = QPainterPath()
            path.moveTo(point(samples[0], key, rect, lo, hi))
            for sample in samples[1:]:
                path.lineTo(point(sample, key, rect, lo, hi))
            painter.save()
            painter.setClipRect(rect.adjusted(-1, -1, 1, 1))
            if key == "input":
                area = QPainterPath(path)
                area.lineTo(point(samples[-1], key, rect, lo, hi).x(), rect.bottom())
                area.lineTo(point(samples[0], key, rect, lo, hi).x(), rect.bottom())
                area.closeSubpath()
                gradient = QLinearGradient(0, rect.top(), 0, rect.bottom())
                start = QColor(color); start.setAlpha(42)
                end = QColor(color); end.setAlpha(2)
                gradient.setColorAt(0, start); gradient.setColorAt(1, end)
                painter.fillPath(area, gradient)
            pen = QPen(QColor(color), 2.3 if key == "input" else 1.6)
            if key == "setpoint":
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawPath(path)
            if len(samples) == 1:
                painter.drawEllipse(point(samples[0], key, rect, lo, hi), 2, 2)
            painter.restore()
        if self.hover_x is not None and top.left() <= self.hover_x <= top.right():
            target = xmin + (self.hover_x - top.left()) / top.width() * (xmax - xmin)
            sample = min(samples, key=lambda s: abs(s["timestamp"] / 1000 - target))
            p = point(sample, "input", top, ymin, ymax)
            painter.setPen(QPen(QColor(c.target), 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(p.x(), top.top()), QPointF(p.x(), bottom.bottom()))
            painter.setPen(QPen(QColor(c.accent), 2))
            painter.setBrush(QColor(c.surface))
            painter.drawEllipse(p, 4, 4)
            text = f"{sample['timestamp'] / 1000:.1f}s   PV {sample['input']:.2f}   SP {sample['setpoint']:.2f}   OUT {sample['pwm']:.1f}"
            box = QRectF(top.left() + 4, top.top(), top.width() - 8, 24)
            painter.fillRect(box, QColor(c.surface))
            painter.setPen(QColor(c.text))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
