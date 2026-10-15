# Native desktop workbench (development version)

The workbench uses PySide6 / Qt Widgets and opens as a standalone window. No browser or Node.js is needed.
It shares the existing tuning engine, PID guardrails, rollback and convergence checks.

## Run

Use **Python 3.10+**:

```powershell
python -m pip install -r requirements-desktop.txt
python launcher.py
```

`python desktop_main.py` and `python launcher.py gui` also open the desktop UI.
On Windows, open `desktop_main.pyw` with the Python installation containing the dependencies for a console-free launch.
The current desktop UI is in Chinese; existing terminal language options remain available.

For a packaged installation, open `dist/llm-pid-tuner-desktop/llm-pid-tuner-desktop.exe`.
Distribute the entire folder, including `_internal`. The packaged desktop reads `config.json` next to the EXE;
source launches read it from the working directory. Existing published releases do not include this development feature.

## Use

The default **local thermal simulation / offline rules** runs without an API key or network requests.
Set initial PID gains and a setpoint, then select Start. The plots show actual simulated response,
setpoint and control output; hover to inspect values. The analysis panel explains each decision,
and round history shows errors, overshoot and stability. Advanced settings default to 12 rounds,
100 samples per round and 10× simulation speed. Speed changes pacing, not the simulation time step.

Use Connection Settings to configure the model API, serial protocol or Simulink paths.
Only edited fields are saved; unknown config keys are preserved. API keys are masked in the UI but
stored in the existing local JSON config, not an encrypted credential store. Environment overrides
are reapplied on the next launch. AI mode uses the project's existing provider clients.
Existing OrcaRouter credentials and model selections are reused. Configure login and catalog selection
through `python tuner.py --orcarouter-settings`; the desktop keeps those provider fields read-only.

- **Hardware:** select an explicit serial port, or `DEMO` for the virtual device. Initial PID and
  setpoint are read from telemetry; connecting does not overwrite them. AI tuning applies subsequent
  adjustments. The read-only MSPM0 DataVision profile is not supported for desktop tuning.
- **Simulink:** requires a local MATLAB Engine installation and a configured model/output signal.
  Advanced dual-controller and separate gain-block settings still come from `config.json`.
  Curves arrive per simulation step. See the [MATLAB guide](MATLAB_GUIDE.md).

Pause suspends sampling and parameter application. An in-flight model request may finish, but its
result waits for resume. Stop ends the tuning session and releases connections; it does not send
an emergency-stop command to physical hardware. In-flight network and MATLAB calls may need to
finish or time out. Closing the window waits for worker cleanup.

Export CSV for complete telemetry or JSON for round metrics and final gains. The plot retains up
to 6,000 samples; export includes all samples spooled to disk. Export before restarting or closing,
which discards the temporary session. Dual-controller telemetry is included in CSV.

Legacy entry points remain: `python launcher.py --tui`, `python launcher.py sim`,
`python launcher.py tune COM5`, and `python launcher.py --plain`.

## Build

```powershell
python -m pip install -r requirements-desktop.txt pyinstaller pytest
python -m pytest
python -m PyInstaller --noconfirm llm-pid-tuner-desktop.spec
```

The desktop bundle has no console window and does not include WebEngine. Automated checks use
offline simulation, the virtual serial device and test doubles. Real hardware and Simulink models
require validation in their target environments.
