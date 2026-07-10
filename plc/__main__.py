from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import List, Optional

from plc.modbus import ModbusPLCBridge
from plc.profile import PLCProfileError, load_plc_profile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read PLC PID loop data through an experimental Modbus profile."
    )
    parser.add_argument(
        "--profile",
        default=str(Path(__file__).with_name("example_profile.json")),
        help="Path to a PLC profile JSON file.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=1,
        help="Number of read-only snapshots to print.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.samples < 1:
        print("--samples must be at least 1", file=sys.stderr)
        return 2
    try:
        profile = load_plc_profile(args.profile)
    except PLCProfileError as exc:
        print(f"invalid PLC profile: {exc}", file=sys.stderr)
        return 2

    bridge = ModbusPLCBridge(profile)
    if not bridge.connect():
        print(f"PLC connection failed: {bridge.last_error}", file=sys.stderr)
        return 1
    try:
        for index in range(args.samples):
            print(json.dumps(bridge.read_snapshot(), ensure_ascii=False))
            if index + 1 < args.samples and profile.acquisition.poll_interval_sec > 0:
                time.sleep(profile.acquisition.poll_interval_sec)
        return 0
    except Exception as exc:
        print(f"PLC read failed: {exc}", file=sys.stderr)
        return 1
    finally:
        bridge.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
