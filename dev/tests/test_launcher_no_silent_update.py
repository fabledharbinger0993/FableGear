"""launch.sh must never change code on disk or upgrade packages by itself.

It used to hard-reset a source checkout to GitHub's latest release tag and run
``pip install --upgrade`` on every launch, unprompted and unsigned, so a
compromised GitHub account or release tag could run attacker code on every
user's machine. Updating is now an explicit user action (the in-app banner and
Update button, or a newer installer). These tests pin that.
"""

import subprocess
from pathlib import Path

import pytest

LAUNCH = Path(__file__).resolve().parents[2] / "fablegear" / "launch.sh"


def _code_lines():
    return [
        line for line in LAUNCH.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]


@pytest.mark.parametrize("needle", [
    "git reset",
    "git merge",
    "git pull",
    "git fetch",
    "git stash",
    "pip install --upgrade",
    "releases/latest",
    "curl ",
])
def test_launcher_does_not_self_update(needle):
    offenders = [line.strip() for line in _code_lines() if needle in line]
    assert not offenders, f"launch.sh must not self-update; found {needle!r}: {offenders}"


def test_launcher_is_valid_bash():
    result = subprocess.run(["bash", "-n", str(LAUNCH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
