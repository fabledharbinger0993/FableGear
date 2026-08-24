"""
FableGear / main.py

Native-window entry point for both development and PyInstaller builds.

Starts the Flask/Waitress server in a background daemon thread, waits for it
to be ready, then opens a pywebview window.  Because the server thread is a
daemon, it automatically dies when the main thread (pywebview) exits.

Any managed CLI subprocesses spawned by the server are explicitly terminated
when this process exits so long-running jobs cannot be orphaned.

If the server is already running on port 5001 (e.g. a second launch while the
app is open), the existing server is reused and a new window is opened.
"""

import os
import sys
import threading
import time
from pathlib import Path

# ── Arch guard (DISABLED for Intel Macs) ─────────────────────────────
# If launched via Rosetta (x86_64 Python on Apple Silicon), re-exec as arm64
# so arm64-only compiled extensions (e.g. psutil) load correctly.  This is
# a last-resort fallback; the launch scripts already use `arch -arm64`.
#
# Disabled for Intel Macs (pre-Apple Silicon):
# if platform.machine() == "x86_64" and sys.platform == "darwin":
#     _arch_tool = "/usr/bin/arch"
#     if os.path.exists(_arch_tool):
#         os.execv(_arch_tool, [_arch_tool, "-arm64", sys.executable] + sys.argv)

# ── Resource root — works in both dev and PyInstaller bundle ─────────────────
# PyInstaller extracts everything to sys._MEIPASS at runtime.
# In dev, __file__ is just the repo root.
_ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).parent.resolve()))

# Make sure toolkit modules are importable
for _p in (str(_ROOT), str(_ROOT / 'chop_shop')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Tell app.py where to find templates and static when bundled
os.environ.setdefault('FABLEGEAR_ROOT', str(_ROOT))

# ── Server config ─────────────────────────────────────────────────────────────
# Bind to all interfaces so Tailscale (and LAN) can reach the mobile API.
# The desktop UI still opens via localhost; the mobile API uses the Tailscale IP.
_HOST = '0.0.0.0'
_PORT = 5001
_LOCAL_URL = f'http://127.0.0.1:{_PORT}/'   # used for health-check and browser


def _server_running() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen(_LOCAL_URL, timeout=1)
        return True
    except Exception:
        return False


def _start_server() -> None:
    from waitress import serve
    from app import app as flask_app
    serve(flask_app, host=_HOST, port=_PORT, threads=16)


def _wait_for_server(retries: int = 200, delay: float = 0.15) -> bool:
    # 200 × 0.15s = 30s ceiling. A cold first launch (PyInstaller unpack, or the
    # first import of librosa/numpy/flask in a fresh venv) can take well over the
    # old 6s budget; exiting early there looked identical to "the app didn't open."
    for _ in range(retries):
        if _server_running():
            return True
        time.sleep(delay)
    return False


class _Api:
    """Exposed to JS as window.pywebview.api — used for the native folder picker.

    pywebview's create_file_dialog() is far more reliable than osascript in
    the PyInstaller bundle because it goes through WKWebView's native APIs
    rather than requiring Finder Automation permission.
    """
    def __init__(self):
        self._window = None

    def pick_folder(self):
        if not self._window:
            return None
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if result:
            import os
            return os.path.normpath(result[0])
        return None

    def pick_file(self, file_types=None):
        """Open a native file picker. file_types is a list of filter strings, e.g.
        ['Database files (*.db)', 'All files (*.*)']"""
        if not self._window:
            return None
        types = tuple(file_types) if file_types else ('All files (*.*)',)
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN_DIALOG,
            allow_multiple=False,
            file_types=types,
        )
        if result:
            import os
            return os.path.normpath(result[0])
        return None

    def minimize(self):
        if self._window:
            self._window.minimize()

    def toggle_fullscreen(self):
        if self._window:
            self._window.toggle_fullscreen()


