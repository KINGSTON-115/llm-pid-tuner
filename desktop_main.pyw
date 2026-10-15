"""Console-free Windows entry point for source installations."""
import ctypes
from pathlib import Path
import sys
import traceback

from desktop_main import main

try:
    main()
except (Exception, SystemExit) as exc:
    directory = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    log = directory / "desktop-startup-error.log"
    try:
        log.write_text(traceback.format_exc(), encoding="utf-8")
        message = f"{exc}\n\n详细信息：{log}"
    except OSError:
        message = str(exc) or traceback.format_exc()
    ctypes.windll.user32.MessageBoxW(None, message, "LLM PID Tuner", 0x10)
