#!/usr/bin/env python3
"""Portable offline PID measurements, demonstration and proposal validation.

Standard library only. No network, device writes, application imports or eval.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import sys


GAINS = ("p", "i", "d", "p2", "i2", "d2")


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def json_number(value, name):
    if not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a JSON number")
    return number(value, name)


def load_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique)


def exact_keys(value, keys, name):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{name} requires exactly: {', '.join(sorted(keys))}")


def read_segments(path):
    segments = []
    carried = {"session_id": "", "round": "", "mode": "", "setpoint": ""}
    previous_signature = None
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        if len(set(fields)) != len(fields):
            raise ValueError("Duplicate CSV header")
        if not {"timestamp_ms", "setpoint", "input"}.issubset(fields):
            raise ValueError("CSV requires timestamp_ms,setpoint,input headers")
        for row in reader:
            line = reader.line_num
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed CSV row at line {line}")
            row = {key: value.strip() for key, value in row.items()}
            new_session = bool(row.get("session_id") and row["session_id"] != carried["session_id"])
            new_round = bool(row.get("round") and row["round"] != carried["round"])
            if new_session:
                carried = {"session_id": "", "round": "", "mode": "", "setpoint": ""}
            elif new_round:
                carried["setpoint"] = ""
            for key in carried:
                if row.get(key):
                    carried[key] = row[key]
            sample = {
                "time_s": number(row["timestamp_ms"], f"timestamp at line {line}") / 1000.0,
                "setpoint": number(carried["setpoint"], f"setpoint at line {line}"),
                "input": number(row["input"], f"input at line {line}"),
                "pwm": number(row["pwm"], f"pwm at line {line}") if row.get("pwm") else None,
            }
            gains = {key: number(row[key], f"{key} at line {line}") if row.get(key) else None
                     for key in GAINS}
            context = {key: carried[key] for key in ("session_id", "round", "mode")}
            context["setpoint"] = sample["setpoint"]
            context["parameters"] = gains
            signature = (*[context[key] for key in ("session_id", "round", "mode", "setpoint")],
                         *gains.values())
            reset = bool(segments and sample["time_s"] < segments[-1]["samples"][-1]["time_s"])
            if signature != previous_signature or reset:
                segments.append({"context": context, "start_line": line, "time_reset": reset, "samples": []})
            elif sample["time_s"] == segments[-1]["samples"][-1]["time_s"]:
                raise ValueError(f"Duplicate timestamp within an experiment at line {line}")
            segments[-1]["samples"].append(sample)
            previous_signature = signature
    if not segments:
        raise ValueError("CSV has no samples")
    return segments


def integrate(times, values):
    return sum((b - a) * (left + right) / 2.0
               for a, b, left, right in zip(times, times[1:], values, values[1:]))


def tail_trace(times, values, start):
    for index in range(1, len(times)):
        if times[index] >= start:
            fraction = (start - times[index - 1]) / (times[index] - times[index - 1])
            value = values[index - 1] + fraction * (values[index] - values[index - 1])
            if times[index] == start:
                return times[index:], values[index:]
            return [start, *times[index:]], [value, *values[index:]]
    raise ValueError("Tail interval is empty")


def metrics(samples, control_limits=None, settling_band=None):
    if control_limits is not None:
        control_limits = [number(v, "control limit") for v in control_limits]
        if len(control_limits) != 2 or control_limits[0] >= control_limits[1]:
            raise ValueError("Control limits require MIN < MAX")
    if settling_band is not None:
        settling_band = number(settling_band, "settling band")
        if settling_band <= 0:
            raise ValueError("Settling band must be positive")
    if len(samples) < 2:
        return None
    times = [row["time_s"] - samples[0]["time_s"] for row in samples]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("Time must increase within a segment")
    duration = times[-1]
    output = [row["input"] for row in samples]
    target = samples[0]["setpoint"]
    error = [target - y for y in output]
    absolute = [abs(e) for e in error]
    iae = integrate(times, absolute)
    tail_times, tail_error = tail_trace(times, error, duration * 0.8)
    _, tail_absolute = tail_trace(times, absolute, duration * 0.8)
    tail_duration = tail_times[-1] - tail_times[0]
    amplitude = abs(target - output[0])
    direction = 1 if target > output[0] else -1
    overshoot = rise = settling = None
    band = settling_band if settling_band is not None else amplitude * 0.02
    if amplitude > 0:
        overshoot = max(0.0, max(direction * (y - target) for y in output)) / amplitude * 100
        progress = [direction * (y - output[0]) / amplitude for y in output]
        t10 = next((t for t, p in zip(times, progress) if p >= 0.1), None)
        t90 = next((t for t, p in zip(times, progress) if p >= 0.9), None)
        if t10 is not None and t90 is not None:
            rise = t90 - t10
        last_outside = max((i for i, e in enumerate(absolute) if e > band), default=-1)
        if last_outside + 1 < len(times) - 1:
            settling = times[last_outside + 1]
    control = [row["pwm"] for row in samples]
    saturation = None
    if control_limits is not None and all(u is not None for u in control):
        low, high = control_limits
        saturation = sum(b - a for a, b, u in zip(times, times[1:], control)
                         if u <= low or u >= high) / duration
    return {
        "duration_s": duration, "mae": iae / duration, "iae": iae,
        "tail_mae": integrate(tail_times, tail_absolute) / tail_duration,
        "tail_bias": integrate(tail_times, tail_error) / tail_duration,
        "tail_peak_to_peak": max(tail_error) - min(tail_error),
        "overshoot_percent": overshoot, "overshoot_basis": "initial_output_to_target",
        "rise_time_s": rise, "settling_time_s": settling, "settling_band": band,
        "saturation_fraction": saturation,
    }


def analyze(path, control_limits=None, settling_band=None):
    reports = []
    for index, segment in enumerate(read_segments(path), 1):
        samples, context = segment["samples"], segment["context"]
        warnings = ["Window metrics alone do not establish convergence or hardware safety."]
        if segment["time_reset"]:
            warnings.append("Time reset: a new segment was started.")
        if any(context["parameters"][key] is None for key in ("p", "i", "d")):
            warnings.append("Primary gains are missing or incomplete; parameter attribution is unknown.")
        if any(context["parameters"][key] is not None for key in ("p2", "i2", "d2")) and any(
                context["parameters"][key] is None for key in ("p2", "i2", "d2")):
            warnings.append("Secondary gains are incomplete.")
        if any(row["pwm"] is None for row in samples):
            warnings.append("Control signal is missing or incomplete.")
        if len(samples) < 2:
            warnings.append("Insufficient samples for time-based metrics.")
        elif samples[0]["input"] == samples[0]["setpoint"]:
            warnings.append("Zero initial step amplitude: overshoot, rise and settling are undefined.")
        reports.append({"segment": index, **context, "start_line": segment["start_line"],
                        "sample_count": len(samples), "start_time_s": samples[0]["time_s"],
                        "metrics": metrics(samples, control_limits, settling_band), "warnings": warnings})
    return {"source": "csv_observations", "segments": reports}


def check_update(spec):
    exact_keys(spec, ("current", "proposed", "bounds"), "Proposal")
    current, proposed, bounds = spec["current"], spec["proposed"], spec["bounds"]
    if not isinstance(current, dict) or not current:
        raise ValueError("current must declare at least one parameter")
    exact_keys(proposed, current, "proposed")
    exact_keys(bounds, current, "bounds")
    checked = {}
    for key in current:
        exact_keys(bounds[key], ("min", "max", "max_step"), f"bounds.{key}")
        low, high, step = (json_number(bounds[key][field], f"{key}.{field}")
                           for field in ("min", "max", "max_step"))
        old, new = json_number(current[key], key), json_number(proposed[key], key)
        if low > high or step < 0:
            raise ValueError(f"Invalid bounds for {key}")
        if not low <= old <= high or not low <= new <= high:
            raise ValueError(f"{key} is outside declared bounds")
        change = abs(new - old)
        if change > step and not math.isclose(change, step, rel_tol=1e-12, abs_tol=0.0):
            raise ValueError(f"{key} exceeds max_step")
        checked[key] = new
    return {"valid": True, "proposed": checked, "applied": False}


def simulate(spec):
    exact_keys(spec, ("plant", "controller", "experiment"), "Simulation")
    exact_keys(spec["plant"], ("gain", "tau_s", "ambient"), "plant")
    exact_keys(spec["controller"], ("kp", "ki", "kd"), "controller")
    exact_keys(spec["experiment"], ("initial_output", "setpoint", "dt_s", "duration_s", "output_limits"), "experiment")
    gain, tau, ambient = (json_number(spec["plant"][k], k) for k in ("gain", "tau_s", "ambient"))
    kp, ki, kd = (json_number(spec["controller"][k], k) for k in ("kp", "ki", "kd"))
    experiment = spec["experiment"]
    y, target, dt, duration = (json_number(experiment[k], k)
                               for k in ("initial_output", "setpoint", "dt_s", "duration_s"))
    limits = experiment["output_limits"]
    if not isinstance(limits, list) or len(limits) != 2:
        raise ValueError("output_limits requires [min, max]")
    low, high = [json_number(v, "output limit") for v in limits]
    if tau <= 0 or dt <= 0 or duration <= 0 or low >= high:
        raise ValueError("Require positive tau_s, dt_s, duration_s and min output < max output")
    count = duration / dt
    if not math.isfinite(count) or not 1 <= count <= 100000 or not math.isclose(count, round(count), rel_tol=0, abs_tol=1e-8):
        raise ValueError("duration_s/dt_s must be an integer from 1 to 100000")
    decay = math.exp(-dt / tau)
    previous, integral = y, 0.0
    rows = []
    for index in range(round(count) + 1):
        error = target - y
        increment = ki * error * dt
        candidate_integral = integral + increment
        p_and_d = kp * error - kd * (y - previous) / dt
        raw = number(p_and_d + candidate_integral, "simulated controller output")
        if not ((raw > high and increment > 0) or (raw < low and increment < 0)):
            integral = candidate_integral
        u = max(low, min(high, number(p_and_d + integral, "simulated controller output")))
        rows.append({"timestamp_ms": index * dt * 1000, "setpoint": target, "input": y,
                     "pwm": u, "error": error, "p": kp, "i": ki, "d": kd})
        previous = y
        equilibrium = ambient + gain * u
        y = number(equilibrium + (y - equilibrium) * decay, "simulated plant output")
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    analysis = sub.add_parser("analyze", help="Analyze CSV experiments separately")
    analysis.add_argument("--csv", type=Path, required=True)
    analysis.add_argument("--control-limits", type=float, nargs=2, metavar=("MIN", "MAX"))
    analysis.add_argument("--settling-band", type=float)
    simulation = sub.add_parser("simulate", help="Run an offline first-order demonstration")
    simulation.add_argument("--config", type=Path, required=True)
    simulation.add_argument("--csv", type=Path, required=True)
    update = sub.add_parser("check-update", help="Validate bounds without applying parameters")
    update.add_argument("--file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            result = analyze(args.csv, args.control_limits, args.settling_band)
        elif args.command == "check-update":
            result = check_update(load_json(args.file))
        else:
            spec = load_json(args.config)
            rows = simulate(spec)
            with args.csv.open("x", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            result = analyze(args.csv, spec["experiment"]["output_limits"])
            result["source"] = "simulation"
            result["model"] = spec
        # Also reject overflow in derived metrics; do not emit nonstandard JSON NaN/Infinity.
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError, TypeError, ArithmeticError, csv.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
