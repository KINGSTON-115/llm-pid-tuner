"""Inspect or run controller JSON using the production desktop session engine."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.config import initialize_runtime_config
from core.controller_formula import ControllerFormula


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("controller", type=Path)
    parser.add_argument("--inspect", action="store_true", help="Inspect without connecting a model or API")
    parser.add_argument("--mode", choices=("python", "simulink", "hardware"), default="python")
    parser.add_argument("--strategy", choices=("offline", "ai"), default="offline")
    parser.add_argument("--setpoint", type=float, default=200.)
    parser.add_argument("--port", default="DEMO")
    parser.add_argument("--rounds", type=int, default=12)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    spec = json.loads(args.controller.read_text(encoding="utf-8-sig"))
    formula = ControllerFormula(spec)
    if args.inspect:
        print(json.dumps(formula.describe(), ensure_ascii=False, indent=2))
        return 0
    initialize_runtime_config(verbose=False)
    from desktop.session import DesktopSession, RunOptions
    from sim.runtime import drain_event_queue
    session = DesktopSession(RunOptions(mode=args.mode, strategy=args.strategy,
        controller_spec=spec, local_search=True, rounds=args.rounds, samples=args.samples,
        setpoint=args.setpoint, port=args.port, speed=50.))
    # Synchronous execution retains the same cleanup and result semantics.
    session._run()
    events = drain_event_queue(session.events)
    result = events[-1]["result"]
    if args.json_out:
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("completed_reason") == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
