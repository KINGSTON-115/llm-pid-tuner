"""Semantic colors shared by Qt widgets, native plots and icons."""
from dataclasses import dataclass
import sys

from PySide6.QtGui import QColor, QPalette


def native_titlebar(widget, dark):
    """Match Windows' native caption to the chosen application theme."""
    if sys.platform != "win32" or widget.windowHandle() is None:
        return
    from PySide6.QtWidgets import QApplication
    if QApplication.platformName() != "windows":
        return
    import ctypes
    from ctypes.wintypes import HWND
    enabled = ctypes.c_int(bool(dark))
    try:
        ctypes.windll.dwmapi.DwmSetWindowAttribute(HWND(int(widget.winId())), 20,
                                                  ctypes.byref(enabled), ctypes.sizeof(enabled))
    except (OSError, AttributeError):
        pass  # Older Windows builds simply keep their native caption color.


@dataclass(frozen=True)
class Colors:
    background: str
    surface: str
    raised: str
    text: str
    muted: str
    border: str
    grid: str
    accent: str
    accent_soft: str
    accent_text: str
    output: str
    target: str
    danger: str
    disabled: str
    sidebar: str
    sidebar_text: str


PALETTES = {
    "light": Colors("#f1f4f8", "#ffffff", "#f7f9fc", "#182d40", "#576b7d", "#dce4ec", "#e7edf3",
                    "#07796b", "#e4f4ef", "#ffffff", "#a36a16", "#687d9a", "#b53b4c", "#7d8b99", "#152a3c", "#bacbda"),
    "dark": Colors("#0c1420", "#152130", "#1b2a3b", "#e4edf7", "#a3b5c9", "#2c3e52", "#25364a",
                   "#65ddc1", "#203e3d", "#102b2b", "#f4bf68", "#9fb5d3", "#ff8b99", "#778aa0", "#101c2b", "#b7c9dd"),
}


def qt_palette(c: Colors) -> QPalette:
    palette = QPalette()
    roles = {"Window": c.background, "WindowText": c.text, "Base": c.surface,
             "AlternateBase": c.raised, "Text": c.text, "Button": c.surface,
             "ButtonText": c.text, "ToolTipBase": c.raised, "ToolTipText": c.text,
             "Highlight": c.accent, "HighlightedText": c.accent_text,
             "PlaceholderText": c.muted, "Link": c.accent, "Light": c.raised,
             "Mid": c.border, "Dark": c.border}
    for role, color in roles.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(c.disabled))
    return palette


def stylesheet(c: Colors) -> str:
    return f"""
QWidget {{ color: {c.text}; font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 12px; }}
QMainWindow, QDialog {{ background: {c.background}; }}
QFrame#sidebar {{ background: {c.sidebar}; border: none; }}
QFrame#sidebar QLabel {{ color: {c.sidebar_text}; background: transparent; }}
QFrame#sidebar QLabel#brand {{ color: #ffffff; font-size: 23px; font-weight: 700; }}
QFrame#sidebar QLabel#navCaption {{ font-size: 10px; font-weight: 600; padding-top: 14px; }}
QFrame#sidebar QPushButton {{ text-align: left; background: transparent; color: {c.sidebar_text}; border: 1px solid transparent; padding: 12px 14px; }}
QFrame#sidebar QPushButton:checked {{ background: #254453; color: #95ead4; }}
QFrame#sidebar QPushButton:hover {{ background: #203b4e; color: #ffffff; }}
QFrame#sidebar QComboBox {{ background: #1c3247; color: #e3edf7; border: 1px solid #3c5268; }}
QFrame#card {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 14px; }}
QFrame#card QLabel {{ background: transparent; border: none; }}
QLabel#title {{ font-size: 26px; font-weight: 700; }}
QLabel#eyebrow {{ color: {c.accent}; font-size: 10px; font-weight: 700; }}
QLabel#section {{ font-size: 15px; font-weight: 600; }}
QLabel#muted {{ color: {c.muted}; }}
QLabel#metric {{ font-family: 'Segoe UI'; font-size: 30px; font-weight: 600; }}
QLabel#badge {{ color: {c.accent}; background: {c.accent_soft}; border-radius: 8px; padding: 8px 12px; font-weight: 600; }}
QLabel#badge[state='error'] {{ color: {c.danger}; }}
QLabel#badge[state='paused'], QLabel#badge[state='stopping'] {{ color: {c.output}; background: {c.raised}; }}
QPushButton {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 8px; padding: 9px 14px; font-weight: 500; }}
QPushButton:hover {{ background: {c.accent_soft}; border-color: {c.accent}; }}
QPushButton:pressed {{ background: {c.raised}; }}
QPushButton:focus {{ border: 1px solid {c.accent}; }}
QPushButton#primary {{ background: {c.accent}; border-color: {c.accent}; color: {c.accent_text}; font-weight: 700; }}
QPushButton#primary:hover {{ border: 1px solid {c.text}; }}
QPushButton#danger {{ color: {c.danger}; }}
QPushButton:disabled {{ background: {c.raised}; color: {c.disabled}; border-color: {c.border}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {c.raised}; border: 1px solid {c.border}; border-radius: 7px; padding: 7px 9px; min-height: 20px; selection-background-color: {c.accent}; selection-color: {c.accent_text}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {c.accent}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {c.disabled}; background: {c.raised}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{ background: transparent; border: none; width: 16px; }}
QComboBox::drop-down {{ border: none; width: 21px; }}
QComboBox QAbstractItemView {{ background: {c.surface}; color: {c.text}; selection-background-color: {c.accent_soft}; selection-color: {c.accent}; padding: 5px; border: 1px solid {c.border}; }}
QPlainTextEdit {{ background: transparent; border: none; padding: 12px; selection-background-color: {c.accent}; selection-color: {c.accent_text}; }}
QTableWidget {{ background: {c.surface}; border: none; gridline-color: {c.grid}; selection-background-color: {c.accent_soft}; selection-color: {c.text}; alternate-background-color: {c.raised}; }}
QHeaderView::section {{ background: {c.raised}; color: {c.muted}; border: none; padding: 10px 8px; font-size: 11px; }}
QTableWidget::item {{ padding: 6px; }}
QTabWidget::pane {{ border: 1px solid {c.border}; border-radius: 9px; background: {c.surface}; }}
QTabBar::tab {{ padding: 11px 20px; color: {c.muted}; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {c.accent}; border-bottom: 2px solid {c.accent}; }}
QTabBar::tab:hover {{ background: {c.raised}; }}
QScrollArea {{ border: none; background: transparent; }}
QWidget#controlBody {{ background: {c.surface}; }}
QScrollBar:vertical {{ width: 8px; background: {c.surface}; }}
QScrollBar::handle:vertical {{ background: {c.border}; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QSplitter::handle {{ background: transparent; }}
QStatusBar {{ background: {c.surface}; border-top: 1px solid {c.border}; color: {c.muted}; }}
QStatusBar::item {{ border: none; }}
QProgressBar {{ border: none; border-radius: 3px; background: {c.border}; max-height: 4px; }}
QProgressBar::chunk {{ background: {c.accent}; border-radius: 3px; }}
QToolTip {{ background: {c.raised}; color: {c.text}; border: 1px solid {c.border}; padding: 8px; }}
QMenu {{ background: {c.surface}; color: {c.text}; border: 1px solid {c.border}; padding: 6px; }}
QMenu::item {{ padding: 9px 18px; border-radius: 5px; }}
QMenu::item:selected {{ background: {c.accent_soft}; color: {c.accent}; }}
"""
