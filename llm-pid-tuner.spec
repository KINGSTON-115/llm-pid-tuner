# -*- mode: python ; coding: utf-8 -*-

from __future__ import annotations

import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)


ROOT_DIR = Path(globals().get("SPECPATH", Path.cwd())).resolve()

hiddenimports = collect_submodules("rich._unicode_data")
datas = []
binaries = []

if importlib.util.find_spec("matlab") is not None:
    hiddenimports += collect_submodules("matlab")
    datas += collect_data_files("matlab")
    binaries += collect_dynamic_libs("matlab")

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
