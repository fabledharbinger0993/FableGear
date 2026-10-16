# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# essentia must be collected explicitly. It is a ~47 MB C++ extension
# (_essentia*.so plus bundled .dylibs), and audio_processor imports it
# *inside functions* on purpose so a missing essentia degrades to librosa
# rather than breaking startup. That combination is exactly what PyInstaller's
# static analysis misses.
#
# Getting this wrong is silent and expensive: the optional-dependency fallback
# would quietly engage in the packaged app and beat-grid accuracy would drop
# from ~91% to ~13% exact agreement with Rekordbox (see
# audio_processor._detect_bpm_essentia) with no error shown to the user.
# `health.py` asserts essentia is importable at runtime so a packaging
# regression surfaces as a health warning instead of bad grids at a gig.
_essentia_datas, _essentia_binaries, _essentia_hidden = collect_all("essentia")

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_essentia_binaries,
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
        ('chop_shop', 'chop_shop'),
    ] + _essentia_datas,
    hiddenimports=[
        'essentia',
        'essentia.standard',
    ] + _essentia_hidden,
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
    [],
    exclude_binaries=True,
    name='FableGear',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FableGear',
)
app = BUNDLE(
    coll,
    name='FableGear.app',
    icon=None,
    bundle_identifier=None,
)
