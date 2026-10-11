import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_repository_does_not_ship_vscode_workspace_configuration():
    for name in ("tasks.json", "settings.json", "launch.json"):
        assert not (ROOT / ".vscode" / name).exists()
    assert ".vscode/" in (ROOT / ".gitignore").read_text()


def test_setup_and_launcher_do_not_mutate_homebrew_automatically():
    brew_mutation = re.compile(
        r'(?m)^\s*(?:_brew|"\$BREW")\s+(?:update|upgrade|install)\b'
    )
    for script in ("setup.sh", "launch.sh"):
        assert not brew_mutation.search((ROOT / script).read_text())


def test_launcher_does_not_enable_tailscale_automatically():
    assert not re.search(r"(?m)^\s*tailscale\s+up\b", (ROOT / "launch.sh").read_text())
