"""Desktop entry point; legacy console modes do not require Qt."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def main() -> None:
    if getattr(sys, "frozen", False):
        os.chdir(Path(sys.executable).resolve().parent)
    from core.config import initialize_runtime_config
    initialize_runtime_config(create_if_missing=True, verbose=False)
    try:
        from desktop.app import run_desktop
    except ImportError as exc:
        raise SystemExit("桌面界面需要 Qt。请使用 Python 3.10+ 执行：\n"
                         "python -m pip install -r requirements-desktop.txt") from exc
    run_desktop()


if __name__ == "__main__":
    main()
