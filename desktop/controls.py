"""Paint clear control affordances with the active Qt palette."""
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainter, QPen, QPalette
from PySide6.QtWidgets import (
    QComboBox as QtComboBox, QDoubleSpinBox as QtDoubleSpinBox,
    QSpinBox as QtSpinBox, QStyle, QStyleOptionSpinBox, QStyledItemDelegate,
)


def chevron(painter, center, up=False):
    x, y = center.x(), center.y()
    direction = -1 if up else 1
    painter.drawLine(QPointF(x - 3, y - direction), QPointF(x, y + 2 * direction))
    painter.drawLine(QPointF(x, y + 2 * direction), QPointF(x + 3, y - direction))


def arrow_painter(widget):
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    group = QPalette.ColorGroup.Active if widget.isEnabled() else QPalette.ColorGroup.Disabled
    painter.setPen(QPen(widget.palette().color(group, QPalette.ColorRole.Text), 1.3))
    return painter


class QComboBox(QtComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Qt's menu delegate takes text colors from the closed combo. Sidebar
        # combos stay dark even in light mode; paint rows with the view's theme.
        self.setItemDelegate(QStyledItemDelegate(self))

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = arrow_painter(self)
        chevron(painter, QPointF(self.width() - 11, self.height() / 2))
        painter.end()


class _SpinArrows:
    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        painter = arrow_painter(self)
        for control, up in ((QStyle.SubControl.SC_SpinBoxUp, True), (QStyle.SubControl.SC_SpinBoxDown, False)):
            rect = self.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, control, self)
            chevron(painter, rect.center(), up)
        painter.end()


class QSpinBox(_SpinArrows, QtSpinBox):
    pass


class QDoubleSpinBox(_SpinArrows, QtDoubleSpinBox):
    pass
