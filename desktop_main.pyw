"""Console-free Windows entry point for source installations."""
import ctypes
import traceback

from desktop_main import main

try:
    main()
except (Exception, SystemExit) as exc:
    ctypes.windll.user32.MessageBoxW(None, str(exc) or traceback.format_exc(), "LLM PID Tuner", 0x10)
