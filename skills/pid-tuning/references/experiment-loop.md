# Experiments and stopping

## Choose the next useful measurement

| Evidence | Investigate before proposing |
| --- | --- |
| Slow approach with available actuator headroom | Proportional sensitivity, plant lag/dead time, rate limits |
| Persistent offset | Integral action, bias, deadband; saturation may make the target unreachable |
| Overshoot with prolonged saturation | Windup, actuator limits and setpoint transition; do not reflexively increase I |
| Oscillation or growing peaks | Loop gain, delay, interaction; retain a safe baseline and reduce the implicated gain cautiously |
| High-frequency control chatter | Sensor noise, derivative filtering and sample timing before increasing D |
| New model forecast conflicts with observations | Model mismatch; shrink the experiment and investigate instead of trusting the forecast |

These are hypotheses, not a universal adjustment table. Infer direction from the
actual controller and evidence. Do not use aggressive ultimate-gain/relay tests on
equipment without a suitable authorized test plan. Open-loop identification needs
a known actuator step and measured plant response; a closed-loop setpoint step does
not by itself identify open-loop plant gain.

Keep a bounded set of local candidates, including the unchanged baseline. Use the
same initial state and disturbance seed. Discard candidates violating hard limits
before ranking performance. Do not compare a dimensioned sum of unrelated metrics
across plants; use user priorities or explicitly scaled costs. A bounded parameter
update can still destabilize a plant.

## Acceptance and rollback

Agree on measured limits such as tail absolute error, overshoot, settling band,
actuator occupancy and observation duration. A window ending inside a band is not
proof of long-term stability. Verify for multiple appropriate windows or repeatable
trials; choose the count and duration from the application, or declare a provisional
simulation-only default. The helper reports metrics, never a safety certification
or automatic `converged=true`.

Keep the best **verified acceptable** result, with its complete parameter set and
measurement conditions. Do not promote a prediction or an invalid/missing metric.
Zero error is valid data, not a missing-value fallback. Compare regressions with
both an absolute tolerance and relative degradation, so a zero-cost baseline works.
Store rejected parameter directions and reasons. If a write fails, stop that
experiment and report readback, not the requested values.

Stop on acceptance after verification, exhausted time/round budget, unavailable
required evidence, repeated lack of improvement, a user stop, or violated physical
limits. Report which occurred. Do not issue a last unevaluable update. Rolling back
also requires readback and observation before claiming the system was restored.

## Compact continuation record

Store one record per evaluated trial, for example:

```json
{
  "trial": "run-03",
  "source": "measured_hardware",
  "data_file": "run-03.csv",
  "controller_form": "parallel; time in seconds; derivative on measurement",
  "conditions": {"setpoint": 60, "sample_time_s": 0.2, "horizon_s": 120},
  "applied_parameters": {"kp": 2.0, "ki": 0.1, "kd": 0.0},
  "readback_confirmed": true,
  "metrics": {"tail_mae": 0.4, "overshoot_percent": 1.2},
  "acceptance": "needs_longer_observation",
  "decision": "hold",
  "reason": "Tail error improved; extend the observation window.",
  "best_verified_trial": "run-02",
  "next_proposal": null
}
```

For simulations use `source: simulation` and record model configuration/version and
reset state. For analysis of logs, confirm provenance with the user; CSV content is
not proof of hardware origin. Include missing evidence, limits, rejected directions,
remaining budget, and secondary-loop parameters when relevant. Keep full raw logs
on disk; send only relevant metrics and changes back into the agent context.
