"""
The installer ships fablegear/ and nothing else.

build_release.sh packages `git archive HEAD:fablegear`, so the boundary between "the app"
and "everything else" is the fablegear/ folder itself. These tests keep that boundary
honest in both directions: nothing development-only creeps into fablegear/, nothing the
app needs is left outside it, and the build script keeps shipping exactly that folder.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "fablegear"
REPO = APP.parent


def _tracked(prefix: str) -> list[str]:
    out = subprocess.run(["git", "ls-files", prefix], cwd=REPO, capture_output=True, text=True, check=True)
    return out.stdout.split()


def test_build_ships_only_the_fablegear_folder():
    script = (REPO / "build_release.sh").read_text()
    archives = re.findall(r"git\s+-C\s+\"\$SCRIPT_DIR\"\s+archive\s+(\S+)", script)
    assert archives == ['"HEAD:fablegear"'], archives


def test_no_development_material_inside_the_app():
    forbidden_dirs = {"tests", "test", "docs", "scripts", "experiments", "benchmarks", "fixtures", ".github"}
    forbidden_names = re.compile(
        r"(^|/)(requirements[-_](dev|build)\.txt|.*\.spec|ruff\.toml|pyrightconfig\.json|conftest\.py"
        r"|test_[^/]+\.py|CLAUDE\.md|MARKETING\.md|PRODUCT\.md|DESIGN\.md)$"
    )
    offenders = []
    for rel in _tracked("fablegear"):
        parts = Path(rel).parts[1:]
        if forbidden_dirs & set(parts[:-1]) or forbidden_names.search(rel):
            offenders.append(rel)
    assert offenders == [], offenders


def test_icon_build_inputs_stay_out_of_the_app():
    """The 2.5k-pixel source art and the .iconset intermediate are build inputs, not runtime assets."""
    tracked = _tracked("fablegear/static")
    assert "fablegear/static/fablegear.png" not in tracked
    assert not [t for t in tracked if "/FableGear.iconset/" in t]
    assert "fablegear/packaging/make_app_icons.py" not in _tracked("fablegear/packaging")


def test_repo_root_holds_no_runtime_code():
    """A .py or app asset dropped at the repo root would silently never ship."""
    allowed = {
        ".gitignore", ".mcp.json", "CLAUDE.md", "LICENSE", "README.md",
        "build_release.sh", "launch.sh", "pyrightconfig.json", "ruff.toml",
    }
    root_files = {p for p in _tracked(".") if "/" not in p}
    assert root_files <= allowed, sorted(root_files - allowed)


def test_app_code_never_reaches_into_dev():
    pattern = re.compile(r"""['"/]dev/(tests|docs|scripts|legacy|icon-source|assets)\b""")
    offenders = []
    for rel in _tracked("fablegear"):
        if not rel.endswith((".py", ".sh", ".js", ".html", ".css")):
            continue
        if pattern.search((REPO / rel).read_text(errors="ignore")):
            offenders.append(rel)
    assert offenders == [], offenders


def test_installer_entry_points_are_inside_the_app():
    for rel in ("installer_wizard.py", "main.py", "launch.sh", "setup.sh", "requirements.txt",
                "requirements_ui.txt", "requirements_optional.txt",
                "packaging/installer_launcher.sh", "packaging/create_launcher_app.sh",
                "templates/installer_wizard.html", "static/fablegear-icon-1024.png"):
        assert (APP / rel).is_file(), rel
