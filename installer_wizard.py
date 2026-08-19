#!/usr/bin/env python3
"""
FableGear -- Install Wizard.

Runs after setup.sh has done the one thing only bash can do (create a venv
and get Flask + pywebview into it), and takes over everything after that:
system checks, the remaining dependency install with live progress, and the
handoff into FableGear itself.

Deliberately standalone. This module imports nothing from the rest of
FableGear -- not config.py, not helpers.py -- because it has to run
correctly BEFORE those are guaranteed to work. config.py calls
load_user_config() at import time and expects a config file that does not
exist yet at this point in a fresh install; helpers.py pulls in that whole
chain transitively. Bootstrapping code that depends on the thing it is
bootstrapping is how installers turn one problem into two.

Two run modes:

    python3 installer_wizard.py           # pywebview window (real installs)
    python3 installer_wizard.py --serve   # plain localhost server, no window

--serve exists for two reasons: it is how this file gets tested without a
display (a headless browser can drive plain HTTP; it cannot drive a native
WKWebView), and it is a legitimate fallback on a machine where pywebview's
native dependencies are unavailable -- "open http://127.0.0.1:PORT" beats a
dead end.

The dependency step list is generated FROM requirements_ui.txt,
requirements.txt, and requirements_optional.txt at request time -- never
hand-copied. A file that goes stale relative to what setup.sh actually
installs is a worse bug than the wizard displaying a package name with no
description, so read the real file and gets a real command wrong.
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("installer_wizard")

REPO_ROOT = Path(__file__).resolve().parent
TEMPLATE_PATH = REPO_ROOT / "templates" / "installer_wizard.html"
STATIC_DIR = REPO_ROOT / "static"
VENV_DIR = REPO_ROOT / "venv"

MIN_PYTHON = (3, 11)
MIN_FREE_DISK_GB = 2.0
BREW_FORMULAS = ("ffmpeg", "chromaprint")


# ─── System checks ─────────────────────────────────────────────────────────
#
# Each check is independent and never raises -- a check that crashes the
# wizard over a missing `sw_vers` binary is worse than one that reports
# "unknown" and lets the user proceed. status is one of "ok" / "warn" / "fail".

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


def check_platform() -> CheckResult:
    system = platform.system()
    if system != "Darwin":
        # This module runs in CI/dev on Linux too (see tests). Report it
        # plainly rather than pretending to be on macOS.
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
        socket.create_connection(("github.com", 443), timeout=4).close()
    except OSError:
        return CheckResult("network", "Internet connection", "fail",
                            "Could not reach github.com -- installs need a connection")
    return CheckResult("network", "Internet connection", "ok", "Connected")


def _find_brew() -> str | None:
    for candidate in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if Path(candidate).is_file():
            return candidate
    return shutil.which("brew")


def check_homebrew() -> CheckResult:
    brew = _find_brew()
    if not brew:
        return CheckResult("homebrew", "Homebrew", "warn",
                            "Not found -- the next step will install it")
    return CheckResult("homebrew", "Homebrew", "ok", f"Found at {brew}")


def check_formula(name: str) -> CheckResult:
    brew = _find_brew()
    if brew and _run([brew, "list", "--formula", name]) is not None:
        return CheckResult(f"formula:{name}", name, "ok", "Installed")
    return CheckResult(f"formula:{name}", name, "warn", "Will be installed")


def check_python_version() -> CheckResult:
    info = sys.version_info
    label = f"Python {info.major}.{info.minor}.{info.micro}"
    if (info.major, info.minor) < MIN_PYTHON:
        return CheckResult("python", "Python version", "fail",
                            f"{label} -- FableGear requires "
                            f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}+")
    return CheckResult("python", "Python version", "ok", label)


def check_venv() -> CheckResult:
    activate = VENV_DIR / "bin" / "activate"
    if activate.is_file():
        return CheckResult("venv", "Virtual environment", "ok", str(VENV_DIR))
    return CheckResult("venv", "Virtual environment", "warn", "Not created yet")


def run_all_checks() -> list[CheckResult]:
    checks = [
        check_platform(),
        check_disk_space(),
        check_network(),
        check_python_version(),
        check_venv(),
        check_homebrew(),
    ]
    checks.extend(check_formula(name) for name in BREW_FORMULAS)
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
    Extract top-level package names from a requirements file.

    Comment lines and blank lines are skipped; a bare `-r other_file.txt`
    line is skipped too since that reference is resolved by pip, not by us
    -- listing it as a "package" would be wrong.
    """
    if not path.is_file():
        return []
    names: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-r "):
            continue
        # Strip a version specifier: "pyrekordbox==0.4.4" -> "pyrekordbox"
        for sep in ("==", ">=", "<=", "~=", ">", "<"):
            if sep in line:
                line = line.split(sep, 1)[0]
                break
        names.append(line.strip())
    return names


