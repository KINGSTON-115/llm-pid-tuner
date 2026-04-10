# MATLAB/Simulink Simulation Tuning Guide

This guide explains how to use llm-pid-tuner to tune MATLAB/Simulink models with AI assistance.

It focuses on three common scenarios:

1. Standard single PID/PI/PD/P controller blocks
2. Dual-controller / cascade setups
3. Split P/I/D gain blocks

If you only want to get your first run working, start with the standard single-controller setup. If your model is more complex, add the advanced fields step by step.

---

## Quick Start (download the exe, no Python required)

If you do not want to set up a Python environment, just download the pre-built exe:

1. Download the latest `llm-pid-tuner.exe` from the Releases page
2. Put `llm-pid-tuner.exe` and `config.json` in the same folder
3. Edit `config.json` and fill in your LLM API key plus the Simulink settings described below
4. Double-click `llm-pid-tuner.exe`, or run it from a terminal:

   ```bash
   llm-pid-tuner.exe
   ```

5. Choose "Simulink simulation tuning" from the menu
6. Press Enter when tuning finishes; the tuned values are saved back to the `.slx` file

> **Prerequisite:** MATLAB must already be installed and licensed on your machine. The packaged app does not replace a local MATLAB installation or license.

---

## When to use this mode

- You already have a Simulink plant model and want a usable parameter set quickly
- Your system is not a simple single loop and may use dual controllers, cascade control, or split gain blocks
- You want to converge parameters in simulation before moving to real hardware
- Your controller parameters are not named with the standard `P/I/D`, but use names such as `Kp/Ki/Kd` or `ProportionalGain/IntegralGain/DerivativeGain`
- You want the tool to adapt to an existing Simulink project instead of forcing you to rebuild the model structure

---

## How it works

```text
Simulink model  ──run one simulation round──>  output time series (To Workspace)
                                              │
                                  extract Time / Data arrays and compute
                                  error, overshoot, and steady-state error
                                              │
                                  LLM analyzes the current round + history
                                  and suggests new parameters
                                              │
                    write back one or more controller blocks
                    (or split P/I/D gain blocks)
                                              │
                                  continue to the next round ──> converge
```

Each round restarts the model from the initial state. The `SimTime(ms)` column is **simulation time in milliseconds**, not real-world time.

---

## Step 1: Install MATLAB Engine API for Python

This is a one-time setup. MATLAB R2021b and later include the package. Run the following inside your MATLAB installation:

```bash
cd <MATLAB_ROOT>/extern/engines/python
python setup.py install
```

Example Windows path:

```text
D:/Program Files/MATLAB/R2025b/extern/engines/python
```

Verify the install:

```bash
python -c "import matlab.engine; print('OK')"
```

---

## Step 2: Prepare your Simulink model

### One general trick: how to get any block path

`MATLAB_PID_BLOCK_PATH`, `MATLAB_PID_BLOCK_PATH_2`, `MATLAB_SETPOINT_BLOCK`, and `MATLAB_P/I/D_BLOCK_PATH(_2)` all expect a **full Simulink block path**.

Use the same workflow for every one of them:

1. Click the block you want in Simulink
2. In the MATLAB Command Window, run:

   ```matlab
   gcb
   ```

3. Copy the returned string directly into `config.json`

For example, if `gcb` returns:

```text
pid_tuner/Inner Loop/PID Controller
```

use that exact string as the block path.

If the block is inside a subsystem, the returned value already includes the hierarchy. You do not need to construct it manually.

### Required item 1: output signal

Connect the plant output you want to tune to a To Workspace block, then configure it like this:

1. Double-click the To Workspace block
2. Set **Variable name** to something like `y_out`
3. Set **Save format** to `Timeseries`
4. Set **Sample time** to `-1` (inherited) or the same step size as your model
5. Click OK

Manual verification:

```matlab
sim('your_model_name');
whos y_out
```

If your model uses inconsistent output variable names, you can later provide several candidates in `MATLAB_OUTPUT_SIGNAL_CANDIDATES`.

### Required item 2: controller parameters must be writable

The tool currently supports three common structures:

#### Option A: standard single controller block

This is the easiest and most reliable option. Typical examples:

- `PID Controller`
- `PI Controller`
- `PD Controller`
- `P Controller`

Supported parameter names include:

- `P` / `I` / `D`
- `Kp` / `Ki` / `Kd`
- `P_Gain` / `I_Gain` / `D_Gain`
- `ProportionalGain` / `IntegralGain` / `DerivativeGain`

