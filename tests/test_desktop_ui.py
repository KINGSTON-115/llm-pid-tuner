import json
import gc
import importlib.util
import os
from pathlib import Path
import time
from unittest.mock import patch, PropertyMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
if importlib.util.find_spec("PySide6") is None:
    pytest.skip("Install requirements-desktop.txt for native UI tests", allow_module_level=True)
from PySide6.QtGui import QFontDatabase, QPalette
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QDialogButtonBox, QFileDialog, QStyle, QStyleOptionSpinBox
import shiboken6

from core.config import CONFIG, DEFAULT_CONFIG
from desktop.app import Workbench
from desktop.settings import SettingsDialog, save_settings
from desktop.widgets import STYLE
from desktop.i18n import t
from desktop.theme import PALETTES
from desktop.preferences import Preferences


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
    gc.collect()
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
def window(app, tmp_path):
    with patch.dict(CONFIG, {**DEFAULT_CONFIG, "CSV_EXPORT_PATH": "", "DESKTOP_LANGUAGE": "zh", "DESKTOP_THEME": "light"}, clear=True):
        with patch("core.config.CONFIG_PATH", str(tmp_path / "config.json")):
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
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
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


def test_existing_orcarouter_model_stays_read_only(app):
    with patch.dict(CONFIG, {"LLM_PROVIDER": "orcarouter_oauth", "LLM_MODEL_NAME": "orcarouter/auto"}):
        dialog = SettingsDialog()
        assert dialog.fields["LLM_PROVIDER"].currentData() == "orcarouter_oauth"
        assert not dialog.fields["LLM_MODEL_NAME"].isEnabled()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_live_theme_and_language_switch_preserves_paused_session(app, window, tmp_path):
    window.samples.setValue(5000)
    window.start_button.click()
    worker, archive, plot = window.session, window.archive, window.plot
    wait_for(app, lambda: archive.count > 3)
    window.pause_button.click()
    QTest.qWait(150)  # Let an in-flight sample and the event queue settle.
    count, points = archive.count, list(plot.samples)
    assert worker.controller.is_paused
    window.language_select.setCurrentIndex(window.language_select.findData("en"))
    window.theme_select.setCurrentIndex(window.theme_select.findData("dark"))
    QTest.qWait(150)
    assert window.session is worker and window.archive is archive and window.plot is plot
    assert archive.count == count and list(plot.samples) == points
    assert worker.controller.is_paused and window.pause_button.text() == "Resume"
    assert "Paused" in window.badge.text()
    assert window.start_button.text() == "Start tuning"
    assert window.mode.currentData() == "python" and window.strategy.currentData() == "offline"
    assert not window.mode.isEnabled() and not window.settings_button.isEnabled()
    assert window.plot.colors == PALETTES["dark"]
    assert app.palette().color(QPalette.ColorRole.Window).name() == PALETTES["dark"].background
    assert "Collecting data for round 1." in window.logs.toPlainText()
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved == {"DESKTOP_LANGUAGE": "en", "DESKTOP_THEME": "dark"}
    window.setpoint.setValue(140)
    window.apply_setpoint.click()
    window.pause_button.click()
    wait_for(app, lambda: any(s["setpoint"] == 140 for s in plot.samples))
    window.stop_button.click()
    wait_for(app, lambda: window.session is None)
    assert window.archive.result["completed_reason"] == "stopped_by_user"
    assert window.start_button.isEnabled()
    assert "Stopped" in window.badge.text()


def test_completed_history_and_offline_analysis_retranslate_without_data_loss(app, window, tmp_path):
    window.rounds.setValue(2)
    window.samples.setValue(50)
    window.speed.setCurrentIndex(window.speed.findData(50))
    window.start_button.click()
    wait_for(app, lambda: window.session is None)
    metrics = list(window.archive.metrics)
    pid, count = dict(window.archive.result["final_pid"]), window.archive.count
    window.range_select.setCurrentIndex(1)
    window.history_nav.click()
    window.preferences.set_language("en")
    assert window.table.horizontalHeaderItem(0).text() == "Round"
    assert window.table.item(0, 4).text() == "Slow response"
    assert "Offline rules" in window.analysis.toPlainText()
    assert "Final PID" in window.current_pid.text()
    assert window.tabs.currentWidget() is window.table and window.history_nav.isChecked()
    assert window.range_select.currentData() == 60 and window.plot.window_seconds == 60
    assert window.archive.count == count and window.archive.metrics == metrics
    assert window.archive.result["final_pid"] == pid
    for fmt in ("csv", "json"):
        path = tmp_path / ("export." + fmt)
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(path), "")) as choose:
            window.export(fmt)
        assert choose.call_args.args[1] == "Export this experiment"
        assert choose.call_args.kwargs["options"] == QFileDialog.Option.DontUseNativeDialog
        assert path.stat().st_size > 100
    window.preferences.set_language("zh")
    assert window.table.item(0, 4).text() == "响应偏慢"
    assert "离线规则" in window.analysis.toPlainText()


