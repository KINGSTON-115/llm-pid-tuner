# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules


rich_hiddenimports = collect_submodules('rich._unicode_data')
matlab_hiddenimports = collect_submodules('matlab')

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[
        ('D:/Program Files/MATLAB/R2022b/extern/engines/python/dist/matlab/engine/win64/matlabengineforpython3_8.pyd', 'matlab/engine/win64'),
    ],
    datas=[
        ('d:/Python_Learning/llm-pid-tuner/venv_build/Lib/site-packages/matlab/engine/_arch.txt', 'matlab/engine'),
    ],
    hiddenimports=rich_hiddenimports + matlab_hiddenimports,
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
    name='llm-pid-tuner',
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
