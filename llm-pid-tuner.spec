# -*- mode: python ; coding: utf-8 -*-

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)


ROOT_DIR = Path(globals().get("SPECPATH", Path.cwd())).resolve()
PYTHON_PREFIX = Path(sys.prefix).resolve()

hiddenimports = collect_submodules("rich._unicode_data") + ["_ssl", "_hashlib"]

if importlib.util.find_spec("textual") is not None:
    hiddenimports += ["sim.tui"]
datas = []
binaries = []


def add_optional_binary(binary_name: str, source_dir: Path, target_dir: str = ".") -> None:
    candidate = source_dir / binary_name
    if candidate.exists():
        binaries.append((str(candidate), target_dir))


for binary_name in ("libssl-3-x64.dll", "libcrypto-3-x64.dll"):
    add_optional_binary(binary_name, PYTHON_PREFIX / "Library" / "bin")

if importlib.util.find_spec("matlab") is not None:
    import matlab
    import matlab.engine
    from matlab.engine import pythonengine
    import matlabmultidimarrayforpython

    hiddenimports += collect_submodules("matlab")
    datas += collect_data_files("matlab")
    binaries += collect_dynamic_libs("matlab")
    binaries += [
        (str(Path(pythonengine.__file__).resolve()), "."),
        (str(Path(matlabmultidimarrayforpython.__file__).resolve()), "."),
    ]

a = Analysis(
    ["launcher.py"],
    pathex=[str(ROOT_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="llm-pid-tuner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
