---
name: pid-tuning
description: Tune and diagnose PID or explicitly defined feedback controllers using response logs, simulations, and bounded experiments. Use for overshoot, oscillation, slow tracking, steady-state error, PID convention conversion, and evidence-based gain proposals; not for generic LLM tuning.
metadata:
  version: "1.0.0"
---

# PID Tuning

Turn a control problem into an attributable experiment: understand the controller,
measure a baseline, propose a bounded change, evaluate it, and keep or roll it back.
Use the user's language. This skill runs independently of LLM-PID-Tuner; the host
agent supplies reasoning and tools. No additional model API or main application is required.

## Establish the experiment

Read available code, model settings, and logs before asking for missing facts.
Identify the plant, units, setpoint, sample time, current parameters, actuator limits,
controller equation and sign convention. Distinguish offline logs, resettable
simulation, and a live physical loop. Record user acceptance limits and a finite
experiment budget; do not silently invent a universal overshoot target.

Read [controller-semantics.md](references/controller-semantics.md) when mapping
PID conventions, discrete coefficients, custom expressions, or multiple loops.
Unknown parameter meaning is a reason to inspect or ask, not guess a numeric update.

## Measure before deciding

Read [telemetry.md](references/telemetry.md) for the CSV contract, segmentation,
metric definitions, and helper commands. Use `scripts/pid_lab.py analyze` to
summarize logs without third-party packages (Python 3.10+). `<skill-dir>` in commands
means this folder's actual location; resolve it before running commands. Store
generated output in the user's task workspace, not in the installed skill.

- Keep session, round, setpoint, parameter changes and time resets separate. Never
  average unlike experiments into one score.
- Associate each response with **confirmed applied** parameters. A suggested or
  rejected write is not the evaluated controller. Missing gains remain unknown.
- Ignore missing or placeholder control signals. A flat zero column does not prove
  actuator saturation. Check whether a finite observation window captures the dynamics.
- Compare like initial conditions, horizon, disturbances, units and sampling. Call
  model rollouts predictions; they cannot establish physical-plant performance.

## Decide, execute, verify

Read [experiment-loop.md](references/experiment-loop.md) for choosing experiments,
acceptance, rollback, and the compact experiment record.

1. Preserve working gains. Change the parameter with the strongest evidence first;
   use joint changes only when the actual equations or sensitivity justify them.
   Do not mechanically restart every loop with P-only control or copy one loop's gains
   into another. Avoid directions already measured to make things worse.
2. Express finite per-parameter bounds and **absolute** maximum changes for this
   experiment. `scripts/pid_lab.py check-update --file proposal.json` rejects invalid
   or out-of-budget proposals, including changes from zero. It never applies them.
3. If a usable model exists, screen a small candidate set against the same baseline.
   `simulate` provides a deterministic first-order demonstration, not a fitted model
   of the user's equipment. Use the user's actual simulator for plant-specific claims.
4. For hardware, use an existing authorized adapter only after confirming units,
   limits, live readback, and the rollback route. An analysis request does not authorize
   changing device gains or setpoints. Respect existing authorization; do not ask
   repeatedly. The bundled scripts never connect to equipment. Keep independent
   equipment protections in place; a prompt or gain check is not a safety interlock.
5. After an allowed change, confirm what was applied and measure it before crediting
   any improvement. Keep the last verified safe baseline. On material regression,
   restore it through the authorized adapter and verify readback and response; on a
   dangerous trend or unavailable telemetry, use the established stop procedure.
6. When acceptance limits are met, hold gains for the agreed verification windows
   instead of spending more calls on tuning. Reset the streak when conditions change
   or a limit fails. If no evaluation budget remains, do not apply another candidate.

## Deliver a usable result

Report the evaluated parameters and their exact convention, a compact baseline vs.
candidate metric comparison, data/model provenance, and the acceptance outcome.
Keep the current device state, best verified result, and untested proposal distinct.
State the missing evidence and next measurement when a conclusion is not supported.
Save a short experiment record so another agent can continue without replaying the
entire conversation. Do not claim convergence from an agent's `DONE` suggestion.

For a runnable offline example, use `assets/thermal-demo.json` with `simulate`.
For an existing LLM-PID-Tuner installation, analyze its exported CSV or use its
documented adapters; installing the application is optional, not a skill prerequisite.
