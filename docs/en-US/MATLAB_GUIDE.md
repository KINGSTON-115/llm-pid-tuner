# MATLAB/Simulink Simulation Tuning Guide

This guide explains how to use llm-pid-tuner to tune PID parameters on a MATLAB/Simulink simulation model with LLM assistance.

---

## Quick Start (download the exe, no Python required)

If you don't want to set up a Python environment, just download the pre-built exe:

1. Download the latest `llm-pid-tuner.exe` from the Releases page
2. Place `llm-pid-tuner.exe` and `config.json` in the same folder
3. Edit `config.json` — fill in your LLM API key and Simulink settings (see Step 3 below)
4. Double-click `llm-pid-tuner.exe`, or run it from a terminal:
   ```
   llm-pid-tuner.exe
   ```
5. Select "Simulink simulation tuning" from the menu — the tool starts MATLAB automatically and begins tuning
6. Press Enter to exit when tuning completes; results are saved back to the `.slx` file automatically

> **Prerequisite:** MATLAB must be installed and licensed on your machine. The exe bundles the MATLAB Engine connector but still requires a local MATLAB installation.

---

## When to use this mode

- You have a Simulink plant model and want a reasonable PID starting point quickly
- Your system is complex (nonlinear, multi-stage, time-delayed) and empirical rules like Ziegler-Nichols are hard to apply directly
- You want to converge parameters in simulation before moving to real hardware
- Your hardware has not arrived yet but you want to validate the tuning workflow in advance

## How it works

```text
Simulink model  ──synchronous sim() call──>  Timeseries output (To Workspace)
                                                      │
                                     extract Time / Data arrays, compute
                                     steady-state error, overshoot, rise time
                                                      │
                                     LLM analyzes current round + history,
                                     suggests new PID parameters
                                                      │
                                writes back to Simulink PID Controller block
                                                      │
                                          next round ──> converge
```

Each round restarts the simulation from the initial state. The time column in the data (`SimTime(ms)`) is **simulation time in milliseconds**, not real-world time.

---

## Step 1: Install MATLAB Engine API for Python

This is a one-time setup. MATLAB R2021b and later ship this package. Run the following in your MATLAB installation directory:

```bash
cd <MATLAB_ROOT>/extern/engines/python
python setup.py install
```

Example path on Windows (replace with your MATLAB version):
```
D:\Program Files\MATLAB\R2022b\extern\engines\python
```

Verify the install:
```bash
python -c "import matlab.engine; print('OK')"
```

---

## Step 2: Prepare your Simulink model

Your model needs two blocks — everything else can stay as-is:

**1. PID Controller block**

Use the standard Simulink PID Controller block. The tool writes P / I / D values directly via the block path you specify, and saves the final parameters back to the `.slx` file when tuning completes.

How to find the full block path:
- Click the PID Controller block in your Simulink model to select it
- In the MATLAB Command Window, type:
  ```matlab
  gcb
  ```
  The returned string is the full block path, e.g. `pid_tuner/PID Controller`
- Copy that string into the `MATLAB_PID_BLOCK_PATH` field in `config.json`

**2. To Workspace block**

Connect your plant output (the controlled variable) to a To Workspace block, then configure it as follows:

1. Double-click the To Workspace block to open its parameters
2. **Variable name**: enter a name, e.g. `y_out` (you will reference this in config)
3. **Save format**: select `Timeseries` from the dropdown — this is required; Array and Structure will not work
4. **Sample time**: use `-1` (inherited) or match your model step size
5. Click OK to save

After configuring, run the simulation once manually in MATLAB to verify:
```matlab
sim('your_model_name');
whos y_out   % should show y_out as a timeseries object
```

Save the model as `.slx` and note the full file path (use forward slashes, e.g. `C:/models/pid_tuner.slx`).

---

## Step 3: Configure `config.json`

Add these fields alongside your existing LLM configuration:

```json
{
  "LLM_API_KEY": "your-key",
  "LLM_API_BASE_URL": "https://api.openai.com/v1",
  "LLM_MODEL_NAME": "gpt-4",
  "LLM_PROVIDER": "openai",

  "MATLAB_MODEL_PATH"     : "C:/models/my_pid_model.slx",
  "MATLAB_PID_BLOCK_PATH" : "my_pid_model/PID Controller",
  "MATLAB_OUTPUT_SIGNAL"  : "y_out",
  "MATLAB_SIM_STEP_TIME"  : 10.0,
  "MATLAB_SETPOINT"       : 200.0
}
```

