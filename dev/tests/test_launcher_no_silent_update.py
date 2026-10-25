"""launch.sh must never change code on disk or upgrade packages by itself.

It used to hard-reset a source checkout to GitHub's latest release tag and run
``pip install --upgrade`` on every launch, unprompted and unsigned, so a
compromised GitHub account or release tag could run attacker code on every
user's machine. Updating is now an explicit user action (the in-app banner and
Update button, or a newer installer). These tests pin that.
"""

import re
import subprocess
from pathlib import Path

import pytest

LAUNCH = Path(__file__).resolve().parents[2] / "fablegear" / "launch.sh"


def _code_lines():
    """Non-comment lines, with backslash continuations joined so a flag moved
    to the next line cannot hide from the patterns below."""
    joined, buf = [], ""
    for line in LAUNCH.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
            continue
        joined.append(buf + line)
        buf = ""
    if buf:
        joined.append(buf)
    return joined


# Patterns, not literal strings, so equivalent spellings (``pip install -U``,
# ``git -C dir reset``, ``wget``, ``git fetch`` + ``git checkout``) are caught
# too. Read-only git (describe, rev-parse, status, log) stays allowed.
FORBIDDEN = {
    "git history/worktree mutation": (
        r"\bgit\b(?:\s+-\S+(?:\s+\S+)?)*\s+"
        r"(?:fetch|pull|merge|reset|stash|checkout|switch|clone|rebase|clean|restore|apply|am|cherry-pick|submodule)\b"
    ),
    "pip upgrade / forced reinstall": (
        r"\bpip3?\b.*\binstall\b.*(?:\s-[A-Za-z]*U[A-Za-z]*\b|--upgrade|--force-reinstall|--pre\b)"
    ),
    "pip install from a URL or VCS": r"\bpip3?\b.*\binstall\b.*(?:https?://|git\+)",
    "network download": r"\b(?:curl|wget|nc|ncat|scp|rsync)\b",
    "GitHub release lookup": r"releases/(?:latest|tags)|api\.github\.com",
    "remote script execution": r"\|\s*(?:ba|z)?sh\b|\b(?:eval|source)\b.*\$\(",
}


@pytest.mark.parametrize("name", sorted(FORBIDDEN))
def test_launcher_does_not_self_update(name):
    rx = re.compile(FORBIDDEN[name])
    offenders = [line.strip() for line in _code_lines() if rx.search(line)]
    assert not offenders, f"launch.sh must not self-update ({name}): {offenders}"


@pytest.mark.parametrize("snippet", [
    "git fetch origin",
    "git -C \"$SCRIPT_DIR\" reset --hard FETCH_HEAD",
    "git checkout v9.9.9",
    "\"$VENV/bin/python\" -m pip install -U -r req.txt",
    "pip install --upgrade foo",
    "pip install foo --force-reinstall",
    "wget -qO- https://example.com/x",
    "curl -s https://api.github.com/repos/x/y/releases/latest",
    "bash <(curl -s https://example.com/x)",
])
def test_forbidden_patterns_actually_match(snippet):
    """Guards the guard: each equivalent spelling must trip at least one pattern."""
    assert any(re.search(rx, snippet) for rx in FORBIDDEN.values()), snippet


@pytest.mark.parametrize("snippet", [
    "FABLEGEAR_VERSION=\"$(git describe --tags --abbrev=0 2>/dev/null)\"",
    "git rev-parse --short HEAD",
    "\"$VENV/bin/python\" -m pip install --quiet -r \"$SCRIPT_DIR/requirements.txt\"",
])
def test_allowed_launcher_commands_do_not_match(snippet):
    assert not any(re.search(rx, snippet) for rx in FORBIDDEN.values()), snippet


def test_launcher_is_valid_bash():
    result = subprocess.run(["bash", "-n", str(LAUNCH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