if __name__ == '__main__':
    # ── Frozen-bundle CLI routing ─────────────────────────────────────────────
    # In a PyInstaller bundle sys.executable is the app binary, not Python. The
    # server shells tools out via [sys.executable, str(CLI_PATH), <subcommand>, …];
    # in a bundle that would re-launch the UI instead of running the tool. The
    # routes always pass str(CLI_PATH) as argv[1], so a leading argument ending
    # in "cli.py" is the reliable signal to dispatch to cli.main() and exit
    # rather than open a window. The dev (non-frozen) path is unaffected.
    if getattr(sys, 'frozen', False) and len(sys.argv) > 1 and sys.argv[1].endswith('cli.py'):
        import cli
        sys.argv = ['cli.py', *sys.argv[2:]]
        cli.main()
        sys.exit(0)

    # ── Single-instance guard ─────────────────────────────────────────────
    # OS-level exclusive lock; released automatically on process exit/crash.
    # Placed AFTER the frozen-CLI dispatch above so bundled CLI subprocesses
    # (which re-enter this binary) are never blocked by the guard.
    from single_instance import acquire as _acquire_single_instance, lock_path as _si_lock_path
    if not _acquire_single_instance():
        print(
            'FableGear is already running — switch to the existing window. '
            f'(instance lock: {_si_lock_path()})',
            file=sys.stderr,
        )
        sys.exit(0)

    started_server_here = False
    if not _server_running():
        started_server_here = True
        threading.Thread(target=_start_server, daemon=True).start()
        if not _wait_for_server():
            print('FableGear: server failed to start', file=sys.stderr)
            sys.exit(1)

    # ── MCP server (AI agent access) ─────────────────────────────────────────
    try:
        from user_config import load_user_config, find_available_mcp_port
        _cfg = load_user_config()
        if _cfg.get('mcp_enabled') and _cfg.get('mcp_autostart'):
            from mcp_server import start_embedded as _start_mcp
            _mcp_port = _cfg.get('mcp_port', 5002)
            _mcp_port = find_available_mcp_port(_mcp_port)
            _mcp_host = '0.0.0.0' if _cfg.get('mcp_expose') else '127.0.0.1'
            _mcp_token = _cfg.get('mcp_token', '')
            if _start_mcp(host=_mcp_host, port=_mcp_port, token=_mcp_token):
                print(f'FableGear: MCP server started on {_mcp_host}:{_mcp_port}')
            else:
                print('FableGear: MCP server failed to start', file=sys.stderr)
    except Exception as _mcp_err:
        print(f'FableGear: MCP autostart skipped — {_mcp_err}', file=sys.stderr)

    # Splash: play intro video on every launch if the file exists
    _splash_video = _ROOT / 'static' / 'fablegear-splash.mp4'
    start_url = f'http://127.0.0.1:{_PORT}/splash' if _splash_video.exists() else _LOCAL_URL

    # The native window is the only visible surface. If anything here throws
    # (pywebview import, WKWebView init, create_window), a fire-and-forget
    # launch would just vanish with no window and no clue. Catch it, log a
    # clear message to the log, and fall back to opening the already-running
    # server in the default browser so the user still gets a working app.
    try:
        import webview

        _api = _Api()

        window = webview.create_window(
            title='FableGear',
            url=start_url,
            width=1400,
            height=900,
            min_size=(900, 600),
            resizable=True,
            frameless=True,
            background_color='#07070f',
            js_api=_api,
        )

        _api._window = window

        webview.start(debug=False)
    except Exception as _win_err:
        import traceback
        import webbrowser
        print(f'FableGear: native window failed to open — {_win_err}', file=sys.stderr)
        print(f'FableGear: falling back to your browser at {_LOCAL_URL}', file=sys.stderr)
        traceback.print_exc()
        try:
            webbrowser.open(_LOCAL_URL)
        except Exception:
            pass
        # Keep the (daemon) server thread alive so the browser tab keeps working
        # instead of the process exiting immediately and killing the server.
        try:
            while _server_running():
                time.sleep(2)
        except KeyboardInterrupt:
            pass

    if started_server_here:
        try:
            from helpers import terminate_managed_subprocesses
            terminate_managed_subprocesses(force=True, include_orphans=True)
        except Exception:
            pass
