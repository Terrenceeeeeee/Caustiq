"""PyInstaller onedir build. Invoke from the repository root on Windows."""
from pathlib import Path
import os

root = Path(SPECPATH).resolve().parents[1]
a = Analysis(
    [str(root / 'frozen_launcher.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / 'assets'), 'assets'),
        (str(root / 'sample_data'), 'sample_data'),
        (str(root / 'docs'), 'docs'),
        (str(root / 'measurement_config.json'), '.'),
    ],
    hiddenimports=['matplotlib.backends.backend_qtagg'],
    hookspath=[],
    hooksconfig={'matplotlib': {'backends': ['QtAgg', 'Agg']}},
    runtime_hooks=[],
    excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'IPython', 'pytest'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='Caustiq', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False,
    console=bool(os.environ.get('CAUSTIQ_DEBUG_CONSOLE')),
    version=str(root / 'packaging' / 'windows' / 'version_info.txt'),
    icon=str(root / 'assets' / 'caustiq.ico'),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Caustiq')
