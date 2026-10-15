"""Explicit, atomic configuration editing with unknown keys preserved."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from core import config
from desktop.widgets import label


def save_settings(changes: dict, path: str | Path | None = None) -> None:
    target = Path(path or config.CONFIG_PATH).resolve()
    previous = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    if not isinstance(previous, dict):
        raise ValueError("配置文件必须是 JSON 对象。")
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
        self.setWindowTitle("连接设置 · LLM PID Tuner")
        self.resize(620, 520)
        self.fields: dict = {}
        self.original: dict = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)
        layout.addWidget(label("连接设置", "title"))
        layout.addWidget(label("配置保存在本机 config.json；API Key 以密码形式显示。", "muted"))
        tabs = QTabWidget()
        layout.addWidget(tabs)

        def page(title):
            widget = QWidget()
            form = QFormLayout(widget)
            form.setContentsMargins(20, 22, 20, 22)
            form.setSpacing(16)
            tabs.addTab(widget, title)
            return form

        def field(form, title, key, choices=None, integer=None, password=False, browse=False):
            value = config.CONFIG.get(key, "")
            if choices:
                widget = QComboBox()
                for caption, data in choices:
                    widget.addItem(caption, data)
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
            widget.setAccessibleName(title)
            self.fields[key] = widget
            if browse:
                row = QHBoxLayout()
                row.addWidget(widget)
                button = QPushButton("浏览…")
                button.clicked.connect(lambda: self._browse(widget))
                row.addWidget(button)
                form.addRow(title, row)
            else:
                form.addRow(title, widget)

        ai = page("AI 模型")
        field(ai, "接口协议", "LLM_PROVIDER", choices=[("OpenAI 兼容", "openai"), ("Claude 原生", "anthropic"), ("Claude 中转", "openai_claude"), ("自动识别", "auto")])
        field(ai, "API 地址", "LLM_API_BASE_URL")
        field(ai, "模型名称", "LLM_MODEL_NAME")
        field(ai, "API Key", "LLM_API_KEY", password=True)
        field(ai, "请求超时（秒）", "LLM_REQUEST_TIMEOUT", integer=(5, 300))
        ai.addRow(label("支持 OpenAI 兼容服务、DeepSeek、Ollama 等。\n离线仿真无需配置模型。", "muted", True))
        hardware = page("串口硬件")
        field(hardware, "默认串口", "SERIAL_PORT")
        field(hardware, "波特率", "BAUD_RATE", integer=(1200, 4000000))
        field(hardware, "硬件协议", "HARDWARE_PROFILE", choices=[("通用串口 CSV", "generic_serial_csv"), ("STM32F407 + OpenMV", "stm32f407_openmv"), ("MSPM0 DataVision（只读）", "mspm0_datavision")])
        hardware.addRow(label("连接时读取设备当前 PID。选择 AI 调参并开始后，\n引擎会根据采样结果向设备写入调整后的参数。", "muted", True))
        matlab = page("Simulink")
        for title, key in [("模型文件", "MATLAB_MODEL_PATH"), ("PID 模块路径", "MATLAB_PID_BLOCK_PATH"),
                           ("输出信号", "MATLAB_OUTPUT_SIGNAL"), ("控制信号", "MATLAB_CONTROL_SIGNAL"),
                           ("目标值模块", "MATLAB_SETPOINT_BLOCK"), ("MATLAB 安装路径", "MATLAB_ROOT")]:
            field(matlab, title, key, browse=key == "MATLAB_MODEL_PATH")
        matlab.addRow(label("需要本机 MATLAB Engine；双控制器等高级配置沿用 config.json。", "muted", True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存设置")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self, widget):
        path, _ = QFileDialog.getOpenFileName(self, "选择 Simulink 模型", "", "Simulink (*.slx *.mdl)")
        if path:
            widget.setText(path)

    def _save(self):
        values = {}
        for key, widget in self.fields.items():
            if isinstance(widget, QComboBox):
                values[key] = widget.currentData()
            elif isinstance(widget, QSpinBox):
                values[key] = widget.value()
            else:
                values[key] = widget.text().strip()
        if not values["LLM_API_BASE_URL"].startswith(("http://", "https://")):
            QMessageBox.warning(self, "检查设置", "API 地址需要以 http:// 或 https:// 开头。")
            return
        try:
            save_settings({key: value for key, value in values.items() if value != self.original[key]})
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return
        self.accept()
