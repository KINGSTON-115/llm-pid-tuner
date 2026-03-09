# LLM-PID-Tuner

An LLM-assisted PID tuning tool focused on reducing the painful trial-and-error process of real controller tuning.

[中文](README.md) | [English](README_EN.md)

[![Star History Chart](https://api.star-history.com/svg?repos=KINGSTON-115/llm-pid-tuner&type=Date)](https://star-history.com/#KINGSTON-115/llm-pid-tuner)

> 📺 [Video Tutorial (Bilibili)](https://b23.tv/WVUuIFb)
> 📺 [YouTube Tutorial](https://youtu.be/Giruc9kN53Y)

> New here? The easiest path is **not** Python.
> Download the packaged `llm-pid-tuner.exe` from Releases and start there.

## System Architecture

```text
┌──────────────────────── Local Simulation Mode ──────────────────────────┐
│                                                                        │
│  simulator.py  ───────────── API(JSON) ─────────────>  LLM / AI Tuner   │
│  (thermal model)                                        (PID decisions) │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘

┌──────────────────────── Real Hardware Mode ─────────────────────────────┐
│                                                                        │
│  MCU / firmware.cpp ── Serial(CSV) ──> tuner.py ── API ──> LLM         │
│  (Arduino / ESP32)                    (host app / exe)                  │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

## Best starting path

- **Just want to tune hardware**: use the packaged `exe`
- **Want to see the idea first**: run `simulator.py`
- **Want to integrate your own board**: use `firmware.cpp` + `tuner.py`
- **Want internals / maintenance notes**: read `PROJECT_DOC.md`

---

## 3-minute quick start for beginners

### 1. Download the packaged app

Latest Release:

`https://github.com/KINGSTON-115/llm-pid-tuner/releases/latest`

Download `llm-pid-tuner.exe` from the Assets section.

### 2. Prepare hardware

Your board should stream CSV data over serial.

The easiest route is to start from `firmware.cpp`, which already matches the protocol expected by this project.

Expected CSV format:

```text
timestamp_ms,setpoint,input,pwm,error,p,i,d
```

### 3. Run the exe once

Double-click `llm-pid-tuner.exe`.

If `config.json` does not exist, the program generates a default one automatically.

### 4. Edit `config.json`

At minimum, set these fields:

```json
{
  "SERIAL_PORT": "AUTO",
  "BAUD_RATE": 115200,
  "LLM_API_KEY": "sk-your-key",
  "LLM_API_BASE_URL": "https://api.openai.com/v1",
  "LLM_MODEL_NAME": "gpt-4",
  "LLM_PROVIDER": "openai"
}
```

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

## Key config fields

| Field | Meaning | Beginner advice |
| :--- | :--- | :--- |
| `SERIAL_PORT` | Serial port or `AUTO` | Start with `AUTO` |
| `BAUD_RATE` | Serial baud rate | Match your firmware |
| `LLM_API_KEY` | Model API key | Required |
| `LLM_API_BASE_URL` | API base URL | OpenAI-compatible endpoints usually end with `/v1` |
| `LLM_MODEL_NAME` | Model name | Example: `gpt-4`, `MiniMax-M2.5` |
| `LLM_PROVIDER` | Provider type | Use `openai` for OpenAI-compatible APIs, `openai_claude` for Claude relays, and `anthropic` for native Claude APIs |
| `BUFFER_SIZE` | Samples per tuning round | Keep the default first |
| `MAX_TUNING_ROUNDS` | Maximum rounds | Keep the default first |

Environment variables are also supported and override `config.json`, but beginners usually find `config.json` easier.

---

## Recommended providers

| Provider | `LLM_API_BASE_URL` example | `LLM_PROVIDER` |
| :--- | :--- | :--- |
| OpenAI | `https://api.openai.com/v1` | `openai` |
| MiniMax-compatible | provider `/v1` URL | `openai` |
| DeepSeek-compatible | provider `/v1` URL | `openai` |
| Claude relay / OneAPI / New API | provider `/v1` URL | `openai_claude` |
| Ollama | `http://localhost:11434/v1` | `openai` |
| LM Studio | `http://localhost:1234/v1` | `openai` |
| Anthropic Claude | `https://api.anthropic.com` | `anthropic` |

The current runtime is hardened for OpenAI-compatible endpoints and includes a more direct HTTP fallback path when SDK behavior is not enough.

---

## No hardware yet? Run the simulator first

```bash
pip install -r requirements.txt
python simulator.py
```

`simulator.py` models a heating system locally and lets the LLM tune it. This is the easiest way to understand the project before touching real hardware.

---

## Run from source

```bash
git clone https://github.com/KINGSTON-115/llm-pid-tuner.git
cd llm-pid-tuner
pip install -r requirements.txt
python tuner.py
```

Optional tools:

- `python simulator.py`
- `python system_id.py --file sample_step.csv`
- `python benchmark.py --cases baseline fallback llm --rounds 8`

---

## Main files

| File | Purpose |
| :--- | :--- |
| `tuner.py` | Main hardware tuning runtime and exe entry |
| `simulator.py` | Local thermal simulation |
| `pid_safety.py` | Guardrails, fallback logic, best-result tracking, rollback |
| `firmware.cpp` | Example MCU firmware |
| `system_id.py` | Step-response based system identification |
| `benchmark.py` | Fixed-seed comparison utility |
| `PROJECT_DOC.md` | Developer-oriented project notes |

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

`MIT`