| Field | Description | Example |
| :--- | :--- | :--- |
| `MATLAB_MODEL_PATH` | Full path to the `.slx` file | `C:/models/my_model.slx` |
| `MATLAB_PID_BLOCK_PATH` | Full block path inside the model | `my_model/PID Controller` |
| `MATLAB_OUTPUT_SIGNAL` | To Workspace variable name | `y_out` |
| `MATLAB_SIM_STEP_TIME` | Simulation time per tuning round (sim seconds) | `10.0` |
| `MATLAB_SETPOINT` | Target value — must match the Setpoint in your model | `200.0` |

`MATLAB_PID_BLOCK_PATH` follows the format **model_name/block_name**. The model name is the `.slx` filename without the extension. If the PID block is inside a subsystem, write `my_model/Subsystem/PID Controller`.

---

## Step 4: Run

**Option A: via launcher (recommended)**

```bash
llm-pid-tuner.exe
```

Select "Simulink simulation tuning" from the menu. The tool will start the MATLAB Engine, load your model, and begin tuning automatically. When tuning completes, press Enter to exit — the final PID parameters are automatically saved back to the `.slx` file.

**Option B: direct invocation**

```bash
python simulator.py
```

When `MATLAB_MODEL_PATH` is set, `simulator.py` switches to MATLAB mode automatically — no extra flags needed. Leave it empty to run the built-in Python heating simulator instead.

---

## Tuning strategy

The LLM follows a standard three-phase tuning sequence consistent with engineering practice:

**Phase 1 — Tune P alone**
- Keep I near 0 and D = 0, adjust only P
- Increase P in large steps to quickly find a gain range where the response is fast and overshoot stays below 5%

**Phase 2 — Add I to eliminate steady-state error**
- Only after P is settled does the LLM start adjusting I
- Increase I gradually until steady-state error approaches zero
- If adding I causes overshoot or oscillation, I is too large — reduce it

**Phase 3 — Add D only if needed**
- D is introduced only when the P+I combination still shows significant overshoot or oscillation
- Start D small; too large a D causes over-damping and slows the response
- If P+I already meets the target, D stays at 0

> **Note:** The `SimTime(ms)` column in each round's data is simulation time in milliseconds, not real-world seconds. For example, with a 15-second simulation step, the data spans 0–15000 ms and rise time is evaluated in those units.

---

## Troubleshooting

**`No module named matlab.engine`**

The MATLAB Engine API is not installed. Follow Step 1, and make sure you run `setup.py install` inside the same Python environment used for this project.

**`MATLAB connection failed` or startup timeout**

- Verify your MATLAB license is active
- Check `MATLAB_MODEL_PATH` for typos — use forward slashes `/` or double backslashes `\\`
- Check `MATLAB_PID_BLOCK_PATH` matches the exact block path in your model (case-sensitive)
- The MATLAB Engine can take 30–60 seconds to start for the first time — this is normal

**To Workspace data not found**

- Confirm the To Workspace block's Save format is **`Timeseries`** (not Array or Structure)
- Confirm the variable name matches `MATLAB_OUTPUT_SIGNAL` exactly
- Run the model once manually in MATLAB and check that the variable appears in the workspace

**The LLM reports the response is "extremely slow" but it looks fine in simulation**

This typically means the LLM misread simulation milliseconds as real-world seconds. The data header is labelled `SimTime(ms)` and the prompt explicitly states the unit. If it recurs, try reducing `MATLAB_SIM_STEP_TIME` so the numbers are smaller and easier to interpret.

**Each round is very slow**

`MATLAB_SIM_STEP_TIME` controls how long each simulation run lasts (in simulation seconds). Reducing it speeds up iteration. Set it to roughly 3–5 times the system time constant so each round captures a complete rise and settling response.

**Where are the final PID parameters saved?**

When tuning finishes (or the user interrupts), the best PID found is written back into the `.slx` file. The next time you open the model in MATLAB, the PID Controller block will contain the tuned values.