#### Option B: dual controllers / cascade control

Typical examples:

- `Outer PID`
- `Inner PID`

The tool can write parameters to both controllers. Right now this support is best described as **minimum viable**, so explicit paths are recommended instead of relying entirely on auto-discovery.

#### Option C: split P/I/D gain blocks

If your controller is built from separate blocks instead of a single PID block, you can configure:

- one path for the P gain block
- one path for the I gain block
- one path for the D gain block

The tool will write compatible parameters such as `Gain`, `Value`, `K`, or `Coefficient`.

### Recommended: setpoint block

If the setpoint source is obvious in your model, record its block path and place it in `MATLAB_SETPOINT_BLOCK`.

This is usually more reliable than depending completely on auto-detection.

### Recommended: control output signal

If you can also send the controller output to To Workspace, for example `u_out`, put that variable name in `MATLAB_CONTROL_SIGNAL`.

That allows the tool to see the real controller output instead of a placeholder `PWM=0.0`, which gives the LLM better context.

---

## Step 3: Configure `config.json`

### Minimum single-controller configuration

```json
{
  "LLM_API_KEY": "your-key",
  "LLM_API_BASE_URL": "https://api.openai.com/v1",
  "LLM_MODEL_NAME": "gpt-4o",
  "LLM_PROVIDER": "openai",

  "MATLAB_MODEL_PATH": "C:/models/my_pid_model.slx",
  "MATLAB_PID_BLOCK_PATH": "my_pid_model/PID Controller",
  "MATLAB_ROOT": "D:/Program Files/MATLAB/R2025b",
  "MATLAB_OUTPUT_SIGNAL": "y_out",
  "MATLAB_SIM_STEP_TIME": 10.0,
  "MATLAB_SETPOINT": 200.0
}
```

### Field reference

| Field | Meaning | Example |
| :--- | :--- | :--- |
| `MATLAB_MODEL_PATH` | Full path to the Simulink `.slx` file | `C:/models/my_model.slx` |
| `MATLAB_PID_BLOCK_PATH` | Full path to the primary controller block | `my_model/PID Controller` |
| `MATLAB_ROOT` | MATLAB installation root directory | `D:/Program Files/MATLAB/R2025b` |
| `MATLAB_OUTPUT_SIGNAL` | To Workspace output variable name | `y_out` |
| `MATLAB_SIM_STEP_TIME` | Simulation time per tuning round, in simulation seconds | `10.0` |
| `MATLAB_SETPOINT` | Target setpoint value | `200.0` |

### Additional compatibility fields

| Field | When to use it | Meaning |
| :--- | :--- | :--- |
| `MATLAB_OUTPUT_SIGNAL_CANDIDATES` | Output variable names are inconsistent | Try several output names in order |
| `MATLAB_CONTROL_SIGNAL` | You want the LLM to see the real control output | For example `u_out`; if omitted, the bridge also tries common names like `u_out`, `u`, `pwm`, `control` |
| `MATLAB_SETPOINT_BLOCK` | Auto-detection of the setpoint source is unstable | Explicitly set the setpoint block |
| `MATLAB_PID_BLOCK_PATHS` | You have several likely controller paths | Provide a candidate path list |
| `MATLAB_PID_BLOCK_PATH_2` | Dual-controller / cascade model | Explicit path to the second controller |
| `MATLAB_P_BLOCK_PATH` `MATLAB_I_BLOCK_PATH` `MATLAB_D_BLOCK_PATH` | Controller uses split gain blocks instead of one PID block | Advanced compatibility mode for explicit split P/I/D gain block paths |
| `MATLAB_P_BLOCK_PATH_2` `MATLAB_I_BLOCK_PATH_2` `MATLAB_D_BLOCK_PATH_2` | Second split-gain controller | Advanced compatibility mode for dual-controller setups |

---

### How to choose these fields without getting confused

- Fields without a suffix belong to the first / primary controller; fields with `_2` belong to the second controller
- If you can edit the model, the most reliable setup is to tag the main PID block as `llm_pid_tuner_primary` and the secondary one as `llm_pid_tuner_secondary`
- If you already know the exact block path, prefer `MATLAB_PID_BLOCK_PATH`, `MATLAB_PID_BLOCK_PATH_2`, or `MATLAB_SETPOINT_BLOCK`
- If you are not sure which controller is the right one yet, but you can list a few candidates, use `MATLAB_PID_BLOCK_PATHS`
- Auto-discovery now prefers block Tags first, then standard `PID Controller` block types, and only then falls back to the older scoring heuristic
- `MATLAB_CONTROL_SIGNAL` and `MATLAB_SETPOINT_BLOCK` are recommended upgrades, not first-run requirements

