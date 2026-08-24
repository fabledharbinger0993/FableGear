"""
fablegear / single_instance.py

Cross-platform single-instance guard.

Holds an exclusive OS-level lock on ~/.fablegear/fablegear.lock for the
lifetime of the process. The lock is released by the OS automatically when
the process exits, crashes, or is killed — so there is no stale-pidfile
problem and no cleanup code to forget.

macOS / Linux : fcntl.flock  (LOCK_EX | LOCK_NB)
Windows       : msvcrt.locking (LK_NBLCK)

Why not the port-5001 probe alone?  Two launches racing the probe can both
see "no server" and both boot; and a window-only second process lingers
after the probe passes. An OS lock is atomic and race-free.
"""

import os
import sys
from pathlib import Path

_LOCK_PATH = Path.home() / ".fablegear" / "fablegear.lock"

# Module-level reference: the file object MUST stay alive for the whole
# process, otherwise GC closes it and the OS releases the lock.
_lock_file = None


def acquire() -> bool:
    """Attempt to become the primary FableGear instance.

    Returns True if the lock was acquired (or already held by this
    process). Returns False if another live process holds it.

    The lock is intentionally never released manually — the OS drops it
    at process exit, including on crash or SIGKILL.
    """
    global _lock_file
    if _lock_file is not None:
        return True

    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    f = open(_LOCK_PATH, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt  # noqa: PLC0415
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # noqa: PLC0415
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False

    # Best-effort: record our PID for humans debugging a stuck lock.
    try:
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
    except OSError:
        pass

    _lock_file = f
    return True


def lock_path() -> Path:
    """Expose the lock location for log messages and tests."""
    return _LOCK_PATH
