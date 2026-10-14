import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_repository_does_not_ship_vscode_workspace_configuration():
    for name in ("tasks.json", "settings.json", "launch.json"):
        assert not (ROOT / ".vscode" / name).exists()
    assert ".vscode/" in (ROOT / ".gitignore").read_text()


def test_setup_and_launcher_do_not_mutate_homebrew_automatically():
    brew_mutation = re.compile(
        r'(?m)^\s*(?:brew|_brew|"\$BREW")\s+(?:update|upgrade|install)\b'
    )
    for script in ("setup.sh", "launch.sh"):
        assert not brew_mutation.search((ROOT / script).read_text())


def test_launcher_runs_pip_only_through_the_venv_interpreter():
    """Every executable pip-install line must use the venv's own interpreter.

    A bare `pip` or `python3 -m pip` resolves through PATH and could install into the
    system or Homebrew Python.
    """
    venv_pip = '"$VENV/bin/python" -m pip install'
    offenders = [
        line.strip()
        for line in (ROOT / "launch.sh").read_text().splitlines()
        if not line.lstrip().startswith("#")
        and re.search(r"\bpip\s+install\b", line)
        and venv_pip not in line
    ]
    assert not offenders, offenders


def test_no_phone_companion_or_vpn_code_ships():
    """The FableGo phone companion and Tailscale integration were removed entirely.

    They live in their own repository now; nothing here may bring them back. Docs,
    audits and this test file are exempt (history may mention them).
    """
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "*.py", "*.sh", "*.js", "*.css", "*.html", "*.txt", "*.toml"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    pattern = re.compile(r"fablego|tailscale|routes_mobile|ws_bus", re.IGNORECASE)
    offenders = []
    for rel in tracked:
        if rel.startswith(("docs/", "tests/", "venv/")):
            continue
        path = ROOT / rel
        if path.is_file() and pattern.search(path.read_text(errors="ignore")):
            offenders.append(rel)
    assert offenders == []


def test_server_binds_loopback_unless_owner_opts_in():
    source = (ROOT / "main.py").read_text()
    assert "_HOST = '0.0.0.0' if _lan_ui_enabled() else '127.0.0.1'" in source
