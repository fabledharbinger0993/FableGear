#!/usr/bin/env python3
"""
FableGear -- Install Wizard.

A friendly window for what setup.sh does in a Terminal: check the Mac, build
FableGear's private Python environment, install its packages with live
progress, create the launcher app, and hand off to FableGear.

What it does NOT do, on purpose: install or change system software. No Homebrew,
no Python, no ffmpeg, no Chromaprint. Those are yours -- if one is missing the wizard
says exactly what is needed and waits while you install it. This is the same contract
setup.sh follows. Everything it installs goes into the private venv inside the
FableGear folder. The one thing it writes outside that folder is the launcher app:
at the very end it (re)creates ~/Applications/FableGear.app via
packaging/create_launcher_app.sh, best-effort and never fatal.

Deliberately standalone, and stdlib-only. It imports nothing from the rest of
FableGear (config.py loads a user config at import time that does not exist yet
on a fresh install) and it does not even need Flask: installing Flask is one of
the things it does, so depending on it would be circular. Any python3 can run
this file -- including the one macOS ships -- because the interpreter that runs
the wizard is separate from the one it picks for FableGear's environment.

Run modes:

    python3 installer_wizard.py           # native window if pywebview is
                                          # importable, otherwise your browser
    python3 installer_wizard.py --serve   # plain localhost server, no window

The dependency list shown to the user is generated FROM requirements_ui.txt,
requirements.txt and requirements_optional.txt at request time, never
hand-copied: a list that goes stale relative to what is really installed is
worse than a package name with no description.

Security: the server listens on 127.0.0.1 only, rejects any Host header that is
not a loopback name (DNS rebinding), and every endpoint that installs software or
launches the app requires a random per-run token that only the served page knows
(so a web page open in another tab cannot trigger an install).
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

log = logging.getLogger("installer_wizard")

REPO_ROOT = Path(__file__).resolve().parent
TEMPLATE_PATH = REPO_ROOT / "templates" / "installer_wizard.html"
STATIC_DIR = REPO_ROOT / "static"
VENV_DIR = REPO_ROOT / "venv"
LAUNCHER_SCRIPT = REPO_ROOT / "packaging" / "create_launcher_app.sh"
TOKEN_PLACEHOLDER = "__WIZARD_TOKEN__"

MIN_PYTHON = (3, 11)
MAX_PYTHON = (3, 13)
MIN_FREE_DISK_GB = 2.0

# Tools FableGear shells out to. (command, label, what it is for)
REQUIRED_TOOLS = (
    ("ffmpeg", "ffmpeg", "audio decoding and conversion"),
    ("fpcalc", "Chromaprint (fpcalc)", "duplicate-detection fingerprinting"),
)

# A GUI-launched process gets a minimal PATH, so look where package managers and
# installers put things too. Read-only: nothing here is ever written to.
EXTRA_TOOL_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin")
PYTHON_CANDIDATES = (
    "python3.13", "python3.12", "python3.11", "python3",
    "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
    "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
    "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3",
)


# ─── System checks ─────────────────────────────────────────────────────────
#
# Each check is independent and never raises -- a check that crashes the wizard
# over a missing `sw_vers` is worse than one that reports "unknown" and lets the
# user decide. status is one of "ok" / "warn" / "fail"; only "fail" blocks.

@dataclass
class CheckResult:
    id: str
    label: str
    status: str
    detail: str = ""


def _run(cmd: list[str], timeout: float = 5.0) -> str | None:
    """Run a command, return stripped stdout, or None on any failure."""
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _search_path() -> str:
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    for extra in EXTRA_TOOL_DIRS:
        if extra not in parts and os.path.isdir(extra):
            parts.append(extra)
    return os.pathsep.join(parts)


def find_tool(name: str) -> str | None:
    return shutil.which(name, path=_search_path())


def _has_homebrew() -> bool:
    return any(Path(p).is_file() for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"))


def install_hint() -> str:
    """How to get the missing tools. Advice only -- the wizard never runs it."""
    if _has_homebrew():
        return "With Homebrew you can run: brew install ffmpeg chromaprint"
    return ("Install ffmpeg (ffmpeg.org) and Chromaprint (acoustid.org/chromaprint), "
            "or use your package manager")


def check_platform() -> CheckResult:
    system = platform.system()
    if system != "Darwin":
        # Also runs in CI/dev on Linux (see tests); say so plainly.
        return CheckResult("platform", "Operating system", "warn",
                           f"FableGear targets macOS; running on {system}")
    version = _run(["sw_vers", "-productVersion"]) or platform.mac_ver()[0]
    if not version:
        return CheckResult("platform", "macOS version", "warn", "Could not detect version")
    try:
        major = int(version.split(".")[0])
    except ValueError:
        return CheckResult("platform", "macOS version", "warn", version)
    if major < 12:
        return CheckResult("platform", "macOS version", "fail",
                           f"macOS {version} -- FableGear requires 12.0 or later")
    return CheckResult("platform", "macOS version", "ok", f"macOS {version}")


def check_disk_space() -> CheckResult:
    try:
        free_gb = shutil.disk_usage(REPO_ROOT).free / (1024**3)
    except OSError as exc:
        return CheckResult("disk", "Free disk space", "warn", str(exc))
    if free_gb < MIN_FREE_DISK_GB:
        return CheckResult("disk", "Free disk space", "fail",
                           f"{free_gb:.1f} GB free -- need at least {MIN_FREE_DISK_GB:.0f} GB")
    return CheckResult("disk", "Free disk space", "ok", f"{free_gb:.1f} GB free")


def check_network() -> CheckResult:
    try:
        socket.create_connection(("pypi.org", 443), timeout=4).close()
    except OSError:
        return CheckResult("network", "Internet connection", "fail",
                           "Could not reach pypi.org -- installing packages needs a connection")
    return CheckResult("network", "Internet connection", "ok", "Connected")


def _python_version(exe: str) -> tuple[int, int, int] | None:
    out = _run([exe, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"])
    try:
        major, minor, micro = (int(x) for x in (out or "").split("."))
    except ValueError:
        return None
    return (major, minor, micro)


def find_python() -> tuple[str, tuple[int, int, int]] | None:
    """The first interpreter in MIN_PYTHON..MAX_PYTHON, or None. Same rule as setup.sh."""
    for name in PYTHON_CANDIDATES:
        exe = name if os.path.isabs(name) else find_tool(name)
        if not exe or not os.path.isfile(exe):
            continue
        ver = _python_version(exe)
        if ver and MIN_PYTHON <= ver[:2] <= MAX_PYTHON:
            return exe, ver
    return None


def check_python() -> CheckResult:
    found = find_python()
    want = f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}-{MAX_PYTHON[0]}.{MAX_PYTHON[1]}"
    if found is None:
        return CheckResult(
            "python", "Python", "fail",
            f"No Python {want} found. Install one from python.org/downloads "
            "(or your package manager), then press Check again")
    exe, ver = found
    return CheckResult("python", "Python", "ok", f"Python {ver[0]}.{ver[1]}.{ver[2]}  ({exe})")


def check_tool(command: str, label: str, purpose: str) -> CheckResult:
    path = find_tool(command)
    if path:
        return CheckResult(f"tool:{command}", label, "ok", path)
    return CheckResult(f"tool:{command}", label, "fail",
                       f"Not found (needed for {purpose}). {install_hint()}, "
                       "then press Check again")


def check_venv() -> CheckResult:
    if (VENV_DIR / "bin" / "activate").is_file():
        return CheckResult("venv", "Private Python environment", "ok", str(VENV_DIR))
    return CheckResult("venv", "Private Python environment", "warn", "Will be created next")


def run_all_checks() -> list[CheckResult]:
    checks = [check_platform(), check_disk_space(), check_network(), check_python()]
    checks.extend(check_tool(*tool) for tool in REQUIRED_TOOLS)
    checks.append(check_venv())
    return checks


# ─── Dependency plan, read from the real requirements files ────────────────

@dataclass
class RequirementGroup:
    id: str
    label: str
    file: str
    required: bool
    description: str
    packages: list[str] = field(default_factory=list)


def _parse_requirements(path: Path) -> list[str]:
    """
    Top-level package names from a requirements file.

    Comments, blank lines and `-r other.txt` includes are skipped: the include is
    resolved by pip, and listing it as a "package" would be wrong.
    """
    if not path.is_file():
        return []
    names: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-r "):
            continue
        for sep in ("==", ">=", "<=", "~=", ">", "<"):
            if sep in line:
                line = line.split(sep, 1)[0]
                break
        names.append(line.strip())
    return names


def build_plan() -> list[RequirementGroup]:
    return [
        RequirementGroup(
            id="ui", label="Interface", file="requirements_ui.txt", required=True,
            description="The window and controls you're looking at right now.",
            packages=_parse_requirements(REPO_ROOT / "requirements_ui.txt"),
        ),
        RequirementGroup(
            id="core", label="Library engine", file="requirements.txt", required=True,
            description="Reads and writes your Rekordbox library, analyzes audio, "
                        "talks to your drives.",
            packages=_parse_requirements(REPO_ROOT / "requirements.txt"),
        ),
        RequirementGroup(
            id="optional", label="Enhanced beat detection",
            file="requirements_optional.txt", required=False,
            description="Best-effort. Narrower platform support, so if it can't "
                        "install, FableGear still runs with less accurate beat detection.",
            packages=_parse_requirements(REPO_ROOT / "requirements_optional.txt"),
        ),
    ]


# ─── Streamed subprocess execution (SSE framing) ────────────────────────────
#
# Same framing as helpers.py::_stream elsewhere in FableGear (data: <line>\n\n),
# reimplemented here per the module docstring rather than imported.

def stream_command(cmd: list[str], *, cwd: Path | None = None) -> Iterator[str]:
    """Run `cmd`, yielding SSE-framed output lines as they arrive."""
    yield f"data: $ {' '.join(cmd)}\n\n"
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
    except OSError as exc:
        yield f"data: [ERROR] could not start: {exc}\n\n"
        yield "data: [DONE:1]\n\n"
        return

    assert proc.stdout is not None
    for line in proc.stdout:
        yield f"data: {line.rstrip()}\n\n"
    yield f"data: [DONE:{proc.wait()}]\n\n"


def _exit_code(done_line: str) -> str:
    return done_line[len("data: [DONE:"):].split("]")[0]


def stream_steps(steps: list[list[str]], *, cwd: Path | None = None) -> Iterator[str]:
    """Run commands in sequence, stopping at the first failure."""
    for cmd in steps:
        failed = False
        for line in stream_command(cmd, cwd=cwd):
            if line.startswith("data: [DONE:"):
                failed = _exit_code(line) != "0"
            else:
                yield line
        if failed:
            yield "data: [SEQUENCE_FAILED]\n\n"
            return
    yield "data: [SEQUENCE_OK]\n\n"


def venv_python() -> str:
    candidate = VENV_DIR / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def _venv_is_usable() -> bool:
    if not (VENV_DIR / "bin" / "activate").is_file():
        return False
    ver = _python_version(str(VENV_DIR / "bin" / "python"))
    return bool(ver and MIN_PYTHON <= ver[:2] <= MAX_PYTHON)  # both bounds: a 3.14 venv is as unusable as a 3.9


def stream_venv_setup() -> Iterator[str]:
    """Create (or repair) the private environment, then bring pip up to date."""
    found = find_python()
    if found is None:
        yield "data: [ERROR] no Python in the supported range was found\n\n"
        yield "data: [SEQUENCE_FAILED]\n\n"
        return
    py, _ver = found

    if VENV_DIR.exists() and not _venv_is_usable():
        # Same repair setup.sh does: a hollow venv (no activate script) or one built
        # by a too-old Python can't be fixed in place. It lives inside the FableGear
        # folder and is rebuilt from requirements, so nothing of the user's is lost.
        yield f"data: Existing environment is unusable -- rebuilding {VENV_DIR}\n\n"
        shutil.rmtree(VENV_DIR, ignore_errors=True)

    steps: list[list[str]] = []
    if not VENV_DIR.exists():
        steps.append([py, "-m", "venv", str(VENV_DIR)])
    steps.append([str(VENV_DIR / "bin" / "python"), "-m", "pip", "install", "--upgrade", "pip"])
    yield from stream_steps(steps, cwd=REPO_ROOT)


def required_install_commands() -> list[list[str]]:
    py = venv_python()
    cmds: list[list[str]] = []
    for group in build_plan():
        if group.required and (REPO_ROOT / group.file).is_file():
            cmds.append([py, "-m", "pip", "install", "-r", str(REPO_ROOT / group.file)])
    return cmds


def optional_install_command() -> list[str] | None:
    path = REPO_ROOT / "requirements_optional.txt"
    if not path.is_file():
        return None
    return [venv_python(), "-m", "pip", "install", "-r", str(path)]


def stream_python_install() -> Iterator[str]:
    """
    Required packages first (any failure stops everything), then the optional group,
    which can never fail the install: setup.sh treats it the same way, because
    essentia has narrow wheel coverage and a missing wheel must degrade beat
    detection, not take the whole install down.
    """
    if not (VENV_DIR / "bin" / "python").exists():
        # Never fall back to the system interpreter: that would pip-install FableGear's
        # packages into the user's global Python, which is exactly what this wizard
        # promises not to touch.
        yield "data: [ERROR] the private environment doesn't exist yet -- run that step first\n\n"
        yield "data: [SEQUENCE_FAILED]\n\n"
        return

    for line in stream_steps(required_install_commands(), cwd=REPO_ROOT):
        if "[SEQUENCE_OK]" in line:
            break
        yield line
        if "[SEQUENCE_FAILED]" in line:
            return

    cmd = optional_install_command()
    if cmd is not None:
        failed = False
        for line in stream_command(cmd, cwd=REPO_ROOT):
            if line.startswith("data: [DONE:"):
                failed = _exit_code(line) != "0"
            else:
                yield line
        if failed:
            yield "data: [OPTIONAL_SKIPPED]\n\n"
    yield "data: [SEQUENCE_OK]\n\n"


# ─── Finish: launcher app, ready marker, hand-off ───────────────────────────

def create_launcher_app() -> bool:
    """Run the shared launcher script. Never raises -- a missing launcher isn't fatal."""
    if not LAUNCHER_SCRIPT.is_file():
        return False
    try:
        done = subprocess.run(
            ["/bin/bash", str(LAUNCHER_SCRIPT), str(REPO_ROOT)],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("launcher creation failed: %s", exc)
        return False
    return done.returncode == 0 and "created" in done.stdout


def finish() -> dict:
    launcher = create_launcher_app()
    (REPO_ROOT / ".fablegear_ready").touch()
    return {"ok": True, "launcher": launcher}


def _launch_app_and_exit() -> None:
    """Hand off to the real app, then let this process end."""
    time.sleep(0.4)
    subprocess.Popen(
        [venv_python(), str(REPO_ROOT / "main.py")], cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    os._exit(0)


# ─── HTTP server (stdlib) ───────────────────────────────────────────────────

class WizardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, *, token: str | None = None):
        super().__init__(address, _Handler)
        self.token = token or secrets.token_urlsafe(16)
        self.port = self.server_address[1]


class _Handler(BaseHTTPRequestHandler):
    server: WizardServer
    server_version = "FableGearWizard"
    protocol_version = "HTTP/1.0"  # streamed responses end by closing the connection

    def log_message(self, fmt, *args):  # keep the Terminal quiet
        log.debug("%s - %s", self.address_string(), fmt % args)

    # -- helpers
    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status: int = 200):
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost", "::1")

    def _token_ok(self, query: dict) -> bool:
        supplied = (query.get("t") or [""])[0]
        return secrets.compare_digest(supplied, self.server.token)

    def _sse(self, gen: Iterator[str]):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            for chunk in gen:
                self.wfile.write(chunk.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    # -- routes
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method: str):
        if not self._host_ok():
            return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
        parts = urlsplit(self.path)
        path, query = parts.path, parse_qs(parts.query)

        if method == "GET":
            if path == "/":
                html = TEMPLATE_PATH.read_text(encoding="utf-8").replace(
                    TOKEN_PLACEHOLDER, self.server.token)
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            if path.startswith("/static/"):
                return self._static(unquote(path[len("/static/"):]))
            if path == "/api/checks":
                return self._json([asdict(c) for c in run_all_checks()])
            if path == "/api/plan":
                return self._json([asdict(g) for g in build_plan()])
            if path in ("/api/install/venv", "/api/install/python"):
                if not self._token_ok(query):
                    return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
                gen = stream_venv_setup() if path.endswith("/venv") else stream_python_install()
                return self._sse(gen)
        elif method == "POST" and path == "/api/complete":
            if not self._token_ok(query):
                return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
            result = finish()
            self._json(result)
            threading.Thread(target=_launch_app_and_exit, daemon=True).start()
            return None
        return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

    def _static(self, rel: str):
        target = (STATIC_DIR / rel).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        return self._send(200, target.read_bytes(), ctype)


# ─── Entry point ───────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FableGear install wizard")
    parser.add_argument("--serve", action="store_true",
                        help="plain localhost server, no native window and no browser launch")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)

    if not TEMPLATE_PATH.is_file():
        print(f"error: missing {TEMPLATE_PATH}", file=sys.stderr)
        return 2

    server = WizardServer(("127.0.0.1", args.port))
    url = f"http://127.0.0.1:{server.port}"

    if args.serve:
        print(f"FableGear install wizard: {url}")
        server.serve_forever()
        return 0

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        import webview  # only present once FableGear's own packages are installed
    except ImportError:
        # The normal case on a fresh Mac: pywebview is one of the things we install.
        print(f"FableGear install wizard: {url}\n(opening your browser)")
        webbrowser.open(url)
        thread.join()
        return 0

    # Wider than the 640px wizard panel on purpose -- Spectre roams the side
    # margins this leaves (see templates/installer_wizard.html).
    webview.create_window("FableGear Setup", url, width=940, height=700,
                          resizable=False, frameless=False)
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
