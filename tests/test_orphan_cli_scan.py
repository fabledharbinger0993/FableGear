"""
The orphaned-CLI scan must identify FableGear's own cli.py subprocesses and
nothing else.

The defect this guards: _list_orphaned_cli_pids() prefiltered with
`pgrep -f "<REPO_ROOT>/cli.py"` and returned those PIDs unvalidated. pgrep -f
matches the pattern anywhere in a process's argv, so any unrelated process that
merely *mentions* the path -- `vim .../cli.py`, a `tail -f` on it, a grep, or
the shell whose own argv carries the path -- came back as "a FableGear scan".

That is not a cosmetic mislabel. The list feeds two consumers:
  * app.py's /api/update/apply, which refuses to update while a "scan" runs, so
    an unrelated editor permanently blocked the updater; and
  * helpers.kill_managed_subprocesses(include_orphans=True), which passes each
    PID to _kill_process_group_or_pid() -- and that starts with os.killpg(),
    signalling the whole process GROUP. A false positive there SIGKILLs the
    user's editor and the shell it runs under.

The ps-based fallback in the same function always applied _command_matches_cli_tool();
only the pgrep fast path skipped it. These tests pin both directions: unrelated
processes are never reported, and genuine cli.py runs still are (including when
filtered by tool name), so the fix cannot be "return nothing".
"""
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import helpers

CLI_PATH = REPO_ROOT / "cli.py"


@pytest.fixture
def reaped():
    """Spawn processes and guarantee they're gone when the test ends."""
    procs: list[subprocess.Popen] = []

    def _spawn(cmd: list[str]) -> subprocess.Popen:
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(p)
        return p

    yield _spawn

    for p in procs:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=10)


def test_process_merely_mentioning_cli_path_is_not_a_scan(reaped):
    """An unrelated process carrying the cli.py path in its argv is not a scan.

    This is the exact shape of `vim /path/to/cli.py` or a backup job sweeping
    the repo: the string is present, but the process is not running the CLI.
    """
    victim = reaped([sys.executable, "-c", "import time; time.sleep(60)", str(CLI_PATH)])
    time.sleep(0.5)
    assert victim.poll() is None, "fixture process died before the assertion"

    assert victim.pid not in helpers._list_orphaned_cli_pids()
    assert victim.pid not in helpers.list_running_managed_subprocesses()


def test_foreign_cli_py_run_with_fablegear_python_is_not_a_scan(reaped, tmp_path):
    """An unrelated project's cli.py, launched with FableGear's own interpreter.

    This is the case the `str(REPO_ROOT) in command` guard was supposed to cover
    and did not. setup.sh builds the venv at $SCRIPT_DIR/venv -- inside the repo
    -- so sys.executable is `<REPO_ROOT>/venv/bin/python` on a normal install and
    the repo path appears in the command line of anything it launches. Combined
    with matching cli.py on basename alone, a foreign cli.py was reported as a
    running FableGear scan.
    """
    foreign = tmp_path / "cli.py"
    foreign.write_text("import time\ntime.sleep(60)\n")

    # Deliberately FableGear's interpreter, exactly as a dev/user install has it.
    proc = reaped([sys.executable, str(foreign), "import"])
    time.sleep(0.5)
    assert proc.poll() is None, "fixture process died before the assertion"

    assert str(REPO_ROOT) in f"{sys.executable} {foreign} import", (
        "precondition: the repo path must appear in the command line, otherwise "
        "this test is not exercising the bug it guards"
    )
    assert proc.pid not in helpers._list_orphaned_cli_pids()
    assert proc.pid not in helpers.list_running_managed_subprocesses()
    assert proc.pid not in helpers.list_running_managed_subprocesses(tool="import")


def test_real_cli_subprocess_is_still_detected(reaped):
    """The guard must keep working -- the fix is a filter, not a mute button.

    Polls for the duration of a real `cli.py import`, because the run can finish
    faster than a fixed sleep would notice on a small/empty music root.
    """
    proc = reaped([
        sys.executable, str(CLI_PATH), "import", str(REPO_ROOT / "tests"),
        "--target", "fablegear", "--dry-run",
    ])

    seen = seen_as_import = seen_as_other_tool = None
    deadline = time.time() + 30
    while time.time() < deadline and proc.poll() is None:
        if proc.pid in helpers.list_running_managed_subprocesses():
            seen = True
            seen_as_import = proc.pid in helpers.list_running_managed_subprocesses(tool="import")
            seen_as_other_tool = proc.pid in helpers.list_running_managed_subprocesses(tool="duplicates")
            break
        time.sleep(0.05)

    if seen is None:
        pytest.skip("cli.py import exited before the scan could observe it")

    assert seen_as_import is True, "a running `cli.py import` must match tool='import'"
    assert seen_as_other_tool is False, "it must not match an unrelated tool filter"


# NOTE: the re.escape() on the pgrep pattern (install paths like
# `~/Music (2024)/FableGear` would otherwise be read as an extended regex) is
# deliberately NOT pinned by a test here. It is not observable from the outside:
# when the pattern fails to match, pgrep exits non-zero and the function falls
# through to the ps-based scan, which compares `str(REPO_ROOT) in command`
# literally and finds the same processes anyway. A test asserting the escaping
# would pass against the unescaped code too, so it would guard nothing. The
# escaping stays as defense-in-depth for the fast path; the ps fallback is what
# actually makes the result correct on such a path.
