# MATLAB/Simulink Simulation Tuning Guide

This guide explains how to use llm-pid-tuner to tune PID parameters on a MATLAB/Simulink simulation model with LLM assistance.

---

## When to use this mode

- You have a Simulink plant model and want a reasonable PID starting point quickly
- Your system is complex (nonlinear, multi-stage, time-delayed) and empirical rules like Ziegler-Nichols are hard to apply directly
- You want to converge parameters in simulation before moving to real hardware
- Your hardware has not arrived yet but you want to validate the tuning workflow in advance

## How it works

```text
Simulink model  ──run for N seconds──>  output data (To Workspace)
                                                │
                                    AdvancedDataBuffer computes metrics
                                                │
                                    LLM analyzes + suggests new PID
                                                │
                               writes back to Simulink PID Controller block
                                                │
                                    next round ──> converge
```

Guardrails, rollback, and best-result tracking work identically to hardware mode. The LLM cannot tell whether data came from simulation or a real device.

---

## Step 1: Install MATLAB Engine API for Python

This is a one-time setup. MATLAB R2021b and later ship this package. Run the following in your MATLAB installation directory:

```bash
cd <MATLAB_ROOT>/extern/engines/python
python setup.py install
```

Example path on Windows:
```
C:\Program Files\MATLAB\R2024a\extern\engines\python
```

Verify the install:
```bash
python -c "import matlab.engine; print('OK')"
```

---

## Step 2: Prepare your Simulink model

Your model needs two blocks — everything else can stay as-is:

**1. PID Controller block**

Use the standard Simulink PID Controller block. The tool writes P / I / D values directly via the block path you specify.

**2. To Workspace block**

Connect your plant output (the controlled variable) to a To Workspace block:
- **Variable name**: anything, e.g. `y_out` — you will reference this in config
- **Save format**: must be `Array`
- **Sample time**: match your model step size, or `-1` (inherited)

Save the model as `.slx` and note the full file path.

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

```bash
python simulator.py
```

When `MATLAB_MODEL_PATH` is set, `simulator.py` switches to MATLAB mode automatically — no extra flags needed. Leave it empty to run the built-in Python heating simulator instead.

---

## Troubleshooting

**`No module named matlab.engine`**

The MATLAB Engine API is not installed. Follow Step 1, and make sure you run `setup.py install` inside the same Python environment used for this project.

**`MATLAB connection failed`**

- Verify your MATLAB license is active
- Check `MATLAB_MODEL_PATH` for typos — use forward slashes `/` or double backslashes `\\`
- Check `MATLAB_PID_BLOCK_PATH` matches the exact block path in your model (case-sensitive)

**To Workspace data not found**

- Confirm the To Workspace block's Save format is `Array`, not `Structure` or `Timeseries`
- Confirm the variable name matches `MATLAB_OUTPUT_SIGNAL` exactly
- Run the model once manually in MATLAB and check that the variable appears in the workspace

**Each round is very slow**

`MATLAB_SIM_STEP_TIME` controls how long each simulation run lasts. Reducing it speeds up iteration, but make sure enough data points are collected per round (controlled by `BUFFER_SIZE`) to give the LLM a meaningful response curve to analyze.