def test_settings_retranslate_keeps_edits_and_masks_secrets(app, window):
    dialog = SettingsDialog(window)
    try:
        dialog.fields["LLM_MODEL_NAME"].setText("my-unsaved-model")
        window.preferences.set_language("en")
        assert dialog.windowTitle() == "Connections · LLM PID Tuner"
        assert dialog.fields["LLM_API_BASE_URL"].accessibleName() == "API URL"
        assert dialog.fields["LLM_PROVIDER"].currentText() == "OpenAI compatible"
        assert dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).text() == "Save settings"
        assert dialog.fields["LLM_MODEL_NAME"].text() == "my-unsaved-model"
        assert dialog.fields["LLM_API_KEY"].echoMode() == QLineEdit.EchoMode.Password
        window.preferences.set_theme("dark")
        assert dialog.palette().color(QPalette.ColorRole.Window).name() == PALETTES["dark"].background
    finally:
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_closed_settings_disconnects_language_callback(app, window, capsys):
    dialog = SettingsDialog(window)
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    window.preferences.set_language("en")
    app.processEvents()
    assert "already deleted" not in capsys.readouterr().err


def test_preference_write_failure_rolls_back_ui_and_memory(app, window, tmp_path):
    original_style = app.styleSheet()
    with patch("desktop.settings.os.replace", side_effect=OSError("disk unavailable")):
        with patch("desktop.app.warning") as warn:
            window.theme_select.setCurrentIndex(window.theme_select.findData("dark"))
        assert warn.call_count == 1
    assert window.preferences.theme == "light" and window.theme_select.currentData() == "light"
    assert not (tmp_path / "config.json").exists()
    assert CONFIG["DESKTOP_THEME"] == "light" and app.styleSheet() == original_style
    assert not list(tmp_path.glob(".pid-config-*"))
    with patch("desktop.settings.save_settings", side_effect=ValueError("invalid JSON")):
        with patch("desktop.app.warning"):
            window.language_select.setCurrentIndex(window.language_select.findData("en"))
    assert window.preferences.language == "zh" and window.language_select.currentData() == "zh"
    assert window.start_button.text() == "开始调参"


def test_restart_honors_saved_preferences_and_cli_override(app, window):
    window.preferences.set_theme("dark")
    window.preferences.set_language("en")
    reloaded = Preferences()
    try:
        assert reloaded.theme == "dark" and reloaded.language == "en"
    finally:
        reloaded.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    overridden = Preferences(language="zh")
    try:
        assert overridden.language == "zh" and CONFIG["DESKTOP_LANGUAGE"] == "en"
    finally:
        overridden.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_system_theme_tracks_os_changes_only_while_selected(app, window):
    preferences = window.preferences
    with patch.object(Preferences, "resolved_theme", new_callable=PropertyMock, return_value="dark"):
        preferences.set_theme("system")
        assert window.plot.colors == PALETTES["dark"]
    with patch.object(Preferences, "resolved_theme", new_callable=PropertyMock, return_value="light"):
        preferences._system_changed(Qt.ColorScheme.Light)
        assert window.plot.colors == PALETTES["light"]
    preferences.set_theme("dark")
    with patch.object(preferences, "_apply_theme") as apply:
        preferences._system_changed(Qt.ColorScheme.Light)
        apply.assert_not_called()


def test_chart_rejects_corrupt_samples_and_bounds_memory(app, window):
    plot = window.plot
    sample = {"timestamp": 0, "input": 20, "setpoint": 100, "pwm": 25, "error": 80, "p": 1, "i": .1, "d": 0}
    for invalid in ({}, {**sample, "input": float("nan")}, {**sample, "timestamp": None},
                    {**sample, "pwm": "bad"}, {**sample, "input": 1e300}):
        assert not plot.add_sample(invalid)
        window._event({**invalid, "type": "sample"})
    assert len(plot.samples) == 0 and window.archive.count == 0
    window._event({**{k: str(v) for k, v in sample.items()}, "type": "sample"})
    assert window.metric_cards[0].value.text() == "20.00" and window.archive.count == 1
    for i in range(1, 6100):
        assert plot.add_sample({**sample, "timestamp": i * 1000})
    assert len(plot.samples) == 6000
    assert not plot.add_sample({**sample, "timestamp": 1})
    plot.window_seconds = 60
    assert len(plot.visible_samples()) == 61
    for theme in ("light", "dark"):
        plot.set_theme(theme)
        plot.hover_x = plot.width() / 2
        assert not plot.grab().isNull()
    plot.clear()
    assert not plot.samples and plot.hover_x is None
    assert not plot.grab().isNull()


def test_numeric_step_buttons_work_in_both_themes(app, window):
    spin = window.setpoint
    for theme in ("light", "dark"):
        window.preferences.set_theme(theme)
        option = QStyleOptionSpinBox()
        spin.initStyleOption(option)
        spin.setValue(200)
        for control, expected in ((QStyle.SubControl.SC_SpinBoxUp, 201), (QStyle.SubControl.SC_SpinBoxDown, 200)):
            rect = spin.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, control, spin)
            QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=rect.center())
            assert spin.value() == expected


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_compact_layout_and_controls_are_usable(app, window, language, theme):
    window.preferences.set_language(language)
    window.preferences.set_theme(theme)
    window.resize(1120, 760)
    window.advanced_button.click()
    app.processEvents()
    assert window.size().width() == 1120
    for spin in window.gains.values():
        assert spin.lineEdit().width() >= 40
    for widget in (window.start_button, window.pause_button, window.stop_button, window.theme_select, window.language_select):
        assert widget.isVisible()
        assert window.rect().contains(widget.mapTo(window, widget.rect().center()))
    assert not window.grab().isNull()
