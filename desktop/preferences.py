"""Application-wide appearance and language; persist before applying changes."""
from PySide6.QtCore import QObject, Signal, Qt, QLibraryInfo, QTranslator, QEvent
from PySide6.QtWidgets import QApplication, QWidget

from core.config import CONFIG
from core.i18n import get_language, set_language
from desktop.theme import PALETTES, qt_palette, stylesheet, native_titlebar


class Preferences(QObject):
    language_changed = Signal()
    theme_changed = Signal(str)

    def __init__(self, parent=None, language=None):
        super().__init__(parent)
        self.app = QApplication.instance()
        self.theme = CONFIG.get("DESKTOP_THEME", "system")
        if self.theme not in ("light", "dark", "system"):
            self.theme = "system"
        selected = language or CONFIG.get("DESKTOP_LANGUAGE", get_language())
        self.language = selected if selected in ("zh", "en") else get_language()
        self.translator = QTranslator(self)
        self.app.styleHints().colorSchemeChanged.connect(self._system_changed)
        self._apply_language()
        self._apply_theme()
        self.app.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
            native_titlebar(obj, self.resolved_theme == "dark")
        return False

    @property
    def resolved_theme(self):
        if self.theme == "system":
            return "dark" if self.app.styleHints().colorScheme() == Qt.ColorScheme.Dark else "light"
        return self.theme

    def set_theme(self, value):
        if value not in ("light", "dark", "system"):
            raise ValueError(value)
        if value == self.theme:
            return
        from desktop.settings import save_settings
        save_settings({"DESKTOP_THEME": value})
        self.theme = value
        self._apply_theme()

    def set_language(self, value):
        if value not in ("zh", "en"):
            raise ValueError(value)
        if value == self.language:
            return
        from desktop.settings import save_settings
        save_settings({"DESKTOP_LANGUAGE": value})
        self.language = value
        self._apply_language()
        self.language_changed.emit()

    def _apply_language(self):
        set_language(self.language)
        self.app.removeTranslator(self.translator)
        if self.language == "zh" and self.translator.load("qtbase_zh_CN", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
            self.app.installTranslator(self.translator)

    def _system_changed(self, _scheme):
        if self.theme == "system":
            self._apply_theme()

    def _apply_theme(self):
        resolved = self.resolved_theme
        self.app.setPalette(qt_palette(PALETTES[resolved]))
        self.app.setStyleSheet(stylesheet(PALETTES[resolved]))
        for window in self.app.topLevelWidgets():
            native_titlebar(window, resolved == "dark")
        self.theme_changed.emit(resolved)
