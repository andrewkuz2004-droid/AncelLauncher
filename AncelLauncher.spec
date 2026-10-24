# -*- mode: python ; coding: utf-8 -*-
# One-file build: output is dist/AncelLauncher.exe only
# Run THAT file. Ignore the build/ folder completely.

from pathlib import Path

block_cipher = None
root = Path(SPECPATH)

a = Analysis(
    ['AncelLauncher.pyw'],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / 'assets'), 'assets'),
        (str(root / 'locale'), 'locale'),
    ],
    hiddenimports=['PIL', 'PIL.Image', 'PIL.ImageDraw', 'PIL._tkinter_finder'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AncelLauncher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(root / 'assets' / 'icons' / 'app.ico'),
)
