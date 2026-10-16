"""packaging/create_launcher_app.sh must only ever replace a launcher that is really ours."""
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "fablegear" / "packaging" / "create_launcher_app.sh"
PLISTBUDDY = Path("/usr/libexec/PlistBuddy")

pytestmark = pytest.mark.skipif(not PLISTBUDDY.exists() or shutil.which("bash") is None,
                                reason="needs macOS PlistBuddy")


def _folder(tmp_path: Path) -> Path:
    folder = tmp_path / "install dir"
    folder.mkdir()
    (folder / "launch.sh").write_text("#!/bin/bash\n")
    return folder


def _existing_app(dest: Path, bundle_id: str | None) -> Path:
    app = dest / "FableGear.app"
    (app / "Contents").mkdir(parents=True)
    plist = ['<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict>']
    if bundle_id is not None:
        plist.append(f"<key>CFBundleIdentifier</key><string>{bundle_id}</string>")
    plist.append("</dict></plist>")
    (app / "Contents" / "Info.plist").write_text("".join(plist))
    (app / "marker.txt").write_text("original")
    return app


def _run(folder: Path, dest: Path):
    return subprocess.run(["bash", str(SCRIPT), str(folder), str(dest)], capture_output=True, text=True, timeout=60)


def test_creates_a_launcher_that_points_at_the_install_folder(tmp_path):
    folder, dest = _folder(tmp_path), tmp_path / "Apps"
    done = _run(folder, dest)
    assert "created" in done.stdout
    script = (dest / "FableGear.app" / "Contents" / "MacOS" / "FableGear").read_text()
    assert "install\\ dir" in script and "launch.sh" in script


@pytest.mark.parametrize("bundle_id", [None, "com.example.Other", "com.apple.ScriptEditor.id.FableGear"])
def test_leaves_a_foreign_or_unidentified_app_alone(tmp_path, bundle_id):
    folder, dest = _folder(tmp_path), tmp_path / "Apps"
    dest.mkdir()
    existing = _existing_app(dest, bundle_id)
    done = _run(folder, dest)
    assert "leaving it alone" in done.stdout and "created" not in done.stdout
    assert (existing / "marker.txt").read_text() == "original"


def test_replaces_our_own_previous_launcher(tmp_path):
    folder, dest = _folder(tmp_path), tmp_path / "Apps"
    dest.mkdir()
    existing = _existing_app(dest, "com.fabledharbinger.fablegear")
    done = _run(folder, dest)
    assert "created" in done.stdout and not (existing / "marker.txt").exists()