---

## Recommended examples (grow from minimal to more capable)

### Example A: standard single PID Controller

This example starts from the minimum single-controller configuration and adds the recommended-but-optional `MATLAB_CONTROL_SIGNAL` and `MATLAB_SETPOINT_BLOCK`.

```json
{
  "MATLAB_MODEL_PATH": "C:/models/pid_tuner.slx",
  "MATLAB_PID_BLOCK_PATH": "pid_tuner/PID Controller",
  "MATLAB_ROOT": "D:/Program Files/MATLAB/R2025b",
  "MATLAB_OUTPUT_SIGNAL": "y_out",
  "MATLAB_CONTROL_SIGNAL": "u_out",
  "MATLAB_SETPOINT_BLOCK": "pid_tuner/Constant",
  "MATLAB_SIM_STEP_TIME": 15.0,
  "MATLAB_SETPOINT": 100.0
}
```

### Example B: dual controllers / cascade setup

```json
{
  "MATLAB_MODEL_PATH": "C:/models/cascade_pid.slx",
  "MATLAB_PID_BLOCK_PATH": "cascade_pid/Outer PID",
  "MATLAB_PID_BLOCK_PATH_2": "cascade_pid/Inner PID",
  "MATLAB_ROOT": "D:/Program Files/MATLAB/R2025b",
  "MATLAB_OUTPUT_SIGNAL": "y_out",
  "MATLAB_CONTROL_SIGNAL": "u_out",
  "MATLAB_SETPOINT_BLOCK": "cascade_pid/Setpoint",
  "MATLAB_SIM_STEP_TIME": 10.0,
  "MATLAB_SETPOINT": 100.0
}
```

### Example C: split P/I/D gain blocks

This is an advanced compatibility path. It still works, but it is no longer the recommended first-run configuration.

```json
{
  "MATLAB_MODEL_PATH": "C:/models/split_pid.slx",
  "MATLAB_P_BLOCK_PATH": "split_pid/P Gain",
  "MATLAB_I_BLOCK_PATH": "split_pid/I Gain",
  "MATLAB_D_BLOCK_PATH": "split_pid/D Gain",
  "MATLAB_ROOT": "D:/Program Files/MATLAB/R2025b",
  "MATLAB_OUTPUT_SIGNAL": "y_out",
  "MATLAB_CONTROL_SIGNAL": "u_out",
  "MATLAB_SETPOINT_BLOCK": "split_pid/Setpoint",
  "MATLAB_SIM_STEP_TIME": 10.0,
  "MATLAB_SETPOINT": 100.0
}
```

### Example D: start with candidate paths instead of one exact path

```json
{
  "MATLAB_MODEL_PATH": "C:/models/multi_loop.slx",
  "MATLAB_PID_BLOCK_PATHS": [
    "multi_loop/Outer PID",
    "multi_loop/Inner PID",
    "multi_loop/Backup Controller"
  ],
  "MATLAB_ROOT": "D:/Program Files/MATLAB/R2025b",
  "MATLAB_OUTPUT_SIGNAL": "y_out",
  "MATLAB_OUTPUT_SIGNAL_CANDIDATES": ["y_out", "yout", "plant_y"],
  "MATLAB_SIM_STEP_TIME": 10.0,
  "MATLAB_SETPOINT": 100.0
}
```

If Example D gets you running, the next recommended step is to replace the discovered primary controller with an explicit `MATLAB_PID_BLOCK_PATH`. That is usually more stable over time.

---

## What auto-discovery can do

The newer runtime prints a short discovery summary when the model starts, for example:

- primary controller path
- secondary controller path, if one is found
- setpoint block
- output signal
- control signal

This helps you:

1. confirm which controller block the runtime actually picked
2. separate "wrong path in config" problems from "model structure is not compatible" problems

> **Important:** auto-discovery is best-effort. For complex models, explicit primary controller, secondary controller, and setpoint paths are still the safest choice.

---

## Suggested rollout for complex configurations

If this is your first Simulink tuning setup, the safest path is:

1. Start with Example A and get a single controller working
2. Add `MATLAB_CONTROL_SIGNAL` so the LLM sees the real control output
3. If the model has multiple loops, add `MATLAB_PID_BLOCK_PATH_2`
4. Only switch to split `MATLAB_P/I/D_BLOCK_PATH` fields if your controller is not a standard PID block

Do not fill every advanced field on day one. The stable pattern is:

**start with the minimum workable config, then add compatibility fields one by one.**

---

## Step 4: Run

### Option A: via launcher (recommended)

```bash
llm-pid-tuner.exe
```

Choose "Simulink simulation tuning" from the menu. The tool will:
1. **Run environment diagnostics**: Automatically use `doctor.py` logic to check if MATLAB Engine is available and config fields are complete.
2. **Pre-Tuning Dialog** (interactive mode): Prompt you for natural language tuning preferences (e.g., "no overshoot allowed").
3. **Start tuning**: Launch MATLAB Engine, load the model, and begin tuning. 

When tuning finishes, press Enter to exit. The final parameters are saved back to the `.slx` file.

### Option B: call `simulator.py` directly

```bash
python simulator.py
```

When `MATLAB_MODEL_PATH` is set, `simulator.py` switches into Simulink mode automatically. No extra flags are required.
If you want to explicitly force the interface language (e.g., Chinese), use the `--lang` flag:
```bash
python simulator.py --lang zh
```

---

## Tuning strategy

The LLM still follows the same three-phase tuning sequence:

### Phase 1: tune P only

- Keep I near 0 and D = 0
- Increase P aggressively to find a useful gain region quickly

### Phase 2: add I to reduce steady-state error

- Only start increasing I after P is reasonably stable
- Increase I gradually until the steady-state error approaches zero
- If overshoot or oscillation appears, reduce I

### Phase 3: add D only if needed

- Only introduce D when the P+I combination still overshoots or oscillates noticeably
- Start with a small D
- If P+I already looks good, D can stay at 0

> **Note:** `SimTime(ms)` is simulation time in milliseconds, not real-world wall-clock time.

---

## Troubleshooting

### Q: `No module named matlab.engine`

The MATLAB Engine API is not installed yet. Follow Step 1, and make sure you run `setup.py install` inside the same Python environment used for this project.

### Q: `MATLAB connection failed` or startup timeout

- Verify that MATLAB is installed and licensed correctly
- Check `MATLAB_MODEL_PATH` for typos and prefer forward slashes `/` or escaped backslashes `\\`
- Check `MATLAB_ROOT` if you are using the packaged app or if source mode cannot find the runtime
- If you are using explicit block paths, re-select the block in Simulink and run `gcb` again to confirm the exact path
- The first MATLAB Engine startup can take 30 to 60 seconds, which is normal

### Q: To Workspace data is not found

- Confirm the To Workspace block uses **`Timeseries`**
- Confirm the variable name matches `MATLAB_OUTPUT_SIGNAL`
- If the output name is inconsistent, add `MATLAB_OUTPUT_SIGNAL_CANDIDATES`
- Run the model manually once in MATLAB and make sure the variable appears in the workspace

### Q: My model is not a single standard PID Controller. Can this still work?

Yes. In increasing order of complexity:

1. Standard controller block: fill `MATLAB_PID_BLOCK_PATH`
2. Dual-controller / multi-loop model: add `MATLAB_PID_BLOCK_PATH_2`
3. Split gain controller: use `MATLAB_P/I/D_BLOCK_PATH` and the `_2` variants when needed

If you are unsure which one applies, start with the explicit primary controller path and add complexity later.

### Q: Auto-discovery picked a strange block. What should I do?

Auto-discovery is only a fallback. For complex models:

- select the correct block in Simulink and run `gcb`
- fill `MATLAB_PID_BLOCK_PATH` explicitly
- fill `MATLAB_SETPOINT_BLOCK` explicitly
- if there is a second controller, also fill `MATLAB_PID_BLOCK_PATH_2`

That is the most stable setup.

### Q: Each round is slow

`MATLAB_SIM_STEP_TIME` controls how long each simulation round runs, in simulation seconds. Lowering it speeds up iteration, but each round still needs to be long enough to capture rise and settling behavior. A good starting point is roughly 3 to 5 times the system time constant.

### Q: Where are the tuned parameters saved?

When tuning finishes, or when you stop it manually, the current best parameters are written back into the `.slx` file. The next time you open the model in MATLAB, the controller blocks contain the tuned values.
