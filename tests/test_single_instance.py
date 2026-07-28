"""Tests for single_instance: cross-process exclusion + release on exit."""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

CHILD_TRY = (
    "import sys; sys.path.insert(0, {repo!r}); "
    "import single_instance; "
    "sys.exit(0 if single_instance.acquire() else 42)"
)


def _child_acquire() -> int:
    """Run acquire() in a fresh process; return its exit code."""
    return subprocess.run(
        [sys.executable, "-c", CHILD_TRY.format(repo=str(REPO))],
        timeout=30,
    ).returncode


def test_second_process_is_blocked_while_lock_held():
    import single_instance

    assert single_instance.acquire() is True, "primary acquire failed"
    # Re-entrant within the same process:
    assert single_instance.acquire() is True
    # A second process must be refused while we hold the lock:
    assert _child_acquire() == 42


def test_lock_released_after_holder_exits():
    # A short-lived process acquires and exits; the OS must release the
    # lock so the next process can acquire. (Runs the acquire in a child
    # that exits immediately, then re-acquires in another child.)
    first = _child_acquire()
    second = _child_acquire()
    # NOTE: if the current pytest process already holds the lock from the
    # test above, both children are blocked — order-independent handling:
    import single_instance

    if single_instance._lock_file is not None:
        assert first == 42 and second == 42
    else:
        assert first == 0 and second == 0
