# Configuration and troubleshooting

[Back to the overview](README.md) · [中文](../zh-CN/CONFIGURATION.md)

This guide covers desktop v2.5.0 and subsequent `dev` versions. Run source from a
release tag or `dev`; older code on `main` may lack released features.
See the [DLC guide](SKILL_DLC.md) for Agent Skill installation and data requirements.

## Location and precedence

The desktop EXE reads `config.json` beside the executable; source runs use the current
working directory. Defaults are created on first run. Edit common fields in desktop
**Connection settings** and consult the [v2.5.0 template](https://github.com/KINGSTON-115/llm-pid-tuner/blob/v2.5.0/config.example.json)
for advanced fields. Environment variables override matching file settings on restart.
The file stores API credentials; remove keys before sharing configuration or logs.

Desktop **Offline rules** needs no model service. The following API fields apply to **AI tuning**.

## Model APIs

For an OpenAI-compatible service, enter its endpoint, an available model ID, and credentials:

```json
{
  "LLM_PROVIDER": "openai",
  "LLM_API_BASE_URL": "https://your-provider.example/v1",
  "LLM_MODEL_NAME": "your-model-id",
  "LLM_API_KEY": "your-api-key"
}
```

| Service | `LLM_PROVIDER` | `LLM_API_BASE_URL` |
| --- | --- | --- |
| OpenAI | `openai` | `https://api.openai.com/v1` |
| Compatible services such as DeepSeek / MiniMax | `openai` | Provider's compatible endpoint |
| Claude relay / OneAPI / New API | `openai_claude` or `openai` | Relay's compatible endpoint |
| Native Claude | `anthropic` | `https://api.anthropic.com` |
| Ollama | `openai` | `http://localhost:11434/v1` |
| LM Studio | `openai` | `http://localhost:1234/v1` |
| OrcaRouter | `orcarouter` / `orcarouter_oauth` | Use the settings entry below |

Choose model IDs from your service's actual catalog. Start Ollama / LM Studio before
connecting, and configure authentication as required by that local service.
Do not select native `anthropic` for a Claude relay that speaks the compatible protocol.

### OrcaRouter

For the Windows release, run:

```powershell
.\llm-pid-tuner.exe tune --orcarouter-settings
```

From source, use `python tuner.py --orcarouter-settings`; the TUI also opens settings
with `o`. Paste a key or sign in, then select a model from the catalog.
Saved changes apply to the next tuning session.

| Action | Source command |
| --- | --- |
| Use an existing key | `python tuner.py --orcarouter-key sk-orca-your-key` |
| Sign in through the browser | `python tuner.py --orcarouter-login` |
| Paste an authorization code without a browser | `python tuner.py --orcarouter-login --orcarouter-flow B` |
| Check credential status | `python tuner.py --orcarouter-status` |
| Clear local credentials | `python tuner.py --orcarouter-logout` |

For the packaged app, replace `python tuner.py` with `.\llm-pid-tuner.exe tune`.
An existing key selects `orcarouter`; a login-issued key selects `orcarouter_oauth`.
Credentials are stored in `config.json`, or supplied through `ORCAROUTER_API_KEY`.
Login uses PKCE: default Flow A receives a local callback; Flow B accepts a pasted code.
The issued credential is an account API key, billed to your account and revocable from
[authorized apps](https://www.orcarouter.ai/console/authorized-apps). Local logout does
not revoke it on the server. Check credentials and sign in again after `401` / `needs_reauth`.

Models come from `https://api.orcarouter.ai/v1/models`; settings indicate when discovery
falls back to the bundled catalog. For a self-hosted deployment, `ORCAROUTER_BASE_URL`
sets a shared base; `ORCAROUTER_AUTH_BASE_URL` and `ORCAROUTER_API_BASE_URL` take precedence
for authentication and inference respectively. Defaults are `https://www.orcarouter.ai`
for authentication and `https://api.orcarouter.ai/v1` for inference. Remote origins require HTTPS.

## Serial hardware

Choose a specific port in the desktop and match the firmware baud rate.
Source / TUI runs can use automatic port selection:

```json
{
  "SERIAL_PORT": "AUTO",
  "BAUD_RATE": 115200,
  "HARDWARE_PROFILE": "generic_serial_csv"
}
```

The default protocol follows [`firmware.cpp`](https://github.com/KINGSTON-115/llm-pid-tuner/blob/v2.5.0/firmware.cpp):

```text
timestamp_ms,setpoint,input,pwm,error,p,i,d
```

| `HARDWARE_PROFILE` | Support |
| --- | --- |
| `generic_serial_csv` | Default CSV firmware and demo mode |
| `stm32f407_openmv` | STM32F407 + OpenMV `Status:`, `T:x,y`, and `N` telemetry |
| `mspm0_datavision` | Read-only telemetry; no PID write-back or desktop tuning support |

Press `t` in the TUI to change the runtime setpoint. The default firmware path sends
`SETPOINT:<value>`; read-only adapters remain read-only. See [project internals](PROJECT_DOC.md)
for telemetry, parameter commands, and multiple-controller details.

## Tuning preferences and bounds

Interactive entry points with a pre-tuning dialog accept preferences such as “keep
overshoot below 5%” or “prioritize stability.” These guide model suggestions; configure
actual acceptance thresholds and parameter bounds for your plant as well.
Source prompts live in [`llm/prompts.py`](https://github.com/KINGSTON-115/llm-pid-tuner/blob/dev/llm/prompts.py):
`_BASE_SYSTEM_PROMPT` holds the shared strategy and `_MODE_NOTES` the mode-specific guidance.

| Setting | Purpose |
| --- | --- |
| `BUFFER_SIZE` / `MAX_TUNING_ROUNDS` | Samples per round / round budget |
| `LLM_REQUEST_TIMEOUT` / `LLM_DEBUG_OUTPUT` | Request timeout / diagnostic output |
| `PID_LIMITS` | Per-mode absolute P/I/D bounds and per-round increase ratios, enforced before write-back |
| `PID_MAX_INCREASE_RATIO` | Additional global increase cap; default `0` leaves this extra cap disabled |
| `GOOD_ENOUGH_AVG_ERROR` | Average error acceptance threshold |
| `GOOD_ENOUGH_STEADY_STATE_ERROR` | Steady-state error acceptance threshold |
| `GOOD_ENOUGH_OVERSHOOT` | Overshoot acceptance threshold |
| `REQUIRED_STABLE_ROUNDS` | Consecutive acceptable observation rounds; default `3` |

Once criteria are met, the app holds gains for observation and stops after enough
consecutive acceptable rounds. A regression resets the counter and resumes tuning.
Example gain limits are not universal equipment limits. Sampling timeouts and minimum
valid-sample counts have separate built-in protection and are not the settings above.

## Proxies and exports

Set `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY` only when needed; empty
values disable proxy configuration. For example, add to `config.json`:

```json
{
  "HTTP_PROXY": "http://127.0.0.1:7890",
  "HTTPS_PROXY": "http://127.0.0.1:7890",
  "CSV_EXPORT_PATH": "logs/tuning_data.csv"
}
```

`CSV_EXPORT_PATH` accepts a relative or absolute path and appends telemetry with
`session_id` and `round`. An empty string disables this export. The desktop also has
manual CSV/JSON export; see the [desktop guide](DESKTOP_GUIDE.md). Analyze exported
CSV with the [PID Tuning Skill](SKILL_DLC.md) when useful.

## Troubleshooting

| Symptom | First checks |
| --- | --- |
| EXE does not start | Extract the entire folder, retain `_internal`, and use a writable location; run the console launcher in PowerShell for errors |
| Serial port missing | Connection, drivers, competing applications, and firmware baud rate |
| Connected but no data | Telemetry format matches the selected `HARDWARE_PROFILE` |
| API chat works but tuning fails | Endpoint, model ID, protocol, and response format; use `python doctor.py` for connectivity checks |
| Missing `matlab.engine` or model block | Follow the [MATLAB guide](MATLAB_GUIDE.md) for the Python environment and exact block path |
| Response gets worse | Inspect round history and rollback results; confirm controller units, sample time, and acceptance criteria |
| Offline use | Select desktop offline rules; for AI, start a local model service such as Ollama / LM Studio |