def build_plan() -> list[RequirementGroup]:
    """
    The dependency plan the wizard displays and installs, generated from the
    actual requirements files rather than duplicated by hand.
    """
    return [
        RequirementGroup(
            id="ui",
            label="Interface",
            file="requirements_ui.txt",
            required=True,
            description="The window and controls you're looking at right now.",
            packages=_parse_requirements(REPO_ROOT / "requirements_ui.txt"),
        ),
        RequirementGroup(
            id="core",
            label="Library engine",
            file="requirements.txt",
            required=True,
            description="Reads and writes your Rekordbox library, "
                        "analyzes audio, talks to your drives.",
            packages=_parse_requirements(REPO_ROOT / "requirements.txt"),
        ),
        RequirementGroup(
            id="optional",
            label="Enhanced beat detection",
            file="requirements_optional.txt",
            required=False,
            description="Best-effort. Narrower platform support, so a "
                        "missing wheel here degrades a feature instead of "
                        "stopping the install.",
            packages=_parse_requirements(REPO_ROOT / "requirements_optional.txt"),
        ),
    ]


# ─── Streamed subprocess execution (SSE) ────────────────────────────────────
#
# Same framing convention as helpers.py::_stream / _sse_response elsewhere in
# FableGear (data: <line>\n\n, a final data: [DONE]\n\n). Reimplemented here
# rather than imported, per the module docstring: this code must not depend
# on the app's own import chain.

def stream_command(cmd: list[str], *, cwd: Path | None = None) -> Iterator[str]:
    """Run `cmd`, yielding SSE-framed output lines as they arrive."""
    yield f"data: $ {' '.join(cmd)}\n\n"
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        yield f"data: [ERROR] could not start: {exc}\n\n"
        yield "data: [DONE:1]\n\n"
        return

    assert proc.stdout is not None
    for line in proc.stdout:
        yield f"data: {line.rstrip()}\n\n"

    code = proc.wait()
    yield f"data: [DONE:{code}]\n\n"


def stream_steps(steps: list[list[str]], *, cwd: Path | None = None) -> Iterator[str]:
    """Run several commands in sequence, stopping at the first failure."""
    for cmd in steps:
        saw_failure = False
        for line in stream_command(cmd, cwd=cwd):
            if line.startswith("data: [DONE:"):
                code = line[len("data: [DONE:"):].split("]")[0]
                if code != "0":
                    saw_failure = True
            else:
                yield line
        if saw_failure:
            yield "data: [SEQUENCE_FAILED]\n\n"
            return
    yield "data: [SEQUENCE_OK]\n\n"


