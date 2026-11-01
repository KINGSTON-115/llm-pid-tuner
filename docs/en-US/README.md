# LLM-PID-Tuner

Use an LLM to help tune PID controllers: propose gains from response data, measure
the result, retain improvements, and roll back regressions. For temperature, motor,
and other systems with an existing PID loop.

**[Download desktop v2.5.0](https://github.com/KINGSTON-115/llm-pid-tuner/releases/tag/v2.5.0)** · **[Download Agent Skill DLC v1.0.0](https://github.com/KINGSTON-115/llm-pid-tuner/releases/tag/skill-pid-tuning-v1.0.0)** · [中文](../../README.md)

## Star History

<a href="https://www.star-history.com/?repos=KINGSTON-115%2Fllm-pid-tuner&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=KINGSTON-115/llm-pid-tuner&type=date&theme=dark&legend=top-left&sealed_token=-jtUW5cYPUGqtvzDDldf5jRjkZ9FU85x0zaizpza3zjIAlZIoIUNaY6zI2Y-P_qec8vzQV1SHDj4IrEjoEy5AXn45k_NshJEaS54lWWwWs0cH82DHkARXQ" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=KINGSTON-115/llm-pid-tuner&type=date&legend=top-left&sealed_token=-jtUW5cYPUGqtvzDDldf5jRjkZ9FU85x0zaizpza3zjIAlZIoIUNaY6zI2Y-P_qec8vzQV1SHDj4IrEjoEy5AXn45k_NshJEaS54lWWwWs0cH82DHkARXQ" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=KINGSTON-115/llm-pid-tuner&type=date&legend=top-left&sealed_token=-jtUW5cYPUGqtvzDDldf5jRjkZ9FU85x0zaizpza3zjIAlZIoIUNaY6zI2Y-P_qec8vzQV1SHDj4IrEjoEy5AXn45k_NshJEaS54lWWwWs0cH82DHkARXQ" />
 </picture>
</a>

## Choose a starting point

| What you want to do | Start here |
| --- | --- |
| Try tuning without hardware or an API key | [Windows desktop](#desktop-quick-start) with offline rules |
| Use your existing agent to analyze data and plan experiments | [PID Tuning Skill DLC](#agent-skill-dlc) |
| Connect an Arduino, ESP32, or another controller | [Hardware and API configuration](CONFIGURATION.md) |
| Tune an existing Simulink model | [MATLAB / Simulink guide](MATLAB_GUIDE.md) |
| Modify the code or follow development | [Run from source](#run-from-source) |

## Agent Skill DLC

**Bring the tuning workflow to your existing agent.** The standalone `pid-tuning/`
folder covers controller equations and units, evidence-based tuning, parameter bounds,
small experiments, verification, and rollback. It includes CSV analysis, offline
simulation, and proposal validation tools. No main application or additional model
API configuration is required; scripts use only Python 3.10+ and its standard library.

1. [Download the ZIP](https://github.com/KINGSTON-115/llm-pid-tuner/releases/download/skill-pid-tuning-v1.0.0/pid-tuning-skill-v1.0.0.zip) and extract `pid-tuning/`.
2. Copy the whole folder into your agent's configured skills directory and reload. Without automatic discovery, ask the agent to read its `SKILL.md` directly.
3. In hosts supporting `$skill-name`, try:

```text
Use $pid-tuning to analyze this temperature-control CSV, compare overshoot and tail error by round, and propose one small experiment.
```

[Installation and usage guide](SKILL_DLC.md) · [Skill source](https://github.com/KINGSTON-115/llm-pid-tuner/tree/skill-pid-tuning-v1.0.0/skills/pid-tuning)

The DLC is released independently. Its scripts perform offline calculations;
live equipment connections use your existing authorized tools.

## Desktop quick start

1. Download `llm-pid-tuner-v2.5.0-windows-x64.zip` from the [v2.5.0 release](https://github.com/KINGSTON-115/llm-pid-tuner/releases/tag/v2.5.0).
2. **Extract the entire folder to a writable location**, open `llm-pid-tuner-desktop.exe`, and keep `_internal` beside it.
3. Keep **Local thermal simulation / Offline rules** selected and start tuning. No Python installation, hardware, or API key is needed.

The desktop includes live plots, Chinese/English switching, light/dark themes, and
CSV/JSON export. For AI tuning, fill in the model API under **Connection settings**
and select **AI automatic tuning**. Select serial hardware or Simulink when connecting
a device or model. Copy your existing `config.json` into a new release folder to retain settings.

See the [desktop guide](DESKTOP_GUIDE.md) for details. The package also includes a terminal launcher:

```powershell
.\llm-pid-tuner.exe --tui       # Terminal mode selector
.\llm-pid-tuner.exe sim         # Simulation TUI
.\llm-pid-tuner.exe tune COM5   # Serial tuning
```

## Configuration and tuning

Use **Connection settings** in the desktop, or start from
[`config.example.json`](https://github.com/KINGSTON-115/llm-pid-tuner/blob/v2.5.0/config.example.json)
when running from source. AI mode supports OpenAI-compatible endpoints, the native
Claude API, OrcaRouter, and local services such as Ollama / LM Studio.
**Offline rules need no model service.**

The [configuration guide](CONFIGURATION.md) covers providers, hardware protocols,
proxies, CSV export, gain limits, and troubleshooting. Model preparation, block paths,
multiple controllers, and MATLAB Engine installation belong in the [Simulink guide](MATLAB_GUIDE.md).

Express preferences such as limiting overshoot or prioritizing response speed.
The application evaluates suggestions against response data and parameter bounds,
then holds gains for observation when configured acceptance criteria are met.
Validate final gains under actual operating conditions before saving them in firmware
or controller configuration.

## Run from source

Use **Python 3.10+**; Simulink also requires a Python version supported by your installed
MATLAB Engine. `dev` is the default development branch. Use tag `v2.5.0` to reproduce
the desktop release; `main` receives user documentation but its code may lag release tags.

```bash
git clone -b dev https://github.com/KINGSTON-115/llm-pid-tuner.git
cd llm-pid-tuner
python -m pip install -r requirements.txt
python launcher.py
```

Other entry points:

```bash
python simulator.py              # Simulation TUI; configure a model API for AI tuning
python tuner.py                  # Serial tuning
python doctor.py                 # Configuration and connectivity checks
```

Use `python simulator.py --plain` for plain text output or `--lang en` for English.
See the [project documentation](PROJECT_DOC.md) for controller internals, the serial
protocol, and system identification.

## Documentation

| Guide | Contents |
| --- | --- |
| [Skill DLC](SKILL_DLC.md) | Installation, portability, offline examples, and independent releases |
| [Desktop workbench](DESKTOP_GUIDE.md) | UI, run controls, exports, and packaging |
| [Configuration](CONFIGURATION.md) | Providers, hardware, tuning settings, and troubleshooting |
| [MATLAB / Simulink](MATLAB_GUIDE.md) | Model setup, Engine installation, and multiple controllers |
| [Project internals](PROJECT_DOC.md) | Core modules, protocols, and tuning workflow |
| [Releases](https://github.com/KINGSTON-115/llm-pid-tuner/releases) | Downloads, changes, and previous versions |

Supervise real equipment and retain independent temperature limits, sensor fault
protection, and a physical power cutoff. The software's Stop action ends host-side
tuning; it is not an equipment emergency stop.

## Community and tutorials

QQ group: **1082281492** · [Bilibili tutorial](https://b23.tv/WVUuIFb) · [YouTube tutorial](https://youtu.be/Giruc9kN53Y)

[MIT License](../../LICENSE)
