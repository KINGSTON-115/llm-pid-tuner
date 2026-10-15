# Native desktop bundle; build with requirements-desktop.txt and PyInstaller.
from PyInstaller.utils.hooks import copy_metadata
from pathlib import Path
import sys

# Conda's libffi may live outside DLLs and PyInstaller's normal search path.
ffi = Path(sys.prefix) / "Library" / "bin" / "ffi.dll"
binaries = [(str(ffi), ".")] if ffi.exists() else []

a = Analysis(
    ["desktop_main.pyw"],
    pathex=[], binaries=binaries,
    datas=copy_metadata("PySide6_Essentials") + copy_metadata("shiboken6"),
    hiddenimports=["serial.tools.list_ports"],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="llm-pid-tuner-desktop",
          debug=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="llm-pid-tuner-desktop")
