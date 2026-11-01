# Offline tools and data contract

Python 3.10+ standard library only. Run from any working directory, using the actual
installed skill path. Commands below use `python`; use the project's designated
interpreter when it specifies one. No command connects to hardware or a model API.

```sh
python <skill-dir>/scripts/pid_lab.py analyze --csv response.csv
python <skill-dir>/scripts/pid_lab.py simulate --config <skill-dir>/assets/thermal-demo.json --csv demo.csv
python <skill-dir>/scripts/pid_lab.py check-update --file proposal.json
```

Reports are JSON on stdout. Errors go to stderr with a nonzero exit code. Simulation
refuses to overwrite an existing CSV. Redirect stdout to a report file when useful.
For other column names/units, create a normalized copy and preserve the source file.

## CSV

Required header: `timestamp_ms,setpoint,input`. Timestamp units are explicitly
milliseconds; input and setpoint share physical units. UTF-8 BOM is accepted.
Optional columns: `session_id,mode,round,pwm,p,i,d,p2,i2,d2`. Extra columns are ignored.
The original firmware CSV and LLM-PID-Tuner exports work without conversion.

- Exported `session_id`, `mode`, `round` and `setpoint` may be sparse; values carry
  forward, except a new explicit session or round requires a fresh setpoint.
- Gains must be present on each row to be attributable; incomplete gains remain
  unknown. Missing measurements, NaN/Infinity, duplicate headers, malformed rows,
  or duplicate timestamps within an experiment are errors, not silently dropped data.
- Split whenever session, round, mode, setpoint, known gains, or gain availability
  changes, or time goes backwards. Each segment reports its own metrics and warnings.
  A time reset is reported, not interpreted as extra elapsed time.
- Only trust a PWM column when its source is confirmed. Saturation fraction is
  calculated only with explicit `--control-limits MIN MAX`; otherwise it is `null`.
  A partial/missing control trace also gives `null`. These limits are measurement
  metadata, not an actuator write or an enforced interlock.

## Metrics

Each segment uses seconds and recomputes `error=setpoint-input`. MAE and IAE use
trapezoidal integration of absolute error over observed time. The tail is the final
20% of observed duration; tail MAE and signed bias are time-weighted. Tail peak-to-peak
is a useful noise/oscillation clue but is not a stability classifier.

Overshoot is excursion past the target in the direction from the **segment's first
output** to its target, divided by that amplitude. It handles negative, zero and
downward targets. If the trace starts mid-response, this is only window-relative
overshoot, not a full-step specification. With zero initial amplitude, overshoot,
rise and settling metrics are `null`. Do not compare that percentage against a
whole-step acceptance limit without the pre-step output.

Rise time is the sampled first 10% to first 90% crossing. Settling time is the first
observed sample after which all remaining samples stay in the band (default 2% of
initial amplitude, or `--settling-band` in output units). It is `null` if the last
sample is outside, or only the last sample is inside. Even a reported settling time
needs a sufficiently long subsequent observation. Samples are not interpolated for
crossing times. Short windows and a missing baseline cannot establish convergence.

Example with confirmed real control units:

```sh
python <skill-dir>/scripts/pid_lab.py analyze --csv response.csv --control-limits 0 255 --settling-band 0.5
```

## Bounded proposal file

```json
{
  "current": {"kp": 2.0, "ki": 0.0},
  "proposed": {"kp": 2.1, "ki": 0.01},
  "bounds": {
    "kp": {"min": 0.0, "max": 5.0, "max_step": 0.2},
    "ki": {"min": 0.0, "max": 0.5, "max_step": 0.02}
  }
}
```

All three maps must have exactly the same declared parameter names. Bounds, current
and proposed values must be finite JSON numbers; boolean values are rejected. Current
values must also be in bounds. A rejected proposal is never clipped into a silently
different experiment. Negative gains are supported when the declared limits allow
them. Bounds and step limits come from the actual controller/test plan, not the demo.

## Demonstration model

`thermal-demo.json` describes a normalized first-order plant:
`dy/dt = (ambient + gain*u - y)/tau`. The simulator integrates this plant exactly
for constant output over each sample interval. It uses parallel PID, seconds,
backward-difference derivative **on measurement**, conditional integral anti-windup,
and explicit output clamping. All state is reset between runs. CSV `pwm` is the
command held until the next sample. There is no noise, dead time, or fitted physics.
`duration_s / dt_s` must be an integer, with at most 100,000 intervals. A separate
configuration is needed for each gain candidate; keep all plant conditions fixed.
This model demonstrates the workflow and cannot validate a real heater or motor.
