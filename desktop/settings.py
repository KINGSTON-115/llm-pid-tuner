"""Explicit, atomic configuration editing with unknown keys preserved."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLineEdit, QMessageBox, QPushButton,
    QTabWidget, QVBoxLayout, QWidget,
)

from core import config
from desktop.i18n import Bindings, t, runtime, TEXT
from desktop.widgets import label
from desktop.controls import QComboBox, QSpinBox


def warning(parent, title, message):
    box = QMessageBox(QMessageBox.Icon.Warning, title, message, parent=parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.addButton(t("ok"), QMessageBox.ButtonRole.AcceptRole)
    box.exec()
    box.deleteLater()


def save_settings(changes: dict, path: str | Path | None = None) -> None:
    target = Path(path or config.CONFIG_PATH).resolve()
    previous = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    if not isinstance(previous, dict):
        raise ValueError(t("invalid_config"))
    previous.update(changes)
    fd, temporary = tempfile.mkstemp(prefix=".pid-config-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(previous, handle, ensure_ascii=False, indent=4)
            handle.write("\n")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    config.CONFIG.update(changes)


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.copy = Bindings()
        self.copy.bind(self.setWindowTitle, "settings_title")
        self.resize(680, 580)
        self.fields: dict = {}
        self.original: dict = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)
        def text(key, name="", wrap=False):
            widget = label("", name, wrap)
            if key in TEXT:
                self.copy.bind(widget.setText, key)
            else:
                widget.setText(key)
            return widget

        layout.addWidget(text("connections", "title"))
        layout.addWidget(text("settings_hint", "muted", True))
        tabs = QTabWidget()
        layout.addWidget(tabs)

        def page(title):
            widget = QWidget()
            form = QFormLayout(widget)
            form.setContentsMargins(20, 22, 20, 22)
            form.setSpacing(16)
            index = tabs.addTab(widget, title)
            if title in TEXT:
                self.copy.bind(lambda caption, i=index: tabs.setTabText(i, caption), title)
            return form

        def field(form, title, key, choices=None, integer=None, password=False, browse=False):
            value = config.CONFIG.get(key, "")
            if choices:
                widget = QComboBox()
                for caption, data in choices:
                    widget.addItem(caption, data)
                    if caption in TEXT:
                        item = widget.count() - 1
                        self.copy.bind(lambda text, w=widget, i=item: self._item_text(w, i, text), caption)
                index = widget.findData(value)
                if index < 0:
                    widget.addItem(str(value), value)
                    index = widget.count() - 1
                widget.setCurrentIndex(index)
                self.original[key] = widget.currentData()
            elif integer:
                widget = QSpinBox()
                widget.setRange(*integer)
                widget.setValue(int(value))
                self.original[key] = widget.value()
            else:
                widget = QLineEdit(str(value))
                widget.setCursorPosition(0)
                if password:
                    widget.setEchoMode(QLineEdit.EchoMode.Password)
                self.original[key] = widget.text()
            if title in TEXT:
                self.copy.bind(widget.setAccessibleName, title)
            else:
                widget.setAccessibleName(title)
            self.fields[key] = widget
            if browse:
                row = QHBoxLayout()
                row.addWidget(widget)
                button = QPushButton()
                self.copy.bind(button.setText, "browse")
                button.clicked.connect(lambda: self._browse(widget))
                row.addWidget(button)
                form.addRow(text(title), row)
            else:
                form.addRow(text(title), widget)

        ai = page("ai_model")
        field(ai, "protocol", "LLM_PROVIDER", choices=[("openai", "openai"), ("anthropic", "anthropic"), ("openai_claude", "openai_claude"), ("auto", "auto")])
        field(ai, "url", "LLM_API_BASE_URL")
        field(ai, "model", "LLM_MODEL_NAME")
        field(ai, "API Key", "LLM_API_KEY", password=True)
        field(ai, "timeout", "LLM_REQUEST_TIMEOUT", integer=(5, 300))
        ai.addRow(text("provider_hint", "muted", True))
        self.orca_note = text("orca_hint", "muted", True)
        ai.addRow(self.orca_note)
        self.fields["LLM_PROVIDER"].currentIndexChanged.connect(self._provider_changed)
        self._provider_changed()
        hardware = page("hardware")
        field(hardware, "default_port", "SERIAL_PORT")
        field(hardware, "baud", "BAUD_RATE", integer=(1200, 4000000))
        field(hardware, "hw_protocol", "HARDWARE_PROFILE", choices=[("generic_csv", "generic_serial_csv"), ("STM32F407 + OpenMV", "stm32f407_openmv"), ("mspm0", "mspm0_datavision")])
        hardware.addRow(text("hw_settings_hint", "muted", True))
        matlab = page("Simulink")
        for title, key in [("model_file", "MATLAB_MODEL_PATH"), ("pid_block", "MATLAB_PID_BLOCK_PATH"),
                           ("output_signal", "MATLAB_OUTPUT_SIGNAL"), ("control_signal", "MATLAB_CONTROL_SIGNAL"),
                           ("sp_block", "MATLAB_SETPOINT_BLOCK"), ("matlab_root", "MATLAB_ROOT")]:
            field(matlab, title, key, browse=key == "MATLAB_MODEL_PATH")
        matlab.addRow(text("matlab_hint", "muted", True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.copy.bind(buttons.button(QDialogButtonBox.StandardButton.Save).setText, "save")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        self.copy.bind(buttons.button(QDialogButtonBox.StandardButton.Cancel).setText, "cancel")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if parent is not None and hasattr(parent, "preferences"):
            parent.preferences.language_changed.connect(self.retranslate)

    def retranslate(self):
        self.copy.refresh()

    @staticmethod
    def _item_text(widget, index, text):
        with QSignalBlocker(widget):
            widget.setItemText(index, text)

    def _browse(self, widget):
        path, _ = QFileDialog.getOpenFileName(self, t("choose_model"), "", "Simulink (*.slx *.mdl)", options=QFileDialog.Option.DontUseNativeDialog)
        if path:
            widget.setText(path)

    def _provider_changed(self):
        from llm.orcarouter import is_orcarouter_provider
        is_orca = is_orcarouter_provider(self.fields["LLM_PROVIDER"].currentData())
        self.orca_note.setVisible(is_orca)
        for key in ("LLM_API_BASE_URL", "LLM_API_KEY", "LLM_MODEL_NAME"):
            self.fields[key].setEnabled(not is_orca)

    def _save(self):
        values = {}
        for key, widget in self.fields.items():
            if isinstance(widget, QComboBox):
                values[key] = widget.currentData()
            elif isinstance(widget, QSpinBox):
                values[key] = widget.value()
            else:
                values[key] = widget.text().strip()
        from llm.orcarouter import is_orcarouter_provider
        if not is_orcarouter_provider(values["LLM_PROVIDER"]) and not values["LLM_API_BASE_URL"].startswith(("http://", "https://")):
            warning(self, t("check_settings"), t("invalid_url"))
            return
        try:
            save_settings({key: value for key, value in values.items() if value != self.original[key]})
        except (OSError, ValueError) as exc:
            warning(self, t("save_error"), runtime(str(exc)))
            return
        self.accept()
