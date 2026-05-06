#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import sys
import traceback

from core.config import initialize_runtime_config
from hw.bridge import safe_pause
import simulator
import tuner


MODE_TUNE = "tune"
MODE_SIM = "sim"
MODE_QUIT = "quit"

TUNE_ALIASES = {MODE_TUNE, "hardware", "hw", "serial", "1"}
SIM_ALIASES = {MODE_SIM, "simulate", "simulator", "dashboard", "2"}
QUIT_ALIASES = {MODE_QUIT, "q", "exit"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Launch hardware tuning or the local simulator dashboard."
    )
    parser.add_argument(
        "mode_or_port",
        nargs="?",
        help="Launch mode (`tune` / `sim`) or a serial port like COM5.",
    )
    parser.add_argument(
        "extra", nargs="*", help="Additional arguments forwarded to the selected mode."
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Disable the Textual dashboard and use plain logs for simulator or hardware tuning.",
    )
    parser.add_argument(
        "--lang",
        choices=["zh", "en"],
        help="Override display language (zh or en); forwarded to simulator.",
    )
    return parser


def normalize_mode(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip().lower()
    if normalized in TUNE_ALIASES:
        return MODE_TUNE
    if normalized in SIM_ALIASES:
        return MODE_SIM
    if normalized in QUIT_ALIASES:
        return MODE_QUIT
    return None


def can_prompt() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def prompt_launch_mode(default_mode: str = MODE_SIM) -> str:
    print("=" * 60)
    print("  LLM PID Tuner PRO - Launcher")
    print("=" * 60)
    print("[1] Hardware tuning (Arduino / ESP32 / serial device)")
    print("[2] Local simulation / Simulink")
    print("[Q] Quit")

    default_choice = "2" if default_mode == MODE_SIM else "1"
    choice = input(f"Choose a mode [{default_choice}]: ").strip().lower()
    if not choice:
        return default_mode

    normalized = normalize_mode(choice)
    if normalized is None:
        print(f"[WARN] Unknown choice '{choice}'. Starting {default_mode}.")
        return default_mode
    return normalized


def run_simulation(force_plain: bool, lang: str | None = None) -> None:
    sim_args: list[str] = []
    if force_plain:
        sim_args.append("--plain")
    if lang:
        sim_args.extend(["--lang", lang])
    simulator.main(sim_args)


def run_tuner(args: list[str]) -> None:
    tuner.main(args)


def dispatch(
    mode_or_port: str | None,
    extra: list[str],
    force_plain: bool = False,
    lang: str | None = None,
) -> None:
    normalized = normalize_mode(mode_or_port)

    if normalized == MODE_SIM:
        if extra:
            raise SystemExit(
                "Simulator mode does not accept extra positional arguments."
            )
        run_simulation(force_plain, lang=lang)
        return

    if normalized == MODE_TUNE:
        tuner_args = list(extra)
        if force_plain:
            tuner_args.append("--plain")
        run_tuner(tuner_args)
        return

    if normalized == MODE_QUIT:
        safe_pause("Press Enter to exit...")
        return

    if mode_or_port:
        tuner_args = [mode_or_port, *extra]
        if force_plain:
            tuner_args.append("--plain")
        run_tuner(tuner_args)
        return

    if force_plain:
        run_simulation(True, lang=lang)
        return

    if not can_prompt():
        run_tuner([])
        return

    choice = prompt_launch_mode(default_mode=MODE_SIM)
    if choice == MODE_TUNE:
        run_tuner([])
        return
    if choice == MODE_SIM:
        run_simulation(force_plain=False, lang=lang)
        safe_pause("Press Enter to exit...")
        return

    safe_pause("Press Enter to exit...")


def main(argv: list[str] | None = None) -> None:
    try:
        initialize_runtime_config(create_if_missing=True, verbose=True)
        args = build_parser().parse_args(argv)
        dispatch(
            args.mode_or_port, list(args.extra), force_plain=args.plain, lang=args.lang
        )
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user (Ctrl+C). Exiting gracefully.")
        # Try to flush any pending logs or CSV writes before exiting
        try:
            import sys
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        sys.exit(0)
    except SystemExit as exc:
        if exc.code not in (None, 0):
            if isinstance(exc.code, str):
                print(f"[ERROR] {exc.code}")
            else:
                print(f"[ERROR] Launcher exited with status {exc.code}.")
            if can_prompt():
                safe_pause("Press Enter to exit...")
        raise
    except Exception as exc:
        print(f"[ERROR] Launcher failed: {exc}")
        if can_prompt():
            debug_enabled = False
            try:
                from core.config import CONFIG

                initialize_runtime_config(create_if_missing=False, verbose=False)
                debug_enabled = bool(CONFIG.get("LLM_DEBUG_OUTPUT"))
            except Exception:
                debug_enabled = False

            if debug_enabled:
                traceback.print_exc()
            safe_pause("Press Enter to exit...")
        raise


if __name__ == "__main__":
    main()
