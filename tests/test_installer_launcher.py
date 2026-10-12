"""packaging/installer_launcher.sh: the executable inside "Install FableGear.app".

It must pick a supported Python, hand the wizard the install target, and never run the
/usr/bin/python3 shim (that pops Apple's developer-tools installer) unless the Command Line Tools
are already present.
"""
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "packaging" / "installer_launcher.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def test_launcher_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(LAUNCHER)], capture_output=True).returncode == 0


def _fake_python(path: Path, version: int, log: Path):
    """A stand-in interpreter: answers the version probe, and records any other invocation."""
    path.write_text(
        "#!/bin/bash\n"
        f'if [ "$1" = "-c" ]; then echo {version}; exit 0; fi\n'
        f'echo "$0 $@" >> "{log}"\n'
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _bundle(tmp_path: Path) -> Path:
    """Contents/MacOS/<launcher> next to Contents/Resources/payload/installer_wizard.py."""
    macos = tmp_path / "Install FableGear.app" / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    payload = macos.parent / "Resources" / "payload"
    payload.mkdir(parents=True)
    (payload / "installer_wizard.py").write_text("")
    exe = macos / "Install FableGear"
    shutil.copy(LAUNCHER, exe)
    exe.chmod(0o755)
    return exe


def _run(exe: Path, tmp_path: Path, path_dirs: list[Path], extra_env: dict | None = None):
    # Absolute fake paths: the launcher appends /usr/local/bin etc. to PATH, where a CI machine's real
    # python3.13 can live, so bare names are not hermetic.
    fakes = [str(b / n) for b in path_dirs for n in ("python3.13", "python3.12", "python3.11") if (b / n).exists()]
    env = {"PATH": ":".join(map(str, path_dirs)) + ":/usr/bin:/bin", "HOME": str(tmp_path / "home"),
           "FABLEGEAR_INSTALL_TARGET": str(tmp_path / "target dir"),
           # Hooks: don't consult real interpreters on the machine running the tests, and never
           # pop a dialog that would wait for a click.
           "FABLEGEAR_PYTHON_CANDIDATES": " ".join(fakes) or "python3.99",
           "FABLEGEAR_NO_DIALOG": "1", **(extra_env or {})}
    (tmp_path / "home").mkdir(exist_ok=True)
    return subprocess.run(["/bin/bash", str(exe)], env=env, capture_output=True, text=True, timeout=30)


def test_runs_the_wizard_with_a_supported_python_and_the_target(tmp_path):
    log = tmp_path / "calls.log"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake_python(bindir / "python3.12", 312, log)
    exe = _bundle(tmp_path)
    done = _run(exe, tmp_path, [bindir])
    assert done.returncode == 0, done.stderr
    call = log.read_text()
    assert "installer_wizard.py --target " + str(tmp_path / "target dir") in call


def test_skips_python_outside_3_11_to_3_13(tmp_path):
    log = tmp_path / "calls.log"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake_python(bindir / "python3.13", 314, log)        # too new
    _fake_python(bindir / "python3.12", 312, log)        # fine
    done = _run(_bundle(tmp_path), tmp_path, [bindir])
    assert done.returncode == 0 and "python3.12" in log.read_text()


def test_missing_payload_stops_without_running_anything(tmp_path):
    exe = _bundle(tmp_path)
    (exe.parent.parent / "Resources" / "payload" / "installer_wizard.py").unlink()
    done = _run(exe, tmp_path, [])
    assert done.returncode == 1


def test_no_supported_python_reports_and_exits_without_running_the_wizard(tmp_path):
    log = tmp_path / "calls.log"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake_python(bindir / "python3.12", 309, log)        # too old
    done = _run(_bundle(tmp_path), tmp_path, [bindir], {"FABLEGEAR_PYTHON_CANDIDATES": str(bindir / "python3.12")})
    assert done.returncode == 1 and not log.exists()
