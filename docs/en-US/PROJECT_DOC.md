`# LLM-PID-Tuner Project Notes

This document is for maintainers, contributors, and anyone who wants a quick understanding of the project internals.

If you just want to use the packaged build, start with [README.md](README.md).

> If you want the **latest development internals and newest tutorial entry points**, prefer the `dev` branch version of this document:  
> <https://github.com/KINGSTON-115/llm-pid-tuner/blob/dev/docs/en-US/PROJECT_DOC.md>

## 1. Project Goals

The core goal is not to produce impressive benchmark numbers. It is to:

- Use an LLM to reduce the trial-and-error cost of manual PID tuning
- Minimize "parameters spiraling out of control" situations on real hardware
- Stop tuning early once the system is usable, rather than over-optimizing
- Keep a fallback path working even when something goes wrong

In short, this project aims to be a **practical tuning assistant**, not an academic optimal control framework.

## 2. Main Components

### `tuner.py`

The main program for real hardware tuning.

Responsibilities:

- Read `config.json` and environment variables
- Scan and connect to the serial port
- Read CSV data reported by the MCU
- Compute current tuning metrics
- Call the LLM for new PID suggestions
- Apply PID guardrails, fallback logic, best-result tracking, and rollback logic

### `simulator.py`

A local thermal system simulator for validating tuning strategies without real hardware.

Responsibilities:

- Simulate the thermal inertia and heat dissipation of the plant
- Run the auto-tuning loop with logic similar to `tuner.py`
- Quickly verify whether a strategy is clearly going off the rails

### `pid_safety.py`

The centralized stability and fallback logic module.

Responsibilities:

- PID parameter guardrails
- Fallback suggestions when the LLM fails
- "Good enough" judgment
- Best-result tracking
- Rollback judgment when results deteriorate

### `firmware.cpp`

Example MCU-side firmware.

Responsibilities:

- Compute PID on a fixed time interval
- Output CSV data over serial
- Receive new PID parameters sent from the host

### `system_id.py`

A system identification utility.

Responsibilities:

- Read step-response data from serial or a CSV file
- Estimate first-order plus dead-time (FOPDT) model parameters
- Produce Ziegler-Nichols style initial PID suggestions

### `benchmark.py`

A fixed-seed development verification tool.

Responsibilities:

- Compare `baseline` / `fallback` / `llm` paths
- Observe strategy changes in a reasonably reproducible way

This is a development utility, not the core value of the project.

## 3. Real Hardware Workflow

The overall flow is:

1. MCU outputs process data using the `firmware.cpp` style protocol
2. `tuner.py` reads and buffers the data over serial
3. After accumulating a sampling window, compute error, overshoot, stability, and other metrics
4. Send the current summary and history to the LLM
5. LLM returns new PID suggestions
6. Suggestions pass through the guardrails and validation in `pid_safety.py`
7. The program sends the new PID back to the device and enters the next round
8. If results deteriorate, roll back to the best historical result
9. If results are already good enough, stop early

## 4. Configuration Sources and Priority

Runtime configuration comes from two places:

- `config.json`
- Environment variables

Priority order:

1. Default values
2. `config.json`
3. Environment variable overrides (highest priority)

For beginners, `config.json` is recommended. For automated scripts, environment variables are more convenient.

## 5. Serial Protocol Convention

The project expects the MCU to output one CSV line per cycle, with fields in this order:

```text
timestamp_ms,setpoint,input,pwm,error,p,i,d
```

Field descriptions:

- `timestamp_ms`: millisecond timestamp
- `setpoint`: target value
- `input`: current measured value
- `pwm`: control output
- `error`: current error
- `p/i/d`: PID parameters currently in use

If your hardware protocol differs, the recommended approach is to align your firmware with `firmware.cpp` rather than patching the host-side code.

## 6. Key Design Principles

### 6.1 Prefer usability over aggression

The goal is not to push parameters harder each round. Instead, the priority is:

- Gradually move the system toward a usable state
- Avoid obvious oscillation and degradation
- Preserve rollback headroom

### 6.2 The LLM is a tuning assistant, not the sole authority

LLM suggestions are valuable, but model output can be unstable. That is why the system includes:

- JSON sanitization and parse protection
- Provider / API compatibility handling
- Fallback logic
- Best-so-far tracking and memory-based rollback

### 6.3 "Good enough" matters more than chasing lower scores

In real control applications, the system often already meets the requirement. Continuing to minimize error can introduce more risk than benefit.

This is why early stopping and stable-round counting are built into the runtime.

## 7. Packaging Notes

The Windows executable is built with `PyInstaller`, with `launcher.py` as the entry point.

`launcher.py` dispatches the packaged build into two user-facing paths:

- `tuner.py` for real hardware tuning
- `simulator.py` for the local simulation / TUI

The current release strategy is designed for end users:

- Source code stays for developers
- The `exe` is for users who want to run directly

## 8. Recommended Maintenance Principles

When evolving the project, maintain these principles:

- Every piece of logic should have a clear purpose
- Fix root causes; do not pile on surface patches
- Do not sacrifice real-world usability for better benchmark scores
- Do not add heavy layers with low practical value
- Keep the real hardware tuning pipeline stable as the top priority

## 9. Entry Points for New Users

If you are handing this project to someone unfamiliar with Python, PID, or serial communication, direct them to:

- [README.md](README.md)
- [RELEASE_NOTES.md](RELEASE_NOTES.md)

Both documents are written in the order: download the exe first, then configure, then connect hardware.
