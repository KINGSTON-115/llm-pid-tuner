# Native desktop bundle; build with requirements-desktop.txt and PyInstaller.
from PyInstaller.utils.hooks import copy_metadata
from pathlib import Path
from PySide6.QtCore import QLibraryInfo
import os
import sys

# Conda's libffi may live outside DLLs and PyInstaller's normal search path.
ffi = Path(sys.prefix) / "Library" / "bin" / "ffi.dll"
binaries = [(str(ffi), ".")] if ffi.exists() else []
translations = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
qt_copy = [(str(translations / "qtbase_zh_CN.qm"), "PySide6/translations")]

a = Analysis(
    ["desktop_main.pyw", "launcher.py"],
    pathex=[], binaries=binaries,
    datas=copy_metadata("PySide6_Essentials") + copy_metadata("shiboken6") + qt_copy,
    hiddenimports=["serial.tools.list_ports"],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)
# Qt 6 uses Windows' ICU shim. An unrelated Poppler/Conda ICU on PATH can be
# collected instead and lacks the unversioned symbols required by Qt.
if sys.platform == "win32":
    system32 = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32"
    system_icu = {name for name in ("icuuc.dll", "icuin.dll", "icudt.dll", "icu.dll")
                  if (system32 / name).exists()}
    a.binaries = [item for item in a.binaries if Path(item[0]).name.lower() not in system_icu]
pyz = PYZ(a.pure)
desktop_scripts = [entry for entry in a.scripts if Path(entry[1]).name != "launcher.py"]
console_scripts = [entry for entry in a.scripts if Path(entry[1]).name != "desktop_main.pyw"]
exe = EXE(pyz, desktop_scripts, [], exclude_binaries=True, name="llm-pid-tuner-desktop",
          debug=False, strip=False, upx=False, console=False)
# Keep console commands available without duplicating the shared Qt/runtime files.
console = EXE(pyz, console_scripts, [], exclude_binaries=True, name="llm-pid-tuner",
              debug=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, console, a.binaries, a.datas, strip=False, upx=False, name="llm-pid-tuner-desktop")