def brew_install_commands() -> list[list[str]]:
    brew = _find_brew() or "brew"
    cmds: list[list[str]] = []
    if not _find_brew():
        # setup.sh runs `bash -c "$(curl -fsSL URL)"` -- that idiom only works
        # because setup.sh's OWN bash performs the $(...) substitution (with
        # quoting intact) before ever invoking the inner `bash -c`. Passed
        # straight through subprocess.Popen(argv_list) there is no outer
        # shell to do that: the inner bash receives the literal, unquoted
        # text "$(curl ... )" as its own -c script, evaluates the
        # substitution itself in bare command position, and an UNQUOTED
        # substitution result gets word-split on whitespace/newlines --
        # so the first "word" of the downloaded multi-line script becomes
        # its own shebang line, and bash tries to exec a program literally
        # named "#!/bin/bash" and fails with "No such file or directory".
        # The standard curl-pipe-bash form sidesteps this: one clean argv
        # string, no nested substitution-in-command-position to word-split.
        cmds.append([
            "/bin/bash", "-c",
            "curl -fsSL "
            "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
            " | /bin/bash",
        ])
    for formula in BREW_FORMULAS:
        cmds.append([brew, "install", formula])
    return cmds


def python_install_commands() -> list[list[str]]:
    venv_python = VENV_DIR / "bin" / "python"
    py = str(venv_python) if venv_python.exists() else sys.executable
    cmds = [[py, "-m", "pip", "install", "--upgrade", "pip"]]
    for group in build_plan():
        req_path = REPO_ROOT / group.file
        if req_path.is_file():
            cmds.append([py, "-m", "pip", "install", "-r", str(req_path)])
    return cmds


# ─── Flask app ───────────────────────────────────────────────────────────────

def create_app():
    from flask import Flask, Response, jsonify, send_from_directory

    app = Flask(__name__, static_folder=None)

    @app.route("/")
    def index():
        html = TEMPLATE_PATH.read_text(encoding="utf-8")
        return Response(html, mimetype="text/html")

    @app.route("/static/<path:filename>")
    def static_files(filename):
        return send_from_directory(STATIC_DIR, filename)

    @app.route("/api/checks")
    def api_checks():
        return jsonify([asdict(c) for c in run_all_checks()])

    @app.route("/api/plan")
    def api_plan():
        return jsonify([asdict(g) for g in build_plan()])

    def _sse(gen: Iterator[str]) -> Response:
        return Response(
            gen, mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.route("/api/install/brew")
    def api_install_brew():
        return _sse(stream_steps(brew_install_commands()))

    @app.route("/api/install/python")
    def api_install_python():
        return _sse(stream_steps(python_install_commands(), cwd=REPO_ROOT))

    @app.route("/api/complete", methods=["POST"])
    def api_complete():
        (REPO_ROOT / ".fablegear_ready").touch()
        threading.Thread(target=_launch_app_and_exit, daemon=True).start()
        return jsonify({"ok": True})

    return app


def _launch_app_and_exit() -> None:
    """Hand off to the real app, then let this process end."""
    time.sleep(0.4)
    venv_python = VENV_DIR / "bin" / "python"
    py = str(venv_python) if venv_python.exists() else sys.executable
    subprocess.Popen(
        [py, str(REPO_ROOT / "main.py")],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    os._exit(0)


# ─── Entry point ───────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FableGear install wizard")
    parser.add_argument("--serve", action="store_true",
                        help="run as a plain localhost server instead of a native window")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)

    if not TEMPLATE_PATH.is_file():
        print(f"error: missing {TEMPLATE_PATH}", file=sys.stderr)
        return 2

    app = create_app()
    port = args.port or _free_port()

    if args.serve:
        print(f"FableGear install wizard: http://127.0.0.1:{port}")
        app.run(host="127.0.0.1", port=port, debug=False)
        return 0

    try:
        import webview
    except ImportError:
        print("pywebview is not installed -- falling back to --serve mode.",
              file=sys.stderr)
        print(f"Open http://127.0.0.1:{port} in a browser.", file=sys.stderr)
        app.run(host="127.0.0.1", port=port, debug=False)
        return 0

    thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    thread.start()
    time.sleep(0.3)

    webview.create_window(
        "FableGear Setup", f"http://127.0.0.1:{port}",
        width=720, height=680, resizable=False, frameless=False,
    )
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
