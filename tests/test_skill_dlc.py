"""Behavioral checks for the independently distributable PID skill."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/pid-tuning"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lab = load_module("pid_skill_lab", SKILL / "scripts/pid_lab.py")
builder = load_module("pid_skill_builder", ROOT / "scripts/build_skill_dlc.py")


def csv_file(tmp_path, text):
    path = tmp_path / "response.csv"
    path.write_text(text, encoding="utf-8-sig")
    return path


def trace(outputs, target, times=None):
    return [{"time_s": t, "setpoint": target, "input": y, "pwm": None}
            for t, y in zip(times or range(len(outputs)), outputs)]


@pytest.mark.parametrize("values,target", [([0, 5, 11, 10, 10], 10),
                                          ([10, 5, -1, 0, 0], 0),
                                          ([0, -5, -11, -10, -10], -10)])
def test_step_metrics_handle_both_directions_and_zero_target(values, target):
    result = lab.metrics(trace(values, target))
    assert result["iae"] == pytest.approx(11)
    assert result["mae"] == pytest.approx(2.75)
    assert result["overshoot_percent"] == pytest.approx(10)
    assert result["tail_mae"] == 0
    assert result["rise_time_s"] == 1
    assert result["settling_time_s"] == 3
    assert result["saturation_fraction"] is None


def test_metrics_weight_irregular_time_instead_of_sample_count():
    result = lab.metrics(trace([0, 0, 2], 2, [0, 1, 3]))
    assert result["iae"] == 4
    assert result["mae"] == pytest.approx(4 / 3)
    assert result["tail_mae"] == pytest.approx(.3)
    assert result["settling_time_s"] is None  # Final sample alone is not settling.


def test_zero_amplitude_and_short_window_do_not_claim_step_metrics():
    result = lab.metrics(trace([0, 0, 0], 0))
    assert result["mae"] == 0
    assert result["overshoot_percent"] is None
    assert result["settling_time_s"] is None
    assert lab.metrics(trace([0], 1)) is None


def test_saturation_requires_complete_signal_and_explicit_units():
    rows = trace([0, 1, 2], 2)
    for row, u in zip(rows, [0, 50, 100]):
        row["pwm"] = u
    assert lab.metrics(rows)["saturation_fraction"] is None
    assert lab.metrics(rows, [0, 100])["saturation_fraction"] == .5
    rows[1]["pwm"] = None
    assert lab.metrics(rows, [0, 100])["saturation_fraction"] is None


def test_sparse_application_exports_preserve_round_attribution(tmp_path):
    path = csv_file(tmp_path,
        "session_id,mode,round,timestamp_ms,setpoint,input,pwm,p,i,d,p2,i2,d2\n"
        "s1,hardware,1,0,10,0,20,1,.1,0,,,\n"
        ",,,1000,,5,20,1,.1,0,,,\n"
        "s1,hardware,2,0,10,0,20,2,.1,0,,,\n"
        ",,,1000,,8,20,2,.1,0,,,\n")
    segments = lab.analyze(path)["segments"]
    assert len(segments) == 2
    assert [s["round"] for s in segments] == ["1", "2"]
    assert [s["parameters"]["p"] for s in segments] == [1, 2]
    assert [s["metrics"]["mae"] for s in segments] == [7.5, 6]
    assert all(s["sample_count"] == 2 for s in segments)


@pytest.mark.parametrize("changed", ["setpoint", "p", "p2", "session_id", "mode", "round"])
def test_each_experiment_boundary_splits_data(tmp_path, changed):
    import csv
    path = tmp_path / "response.csv"
    first = dict(timestamp_ms=0, setpoint=10, input=0, p=1, i=0, d=0,
                 p2=1, i2=0, d2=0, session_id="s1", mode="hardware", round=1)
    second = {**first, "timestamp_ms": 1000}
    second[changed] = "s2" if changed == "session_id" else "simulation" if changed == "mode" else 2
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, first.keys())
        writer.writeheader()
        writer.writerows([first, second])
    assert len(lab.analyze(path)["segments"]) == 2


def test_time_reset_and_missing_gain_start_new_segments(tmp_path):
    path = csv_file(tmp_path, "timestamp_ms,setpoint,input,p,i,d\n"
                    "0,10,0,1,0,0\n1000,10,1,1,0,0\n"
                    "0,10,0,1,0,0\n1000,10,1,,,\n")
    segments = lab.analyze(path)["segments"]
    assert len(segments) == 3
    assert any("Time reset" in warning for warning in segments[1]["warnings"])
    assert segments[2]["parameters"]["p"] is None


@pytest.mark.parametrize("text", [
    "timestamp_ms,setpoint,input\n",
    "timestamp_ms,setpoint,input,input\n0,1,0,0\n",
    "timestamp_ms,setpoint,input\n0,1,nan\n",
    "timestamp_ms,setpoint,input\n0,1,inf\n",
    "timestamp_ms,setpoint,input\n0,,0\n",
    "timestamp_ms,setpoint,input\n0,1\n",
    "timestamp_ms,setpoint,input\n0,1,0,extra\n",
    "timestamp_ms,setpoint,input\n0,1,0\n0,1,1\n",
    "session_id,round,timestamp_ms,setpoint,input\ns,1,0,1,0\ns,2,1000,,1\n",
])
def test_invalid_csv_is_not_silently_scored(tmp_path, text):
    with pytest.raises(ValueError):
        lab.analyze(csv_file(tmp_path, text))


def proposal():
    return {"current": {"ki": 0}, "proposed": {"ki": .01},
            "bounds": {"ki": {"min": -.1, "max": .1, "max_step": .02}}}


def test_update_from_zero_is_bounded_without_assuming_gain_sign():
    spec = proposal()
    assert lab.check_update(spec) == {"valid": True, "proposed": {"ki": .01}, "applied": False}
    spec["proposed"]["ki"] = -.01
    assert lab.check_update(spec)["valid"]
    spec["proposed"]["ki"] = .03
    with pytest.raises(ValueError, match="max_step"):
        lab.check_update(spec)


@pytest.mark.parametrize("value", [True, None, "0.01", float("nan"), float("inf"), .2])
def test_invalid_proposals_are_rejected(value):
    spec = proposal()
    spec["proposed"]["ki"] = value
    with pytest.raises(ValueError):
        lab.check_update(spec)


def test_proposals_cannot_add_names_or_ignore_invalid_current_state():
    spec = proposal()
    spec["proposed"]["unknown"] = 0
    with pytest.raises(ValueError):
        lab.check_update(spec)
    spec = proposal()
    spec["current"]["ki"] = 1
    with pytest.raises(ValueError):
        lab.check_update(spec)
    spec = proposal()
    spec["bounds"]["ki"]["max_step"] = 0
    with pytest.raises(ValueError):
        lab.check_update(spec)


def test_demo_is_repeatable_and_obeys_physical_command_bounds():
    spec = lab.load_json(SKILL / "assets/thermal-demo.json")
    original = copy.deepcopy(spec)
    baseline = lab.simulate(spec)
    assert baseline == lab.simulate(spec)
    assert spec == original
    assert len(baseline) == 601
    assert baseline[0]["input"] == 20
    assert abs(baseline[-1]["input"] - 60) < .1
    assert all(0 <= row["pwm"] <= 100 for row in baseline)
    spec["controller"]["kp"] = 2
    assert baseline != lab.simulate(spec)


@pytest.mark.parametrize("key,value", [("dt_s", 0), ("duration_s", 1e9),
                                        ("duration_s", 1.1), ("output_limits", [100, 0])])
def test_demo_rejects_invalid_or_unbounded_experiments(key, value):
    spec = lab.load_json(SKILL / "assets/thermal-demo.json")
    spec["experiment"][key] = value
    with pytest.raises(ValueError):
        lab.simulate(spec)


def test_duplicate_json_keys_are_rejected(tmp_path):
    path = tmp_path / "proposal.json"
    path.write_text('{"current": {}, "current": {"kp": 1}}')
    with pytest.raises(ValueError, match="Duplicate JSON"):
        lab.load_json(path)


def test_distributed_package_runs_outside_repository_without_site_packages(tmp_path):
    artifacts = builder.build(tmp_path / "build")
    second = builder.build(tmp_path / "rebuild")
    assert artifacts[0].read_bytes() == second[0].read_bytes() == artifacts[1].read_bytes()
    digest = hashlib.sha256(artifacts[0].read_bytes()).hexdigest()
    assert digest in artifacts[2].read_text()
    standalone = tmp_path / "unpacked"
    with zipfile.ZipFile(artifacts[0]) as archive:
        manifest = json.loads((ROOT / "dlc/pid-tuning.json").read_text())
        assert set(archive.namelist()) == {"pid-tuning/" + path for path in manifest["files"]}
        archive.extractall(standalone)
    extracted = standalone / "pid-tuning"
    command = [sys.executable, "-I", "-S", str(extracted / "scripts/pid_lab.py")]
    csv_path = standalone / "demo.csv"
    sim_args = ["simulate", "--config", str(extracted / "assets/thermal-demo.json"), "--csv", str(csv_path)]
    result = subprocess.run([*command, *sim_args], cwd=standalone, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["source"] == "simulation"
    assert report["segments"][0]["metrics"]["tail_mae"] < .1
    before = csv_path.read_bytes()
    repeat = subprocess.run([*command, *sim_args], cwd=standalone, capture_output=True, text=True)
    assert repeat.returncode != 0
    assert csv_path.read_bytes() == before
    analysis = subprocess.run([*command, "analyze", "--csv", str(csv_path)], cwd=standalone, capture_output=True, text=True)
    assert analysis.returncode == 0, analysis.stderr
    assert json.loads(analysis.stdout)["segments"][0]["sample_count"] == 601
    file = standalone / "proposal.json"
    file.write_text(json.dumps(proposal()))
    checked = subprocess.run([*command, "check-update", "--file", str(file)], cwd=standalone, capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout)["applied"] is False
    file.write_text('{"current": {}}')
    rejected = subprocess.run([*command, "check-update", "--file", str(file)], cwd=standalone, capture_output=True, text=True)
    assert rejected.returncode == 2 and not rejected.stdout


def test_builder_rejects_files_outside_skill(tmp_path):
    skill = tmp_path / "skills/pid-tuning"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text((SKILL / "SKILL.md").read_text())
    (skill / "LICENSE").write_text("MIT")
    manifest = json.loads((ROOT / "dlc/pid-tuning.json").read_text())
    manifest["files"] = ["SKILL.md", "LICENSE", "../../config.json"]
    (tmp_path / "dlc").mkdir()
    (tmp_path / "dlc/pid-tuning.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Unsafe manifest path"):
        builder.build(tmp_path / "build", root=tmp_path)
