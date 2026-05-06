# -*- mode: python ; coding: utf-8 -*-

import os
import sys


conda_env = sys.prefix
dll_path = os.path.join(conda_env, "Library", "bin")
binaries = []
if os.path.exists(dll_path):
    for dll in [
        "ffi.dll",
        "ffi-7.dll",
        "ffi-8.dll",
        "libbz2.dll",
        "liblzma.dll",
        "libcrypto-3-x64.dll",
        "libssl-3-x64.dll",
        "libexpat.dll",
    ]:
        dll_file = os.path.join(dll_path, dll)
        if os.path.exists(dll_file):
            binaries.append((dll_file, "."))


a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=[],
    hiddenimports=[
        "ssl",
        "urllib3",
        "requests",
        "serial",
        "serial.tools",
        "serial.tools.list_ports",
    ],
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
