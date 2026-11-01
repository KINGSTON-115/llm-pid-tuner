# Side story: PID Tuning Skill DLC

**v1.0.0 · Bring the tuning workflow to your existing agent.**

[Download the standalone DLC](https://github.com/KINGSTON-115/llm-pid-tuner/releases/tag/skill-pid-tuning-v1.0.0) · [中文](../zh-CN/SKILL_DLC.md) · [Skill source](https://github.com/KINGSTON-115/llm-pid-tuner/tree/skill-pid-tuning-v1.0.0/skills/pid-tuning)

The main project provides a desktop workbench. This companion distills its reusable
method into one portable `pid-tuning/` folder: understand the controller, measure a
baseline, make a bounded change, verify it, and keep or roll back the result.
Your agent provides reasoning; bundled tools provide reproducible calculations.
No main application, additional model API key, or specific model provider is required.
Instructions work on their own; scripts require only Python 3.10+ and its standard library.

## What carries over

| Project capability | Portable form |
| --- | --- |
| Evidence-based tuning without blindly resetting I/D | Skill instructions and experiment records |
| Parameter bounds, step limits and rollback | Proposal checker plus verification/rollback workflow |
| Controller equations, discretization and multiple loops | References loaded when needed |
| Attributable measurements and separate predictions | Segmented CSV metrics with explicit provenance |
| Hold when acceptable; stop at the experiment budget | Reusable acceptance procedure |

Desktop/TUI, model clients, serial drivers, MATLAB Engine and PLC protocols remain
in the main application or your existing tools. This package has no machine-specific
paths, ports or universal gain limits. Its scripts perform offline computations;
live experiments require an existing authorized adapter and equipment protections.

## Install or move to another agent

1. Download `pid-tuning-skill-v1.0.0.zip` and extract `pid-tuning/`.
2. Copy the **whole folder** into your agent's configured skills directory, with
   `SKILL.md` directly inside it. For example, this project's Codex development
   environment uses `~/.codex/skills/`; consult your host configuration and the
   [official Codex documentation](https://developers.openai.com/codex/skills/) for
   the discovery locations supported by your installation.
3. Reload skills or start a new session and check discovery. Hosts supporting
   `$skill-name` invocation can use `$pid-tuning`.
4. For agents without automatic skill discovery, ask them to read
   `your-path/pid-tuning/SKILL.md` and its relevant references explicitly.

The `.skill` asset has the same contents as the ZIP, for hosts supporting that import
format; direct import is not guaranteed in every product. Preserve relative paths.
Other agents can ignore the optional `agents/openai.yaml` UI metadata. Preserve local
customizations before replacing an installation. SHA-256 checksums accompany the assets.

## Example requests

```text
Use $pid-tuning to analyze this temperature-control CSV. Confirm gain semantics and
data quality, compare overshoot and tail error by round, and propose one small
experiment without connecting to equipment.

Use $pid-tuning to inspect this Simulink controller's equation and sample time.
Evaluate candidate gains in my model and retain the best verified result.

Read ./pid-tuning/SKILL.md and demonstrate baseline, one gain change, and verification
using the bundled model. Distinguish demo results from real equipment performance.
```

## Offline quick start

From the extraction directory (replace `python` with your interpreter as needed):

```sh
python pid-tuning/scripts/pid_lab.py simulate --config pid-tuning/assets/thermal-demo.json --csv baseline.csv
python pid-tuning/scripts/pid_lab.py analyze --csv baseline.csv --control-limits 0 100
```

Reports are JSON. Existing CSV files are never overwritten. Copy the demo configuration
into your working directory, change one gain, and simulate into a new CSV to compare.
This is a repeatable first-order model, not a model of your equipment. Sparse CSV
exports from the main application also work; round, gain, setpoint and time resets
start separate segments. See the [tool reference](https://github.com/KINGSTON-115/llm-pid-tuner/blob/skill-pid-tuning-v1.0.0/skills/pid-tuning/references/telemetry.md)
for metric definitions, input requirements and the `check-update` proposal format.

Acceptance criteria depend on the application. A small observed error or bounded
gain update alone does not establish safety or convergence. Confirm controller units,
sample time and output scaling before transferring gains.

## Independent releases

DLC tags use `skill-pid-tuning-v*`, independently of desktop `v2.*` releases, and do
not replace the desktop Latest download. The package contains the skill, three
references, an offline helper, demo configuration, UI metadata and MIT License.
Application configuration and credentials are excluded by an explicit file allowlist.

Maintainers build from the repository with its designated interpreter:

```powershell
& 'C:/Users/Administrator/conda-envs/matlab310/python.exe' -m pytest -q tests/test_skill_dlc.py
& 'C:/Users/Administrator/conda-envs/matlab310/python.exe' scripts/build_skill_dlc.py
```

The builder checks `dlc/pid-tuning.json` against the skill version and creates
deterministic ZIP/`.skill` archives and SHA-256 checksums in `dist/skill-dlc/`.
Update version metadata and the allowlist with skill changes, run the full project
suite, and verify the extracted package outside the repository before publishing.
