# -*- mode: python ; coding: utf-8 -*-
#
# FableGear PyInstaller spec — Windows
#
# Build with:  pyinstaller FableGear_win.spec --noconfirm
# Output:      dist/FableGear/  (one-dir mode — WebView2 DLL resolution)
#
# One-dir is required on Windows because the WebView2 loader (via pywebview)
# expects to find its DLLs next to the executable.  Onefile unpacking adds a
# delay and can cause WebView2 init failures on first run.

from pathlib import Path

SRC = Path('.')

a = Analysis(
    [str(SRC / 'main.py')],
    pathex=[str(SRC)],
    binaries=[],
    datas=[
        (str(SRC / 'templates'), 'templates'),
        (str(SRC / 'static'),    'static'),
    ],
    hiddenimports=[
        # Waitress
        'waitress',
        'waitress.task',
        'waitress.channel',
        'waitress.server',
        'waitress.runner',
        'waitress.utilities',
        # Flask / Jinja2
        'flask',
        'flask.templating',
        'jinja2',
        'jinja2.ext',
        # pkg_resources
        'pkg_resources',
        'pkg_resources.py2_compat',
        # pywebview Windows backends
        # EdgeChromium (WebView2) is the default on Windows for pywebview 5.x.
        # WinForms backend requires pythonnet (clr).
        'webview',
        'webview.http',
        'webview.http.falcon',
        'webview.platforms.winforms',
        'webview.platforms.edgechromium',
        'clr',   # pythonnet — required by pywebview WinForms renderer
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'IPython',
        # macOS-only
        'Foundation',
        'AppKit',
        'Cocoa',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FableGear',
    debug=False,
    strip=False,
    upx=False,
    console=False,   # no terminal window
    icon=str(SRC / 'static' / 'FableGear.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='FableGear',
)
