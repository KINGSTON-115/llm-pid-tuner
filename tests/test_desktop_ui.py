import json
import os
from pathlib import Path
import time
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtGui import QFontDatabase
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit
import shiboken6

from core.config import CONFIG, DEFAULT_CONFIG
from desktop.app import Workbench
from desktop.settings import SettingsDialog, save_settings
from desktop.widgets import STYLE


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    instance.setStyle("Fusion")
    # The Windows offscreen plugin does not enumerate system fonts.
    if not QFontDatabase.families():
        for path in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/segoeui.ttf"):
            if Path(path).exists():
                QFontDatabase.addApplicationFont(path)
    instance.setStyleSheet(STYLE)
    yield instance
    instance.closeAllWindows()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    shiboken6.delete(instance)


def wait_for(app, condition, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return
        time.sleep(.01)
    assert condition(), "Timed out waiting for desktop state"


@pytest.fixture
def window(app):
    with patch.dict(CONFIG, {**DEFAULT_CONFIG, "CSV_EXPORT_PATH": ""}, clear=True):
        widget = Workbench()
        widget.show()
        app.processEvents()
        yield widget
        if widget.session:
            widget.stop()
            wait_for(app, lambda: widget.session is None)
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_real_run_reaches_ui_and_can_restart(app, window, tmp_path):
    window.samples.setValue(50)
    window.rounds.setValue(2)
    window.speed.setCurrentIndex(window.speed.findData(50))
    with patch("desktop.session.LLMTuner", side_effect=AssertionError("Unexpected network client")):
        window.start_button.click()
        assert not window.settings_button.isEnabled()
        wait_for(app, lambda: window.session is None)
        assert len(window.plot.samples) == 100
        assert window.table.rowCount() == 2
        assert window.metric_cards[0].value.text() != "—"
        assert window.start_button.isEnabled()
        assert window.export_button.isEnabled()
        window.archive.export_csv(str(tmp_path / "run.csv"))
        assert (tmp_path / "run.csv").stat().st_size > 100
        window.start_button.click()
        wait_for(app, lambda: window.session is None)
        assert window.archive.count == 100
        assert window.table.rowCount() == 2


def test_close_waits_for_worker_cleanup(app, window):
    window.samples.setValue(5000)
    window.start_button.click()
    worker = window.session
    wait_for(app, lambda: window.archive.count > 0)
    window.close()
    wait_for(app, lambda: not window.isVisible())
    assert not worker.running


def test_controls_remain_readable_and_modes_are_explicit(app, window):
    for spin in window.gains.values():
        assert spin.lineEdit().width() >= 40
    window.mode.setCurrentIndex(window.mode.findData("hardware"))
    assert window.port.isVisible()
    assert window.strategy.currentData() == "ai"
    assert not window.gains["p"].isEnabled()
    window.resize(1120, 760)
    app.processEvents()
    assert window.start_button.isVisible()
    assert window.stop_button.isVisible()
    window.history_nav.click()
    assert window.tabs.currentWidget() is window.table


def test_settings_mask_key_and_only_persist_edits(app, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"LLM_API_KEY": "saved-secret", "custom": {"keep": True}}))
    with patch.dict(CONFIG, {**DEFAULT_CONFIG, "LLM_API_KEY": "env-secret"}, clear=True):
        with patch("core.config.CONFIG_PATH", str(path)):
            dialog = SettingsDialog()
            assert dialog.fields["LLM_API_KEY"].echoMode() == QLineEdit.EchoMode.Password
            dialog.fields["LLM_MODEL_NAME"].setText("test-model")
            dialog._save()
            assert dialog.result() == QDialog.DialogCode.Accepted
    saved = json.loads(path.read_text())
    assert saved["LLM_API_KEY"] == "saved-secret"
    assert saved["custom"] == {"keep": True}
    assert saved["LLM_MODEL_NAME"] == "test-model"


def test_invalid_config_file_is_not_overwritten(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("invalid JSON")
    with pytest.raises(ValueError):
        save_settings({"LLM_MODEL_NAME": "new"}, path)
    assert path.read_text() == "invalid JSON"
