# LLM-PID-Tuner

An LLM-assisted PID tuning tool focused on reducing the painful trial-and-error process of real controller tuning.

[中文](../../README.md) | English

[![Star History Chart](https://api.star-history.com/svg?repos=KINGSTON-115/llm-pid-tuner&type=Date)](https://star-history.com/#KINGSTON-115/llm-pid-tuner)

> 📺 [Video Tutorial (Bilibili)](https://b23.tv/WVUuIFb)
> 📺 [YouTube Tutorial](https://youtu.be/Giruc9kN53Y)

> New here? The easiest path is **not** Python.
> Download the packaged `llm-pid-tuner.exe` from Releases and start there.

## Table of Contents

- [System Architecture](#system-architecture)
- [Best starting path](#best-starting-path)
- [3-minute quick start for beginners](#3-minute-quick-start-for-beginners)
- [How to fill config.json](#how-to-fill-configjson)
- [How to customize prompts](#how-to-customize-prompts)
- [MATLAB/Simulink Simulation Mode](#matlabsimulink-simulation-mode)
- [If you don't have hardware yet, run the simulator](#if-you-dont-have-hardware-yet-run-the-simulator)
- [Advanced: Running from source](#advanced-running-from-source)

## System Architecture

```text
┌──────────────────────── Local Simulation Mode ──────────────────────────┐
│                                                                         │
│  simulator.py  ───────────── API(JSON) ─────────────>  LLM / AI Tuner   │
│  (thermal model)                                        (PID decisions) │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘

┌──────────────────────── Real Hardware Mode ─────────────────────────────┐
│                                                                         │
│  MCU / firmware.cpp ── Serial(CSV) ──> tuner.py ── API ──> LLM          │
│  (Arduino / ESP32)                    (host app / exe)                  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## Best starting path

- **Just want to tune hardware**: use the packaged `exe`
- **Want to see the idea first**: run `simulator.py`
- **Want to integrate your own board**: use `firmware.cpp` + `tuner.py`
- **Want internals**: read [PROJECT_DOC.md](PROJECT_DOC.md)

---

## 3-minute quick start for beginners

### 1. Download the packaged app

Open [Latest Release](https://github.com/KINGSTON-115/llm-pid-tuner/releases/latest) page and download `llm-pid-tuner.exe` from the Assets section.

### 2. Prepare hardware

Your board should stream CSV data over serial.

The easiest route is to start from `firmware.cpp`, which already matches the protocol expected by this project.

Expected CSV format:

```text
timestamp_ms,setpoint,input,pwm,error,p,i,d
```

### 3. Run the exe once

Double-click `llm-pid-tuner.exe`.

The launcher now asks which mode to start:
`[1]` hardware tuning or `[2]` the local simulation TUI (default).
If you are connecting a real serial device, choose `[1]`.

If `config.json` does not exist, the program generates a default one automatically.

### 4. Edit `config.json`

At minimum, start with the hardware-mode fields below:

```json
{
  "SERIAL_PORT": "AUTO",
  "BAUD_RATE": 115200,
  "LLM_API_KEY": "sk-your-key",
  "LLM_API_BASE_URL": "https://api.openai.com/v1",
  "LLM_MODEL_NAME": "gpt-4o",
  "LLM_PROVIDER": "openai"
}
```

If you only want to try `simulator.py` first and are not connecting real hardware yet, you can leave `SERIAL_PORT` unset and just fill the LLM fields.

For OpenAI-compatible providers such as MiniMax, DeepSeek, Ollama, or LM Studio, keep `LLM_PROVIDER` as `openai` and point `LLM_API_BASE_URL` to the provider's `/v1` endpoint.

Example:

```json
{
  "LLM_API_BASE_URL": "http://your-endpoint/v1",
  "LLM_MODEL_NAME": "MiniMax-M2.5",
  "LLM_PROVIDER": "openai"
}
```

If you use Claude through an OpenAI-compatible relay such as OneAPI or New API, you can now make that explicit:

```json
{
  "LLM_API_BASE_URL": "https://your-relay.example.com/v1",
  "LLM_MODEL_NAME": "claude-3-7-sonnet",
  "LLM_PROVIDER": "openai_claude"
}
```

Both `openai` and `openai_claude` use the OpenAI-compatible transport. `openai_claude` is just the clearer choice for Claude relays.

For native Claude APIs, use `LLM_PROVIDER: "anthropic"`.

### 5. Run it again

- If `SERIAL_PORT` is `AUTO`, the app scans ports and lets you choose
- If you already know the port, set something like `COM5`

### 6. Watch the tuning loop

The app will:

- collect serial data
- evaluate response quality
- ask the LLM for new PID values
- fall back to safer suggestions when needed
- remember the best stable result
- roll back when a later suggestion clearly makes things worse
- stop early when the system is already “good enough”

In practice, this means the tool tries to be useful on real hardware, not just aggressive.

---

## How to fill `config.json`

On the first run of `tuner.py`, `simulator.py`, or `llm-pid-tuner.exe`, the app generates a default `config.json` automatically if one does not exist.

If you prefer starting from a template, check `config.example.json`.

### Start with these fields

**1. Minimum config for real hardware**

```json
{
  "SERIAL_PORT": "AUTO",
  "BAUD_RATE": 115200,
  "LLM_API_KEY": "sk-your-key",
  "LLM_API_BASE_URL": "https://api.openai.com/v1",
  "LLM_MODEL_NAME": "gpt-4o",
  "LLM_PROVIDER": "openai"
}
```

**2. Minimum config for the built-in Python simulator**

```json
{
  "LLM_API_KEY": "sk-your-key",
  "LLM_API_BASE_URL": "https://api.openai.com/v1",
  "LLM_MODEL_NAME": "gpt-4o",
  "LLM_PROVIDER": "openai"
}
```

**3. Extra fields for Simulink mode**

```json
{
  "MATLAB_MODEL_PATH": "C:/models/my_pid_model.slx",
  "MATLAB_PID_BLOCK_PATH": "my_pid_model/PID Controller",
  "MATLAB_ROOT": "D:/Program Files/MATLAB/R2025b",
  "MATLAB_OUTPUT_SIGNAL": "y_out",
  "MATLAB_SIM_STEP_TIME": 15.0,
  "MATLAB_SETPOINT": 200.0
}
```

Those six fields are the **minimum setup** for a single-controller model.

If your Simulink model is not the simplest "single standard PID Controller block + one output signal" layout (e.g., dual-loop, split P/I/D gain blocks), the tool also supports advanced compatibility fields.

> **Please refer directly to the full [MATLAB/Simulink Tuning Guide](docs/en-US/MATLAB_GUIDE.md)**.
> The guide covers:
> - How to find exact Simulink block paths easily
> - How to configure dual controllers and split gain blocks
> - When `MATLAB_ROOT` is actually required
> - How to troubleshoot environment errors (e.g., `No module named matlab.engine`)

### Config groups by use case

| Group | When needed | Fields | Notes |
| :---- | :---------- | :----- | :---- |
| Serial hardware | Real hardware tuning | `SERIAL_PORT` `BAUD_RATE` | Start with `SERIAL_PORT: "AUTO"` and match the firmware baud rate |
| LLM basics | Required in every mode | `LLM_API_KEY` `LLM_API_BASE_URL` `LLM_MODEL_NAME` `LLM_PROVIDER` | This is the core set needed for any tuning run |
| Tuning behavior | Optional | `BUFFER_SIZE` `MIN_ERROR_THRESHOLD` `MAX_TUNING_ROUNDS` `LLM_REQUEST_TIMEOUT` `LLM_DEBUG_OUTPUT` | Leave defaults unless you are tuning strategy or debugging |
| Simulink | MATLAB/Simulink mode only | `MATLAB_MODEL_PATH` `MATLAB_PID_BLOCK_PATH` `MATLAB_ROOT` `MATLAB_OUTPUT_SIGNAL` `MATLAB_SIM_STEP_TIME` `MATLAB_SETPOINT`, plus optional `MATLAB_CONTROL_SIGNAL` `MATLAB_SETPOINT_BLOCK` `MATLAB_PID_BLOCK_PATHS` `MATLAB_PID_BLOCK_PATH_2` `MATLAB_P/I/D_BLOCK_PATH(_2)` | Start with the minimum six fields, then add compatibility fields only when needed |
| Proxy | Only if you need one | `HTTP_PROXY` `HTTPS_PROXY` `ALL_PROXY` `NO_PROXY` | Leave empty to disable |

### When should I fill `MATLAB_ROOT`?

See the [MATLAB/Simulink Tuning Guide](docs/en-US/MATLAB_GUIDE.md).

Environment variables are also supported and override `config.json`, but beginners usually find `config.json` easier.

### Proxy (optional)

If you need a VPN/proxy, add these fields to `config.json`:

```json
{
  "HTTP_PROXY": "http://127.0.0.1:7890",
  "HTTPS_PROXY": "http://127.0.0.1:7890",
  "ALL_PROXY": "http://127.0.0.1:7890",
  "NO_PROXY": ""
}
```

Leave them empty to **disable proxy**. If you do not need a proxy, you can ignore these fields.

### Export CSV Data (optional)

If you want to save real-time data during the tuning process (timestamp, setpoint, input, PWM, error, PID parameters, etc.) for later analysis in Excel or MATLAB, you can configure the `CSV_EXPORT_PATH` field in `config.json`:

```json
{
  "CSV_EXPORT_PATH": "logs/tuning_data.csv"
}
```

- Fill in the path where you want to save the CSV file (supports relative or absolute paths).
- Every time you run the tuner, the program will automatically append data to this file and record the current `session_id` and `round`, making it easy to distinguish between different tuning sessions.
- Leave it empty (`""`) to **disable CSV export**.

---

## Recommended providers

| Provider                        | `LLM_API_BASE_URL` example  | `LLM_PROVIDER`  |
| :------------------------------ | :-------------------------- | :-------------- |
| OpenAI                          | `https://api.openai.com/v1` | `openai`        |
| MiniMax-compatible              | provider `/v1` URL          | `openai`        |
| DeepSeek-compatible             | provider `/v1` URL          | `openai`        |
| Claude relay / OneAPI / New API | provider `/v1` URL          | `openai_claude` |
| Ollama                          | `http://localhost:11434/v1` | `openai`        |
| LM Studio                       | `http://localhost:1234/v1`  | `openai`        |
| Anthropic Claude                | `https://api.anthropic.com` | `anthropic`     |

The current runtime is hardened for OpenAI-compatible endpoints and includes a more direct HTTP fallback path when SDK behavior is not enough.

---

## How to customize prompts

This project provides two ways to customize prompts (tuning strategies) to suit different needs:

### 1. Pre-Tuning Conversation (Recommended, no coding required)
Every time you start tuning (whether in simulation or hardware mode), the program will first enter the **Pre-Tuning Conversation**.
Here, you can directly input your preferences in natural language. For example:
- *"I want the system to be absolutely stable with zero overshoot, even if the response is a bit slow."*
- *"This is a system that requires extremely fast response. Overshoot within 10% is acceptable. Please tune as aggressively as possible."*
- *"Note that this motor has a large deadband, so the P parameter can be larger."*

The program will automatically convert your natural language preferences into structured JSON constraints and append them to the LLM's system prompt, thereby changing the LLM's tuning behavior.

### 2. Modify the system prompt in source code (Advanced)
If you want to permanently change the core tuning logic or role setting of the LLM, you need to modify the source code:
1. Open the `llm/prompts.py` file.
2. Find the `_BASE_SYSTEM_PROMPT` variable, which defines the core role, responsibilities, and tuning principles of the LLM.
3. Find the `_MODE_NOTES` variable, which defines specific rules for different modes (e.g., `hardware`, `simulink`, `python_sim`).
4. Modify these string contents according to your needs.

---

## MATLAB/Simulink simulation mode

If you already have a MATLAB/Simulink model, you can tune its PID parameters directly with LLM assistance — no real hardware needed.

Set these fields in `config.json`, then run `python simulator.py` or choose the Simulink mode from the packaged launcher:

- `MATLAB_MODEL_PATH`: path to the Simulink `.slx` file
- `MATLAB_PID_BLOCK_PATH`: full path to the PID block inside the model
- `MATLAB_OUTPUT_SIGNAL`: To Workspace variable name
- `MATLAB_ROOT`: MATLAB install root; recommended for the packaged app, optional for source runs when `matlab.engine` already imports in your Python environment

For full setup instructions, model requirements, and troubleshooting, see the [MATLAB/Simulink Tuning Guide](docs/en-US/MATLAB_GUIDE.md).

---


## No hardware yet? Run the simulator first

```bash
pip install -r requirements.txt
python simulator.py
```

`simulator.py` models a simple local heating system and opens a lightweight Textual terminal dashboard by default when run in an interactive terminal.

Before the tuning loop starts, it now does three beginner-friendly things automatically:

- runs a quick doctor check for config, API reachability, serial ports, expected protocol fields, and proxy settings
- runs a short system-identification warm start so the initial PID is better than the fixed default values
- supports a **Pre-Tuning Conversation** (Interactive mode only), where you can describe tuning preferences and hard constraints in natural language (e.g., "overshoot must be less than 5%"), which the LLM will automatically enforce.

- Press `q` to quit the dashboard
- Press `p` to pause or resume the simulation loop
- Press `l` to toggle concise vs detailed event logs
- Press `r` to clear the event log and temporary summary

If you prefer the old plain log output, or want to explicitly set the language:

```bash
python simulator.py --plain
python simulator.py --lang zh  # Override language to Chinese. The system auto-detects English/Chinese by default.
```

If the current terminal is non-interactive, or if `textual` is not installed yet, the simulator falls back to plain console logs automatically.

If you want to run the startup checks manually without launching the simulator, use:

```bash
python doctor.py
```

---

## Run from source

If you want the latest behavior from source, prefer `dev` over `main`. New fixes, TUI changes, and pre-release validation land on `dev` first, while `main` is the slower-moving stable branch.

### Fresh clone: use `dev`

```bash
git clone -b dev https://github.com/KINGSTON-115/llm-pid-tuner.git
cd llm-pid-tuner
```

### Existing clone: switch to `dev` and update

```bash
git fetch origin
git checkout dev
git pull --ff-only origin dev
```

### Install dependencies

```bash
pip install -r requirements.txt
```

If you plan to run Simulink from source, make sure this same Python environment can import `matlab.engine`. The [MATLAB/Simulink Tuning Guide](docs/en-US/MATLAB_GUIDE.md) covers that setup.

### Before you run

- `python tuner.py`: fill LLM settings and serial settings
- `python simulator.py`: fill LLM settings
- Simulink mode: also fill the `MATLAB_*` fields

If `config.json` does not exist yet, the program will create a default one on first run. You can also start from `config.example.json`.

### Common commands

- `python tuner.py`

- `python simulator.py`
- `python simulator.py --plain`
- `python doctor.py`
- `python system_id.py --file sample_step.csv`
- `python benchmark.py --cases baseline fallback llm --rounds 8`

---

## Main files

| File                        | Purpose                                                    |
| :-------------------------- | :--------------------------------------------------------- |
| `launcher.py`               | Packaged exe entry that routes to hardware or simulation   |
| `tuner.py`                  | Main hardware tuning runtime                               |
| `simulator.py`              | Local thermal simulation                                   |
| `pid_safety.py`             | Guardrails, fallback logic, best-result tracking, rollback |
| `firmware.cpp`              | Example MCU firmware                                       |
| `system_id.py`              | Step-response based system identification                  |
| `doctor.py`                 | Environment check utility to debug config/API issues       |
| `benchmark.py`              | Fixed-seed comparison utility                              |
| `docs/en-US/PROJECT_DOC.md` | Developer-oriented project notes                           |

---

## FAQ

### The exe closes immediately

Common reasons:

- `config.json` is incomplete
- invalid API key
- serial port cannot be opened
- current directory is not writable

Run it from PowerShell to keep the error message visible:

```powershell
.\llm-pid-tuner.exe
```

### The app cannot find my serial port

Check:

- cable / driver
- whether another tool already opened the port
- whether your baud rate matches the firmware

### I get a connection but no useful data

Usually the board is not sending the expected CSV protocol. Start from `firmware.cpp` if possible.

### Can I use it fully offline?

Yes, if you run a local model server such as Ollama or LM Studio and expose an OpenAI-compatible endpoint.

---

## Safety

**Always supervise real hardware during tuning.**

Especially for heating systems, you still need hardware-level protection such as:

- over-temperature cutoff
- sensor failure handling
- power-stage fault protection
- physical power disconnect if necessary

This tool reduces tuning pain. It does not replace hardware safety design.

## License

`CC BY-NC-SA 4.0`
