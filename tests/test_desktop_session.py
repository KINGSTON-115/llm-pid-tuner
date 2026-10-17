"""Exercise real sampling, cancellation, export and device cleanup without network access."""
import csv
import json
import time
from unittest.mock import Mock, patch

import pytest

from core.config import CONFIG, DEFAULT_CONFIG
from desktop.session import DesktopSession, RunOptions, SessionArchive
from sim.runtime import drain_event_queue


@pytest.fixture(autouse=True)
def isolated_config():
    with patch.dict(CONFIG, {**DEFAULT_CONFIG, "CSV_EXPORT_PATH": ""}, clear=True):
        yield


def test_offline_run_streams_each_sample_once_and_restores_config():
    before = dict(CONFIG)
    session = DesktopSession(RunOptions(rounds=2, samples=50, speed=50))
    with patch("desktop.session.LLMTuner", side_effect=AssertionError("Offline must never create an API client")):
        session.start()
        session.thread.join(5)
    assert not session.running
    events = drain_event_queue(session.events)
    samples = [e for e in events if e["type"] == "sample"]
    assert len(samples) == 100
    assert len({s["timestamp"] for s in samples}) == 100
    assert events.index(samples[0]) < next(i for i, e in enumerate(events) if e["type"] == "round_metrics")
    result = events[-1]["result"]
    assert result["completed_reason"] == "max_rounds_reached"
    assert result["provider"] == "offline"
    assert result["final_pid"]["p"] > 1
    assert CONFIG == before


def test_pause_setpoint_resume_stop_during_collection():
    session = DesktopSession(RunOptions(samples=5000, speed=50))
    session.start()
    try:
        deadline = time.monotonic() + 3
        samples = []
        while time.monotonic() < deadline and len(samples) < 5:
            samples += [e for e in drain_event_queue(session.events) if e["type"] == "sample"]
            time.sleep(.01)
        assert len(samples) >= 5
        session.controller.pause()
        time.sleep(.12)
        drain_event_queue(session.events)
        time.sleep(.12)
        assert not any(e["type"] == "sample" for e in drain_event_queue(session.events))
        session.controller.request_setpoint(150)
        session.controller.resume()
        resumed = []
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            resumed += drain_event_queue(session.events)
            if any(e["type"] == "sample" and e["setpoint"] == 150 for e in resumed):
                break
            time.sleep(.01)
    finally:
        session.controller.request_stop()
        session.thread.join(3)
    events = resumed + drain_event_queue(session.events)
    assert any(e["type"] == "sample" and e["setpoint"] == 150 for e in events)
    assert events[-1]["result"]["completed_reason"] == "stopped_by_user"
    assert not any(e["type"] == "decision" for e in events)


def test_connection_failure_releases_port_and_finishes():
    bridge = Mock()
    bridge.connect.return_value = False
    bridge.last_error = "Port unavailable"
    session = DesktopSession(RunOptions(mode="hardware", strategy="ai", port="COM404"))
    with patch("desktop.session.SerialBridge", return_value=bridge):
        session.start()
        session.thread.join(3)
    bridge.disconnect.assert_called_once()
    assert drain_event_queue(session.events)[-1]["result"]["completed_reason"] == "error"


def test_demo_hardware_uses_telemetry_and_does_not_write_initial_gains():
    session = DesktopSession(RunOptions(mode="hardware", strategy="ai", port="DEMO", samples=50, rounds=1))
    from hw.bridge import SerialBridge
    writes = []
    real_command = SerialBridge.send_profile_command

    def record(bridge, command, **kwargs):
        writes.append(command)
        return real_command(bridge, command, **kwargs)

    with patch("desktop.session.LLMTuner") as tuner, patch.object(SerialBridge, "send_profile_command", record):
        tuner.return_value.analyze.return_value = None
        session.start()
        session.thread.join(10)
        if session.running:
            session.controller.request_stop()
            session.thread.join(3)
    events = drain_event_queue(session.events)
    assert events[-1]["result"]["completed_reason"] == "max_rounds_reached"
    assert writes[0] == "STATUS"
    assert writes.count("SET") == 0  # One round measures only; no unverified final write.
    tuner.return_value.analyze.assert_not_called()
    assert len([e for e in events if e["type"] == "sample"]) == 50


def test_simulink_adapter_streams_model_data_and_disconnects():
    from sim.model import HeatingSimulator

    class FakeSimulink(HeatingSimulator):
        def __init__(self):
            super().__init__()
            self.connected = False
            self.disconnected = False
            self.batch = []

        def connect(self):
            self.connected = True

        def run_step(self):
            self.batch = []
            for _ in range(50):
                self.compute_pid()
                self.update()
                self.batch.append(super().get_data())

        def get_data(self):
            return self.batch

        def set_pid_pair(self, primary, secondary=None):
            self.set_pid(primary["p"], primary["i"], primary["d"])

        def disconnect(self):
            self.disconnected = True

    sim = FakeSimulink()
    with patch.dict(CONFIG, {"MATLAB_MODEL_PATH": "test-model.slx"}):
        session = DesktopSession(RunOptions(mode="simulink", strategy="ai", samples=50, rounds=1))
        with patch("desktop.session.create_simulink_bridge", return_value=sim), patch("desktop.session.LLMTuner") as tuner:
            tuner.return_value.analyze.return_value = None
            session.start()
            session.thread.join(3)
    events = drain_event_queue(session.events)
    assert sim.connected and sim.disconnected
    assert len([e for e in events if e["type"] == "sample"]) == 50
    assert events[-1]["result"]["completed_reason"] == "max_rounds_reached"


@pytest.mark.parametrize("options", [RunOptions(p=float("nan")), RunOptions(speed=0),
    RunOptions(samples=1), RunOptions(mode="hardware"), RunOptions(mode="hardware", strategy="ai", port="AUTO")])
def test_invalid_run_is_rejected_before_worker_start(options):
    with pytest.raises(ValueError):
        DesktopSession(options)


def test_archive_keeps_all_samples_and_exports_numeric_csv(tmp_path):
    archive = SessionArchive()
    try:
        for i in range(6100):
            archive.sample({"timestamp": i * 200, "input": i / 10, "setpoint": 200, "p": 1})
        archive.result = {"final_pid": {"p": 1, "i": .1, "d": .05}}
        archive.export_csv(str(tmp_path / "session.csv"))
        archive.export_json(str(tmp_path / "report.json"))
        with (tmp_path / "session.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 6100
        assert rows[-1]["timestamp"] == str(6099 * 200)
        assert json.loads((tmp_path / "report.json").read_text())["sample_count"] == 6100
    finally:
        archive.close()


def test_runtime_auth_failure_state_survives_session_cleanup():
    session = DesktopSession(RunOptions(strategy="ai", samples=50, rounds=2, speed=50))

    def analyze(*_args, **_kwargs):
        CONFIG["ORCAROUTER_AUTH_STATE"] = "needs_reauth"
        return None

    with patch("desktop.session.LLMTuner") as tuner:
        tuner.return_value.analyze.side_effect = analyze
        session.start()
        session.thread.join(3)
    assert not session.running
    assert CONFIG["ORCAROUTER_AUTH_STATE"] == "needs_reauth"
