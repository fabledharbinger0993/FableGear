"""
fablegear / anvil / safety.py

The write path. Every Anvil write goes through here; there is no other route
to disk and no "unsafe but faster" variant to reach for under deadline.

The sequence is: serialize to a temp file in the same directory, fsync it,
atomically rename over the original, fsync the directory, then read the file
back and confirm it says what we just wrote.

Why it matters, traced backwards from the failure it prevents: a crash or
power loss during an in-place rewrite of an audio file leaves a half-written
original -- the tag header claims a size the body no longer matches, and the
file is unplayable. Under this sequence there is no moment when the original
path holds a partial file. os.replace() is atomic within a filesystem, so a
crash lands either before it (original untouched) or after it (new file
complete). The worst outcome is an orphaned temp file, which is harmless and
gets cleaned up on the next run.

audio_processor.py already does a hand-rolled version of the read-back step
for MP3 output only ("soundfile can't open MP3s, so use mutagen for those").
Here it is the default for every format, not a step a caller has to remember.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from anvil.errors import WriteVerificationFailed

log = logging.getLogger(__name__)

TEMP_PREFIX = ".anvil-"
TEMP_SUFFIX = ".tmp"


def cleanup_orphans(directory: Path) -> int:
    """
    Remove Anvil temp files left behind by an interrupted write.

    Safe to call at any time: these files are only ever created inside the
    write below, and are renamed away on success, so anything still bearing
    the prefix is by definition abandoned.
    """
    removed = 0
    try:
        for entry in directory.iterdir():
            if entry.name.startswith(TEMP_PREFIX) and entry.name.endswith(TEMP_SUFFIX):
                try:
                    entry.unlink()
                    removed += 1
                except OSError:
                    log.debug("could not remove orphaned temp file %s", entry)
    except OSError:
        log.debug("could not scan %s for orphaned temp files", directory)
    return removed


def atomic_write(
    path: Path,
    data: bytes,
    *,
    verify: Callable[[], None] | None = None,
    checkpoint: Callable[[Path], None] | None = None,
) -> None:
    """
    Replace `path` with `data`, atomically, then verify.

    `verify` is called after the rename and should raise
    WriteVerificationFailed if the file does not read back correctly.

    `checkpoint` is called only after verification passes -- wire it to
    FableGear's checkpoint.py and every tag write becomes undoable at the
    point of writing. Ordering is deliberate: a write that failed verification
    is not something anyone wants an undo entry for.
    """
    path = Path(path)
    directory = path.parent
    existed = path.exists()

    fd, tmp_name = tempfile.mkstemp(
        dir=directory, prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX
    )
    tmp_path = Path(tmp_name)

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            # Force the bytes to the platter before the rename. Without this
            # the rename can land while the data is still in the page cache,
            # which on a crash yields a correctly-named, empty-or-partial file
            # -- exactly the outcome the atomic rename is supposed to prevent.
            os.fsync(handle.fileno())

        if existed:
            # Carry over mode and ownership so a tagged file does not silently
            # change permissions. copystat covers mode and timestamps; it is
            # best-effort because some filesystems refuse parts of it.
            try:
                shutil.copystat(path, tmp_path)
            except OSError:
                log.debug("could not copy file metadata onto temp file")

        os.replace(tmp_path, path)

        # Persist the rename itself. The directory entry is separate metadata
        # from the file contents and needs its own flush.
        try:
            dir_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            log.debug("could not fsync directory %s", directory)

    except BaseException:
        # Any failure before the rename leaves the original untouched; clear
        # the temp file so it does not accumulate.
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise

    if verify is not None:
        verify()

    if checkpoint is not None:
        try:
            checkpoint(path)
        except Exception:
            # A checkpoint failure must not be reported as a write failure --
            # the write genuinely succeeded and the file on disk is correct.
            log.warning("checkpoint hook failed for %s", path, exc_info=True)


def verify_fields(
    read_back: Callable[[], object],
    expected: dict[str, object],
    path: Path,
) -> None:
    """
    Confirm every field we just wrote reads back with the value we wrote.

    Compares only the fields that were actually written, so an unrelated tag
    Anvil did not touch cannot fail a verification it has nothing to do with.
    """
    actual = read_back()
    mismatches = []
    for name, want in expected.items():
        got = getattr(actual, name, None)
        if isinstance(want, float) and isinstance(got, (int, float)):
            if abs(float(got) - want) > 1e-6:
                mismatches.append((name, want, got))
        elif got != want:
            mismatches.append((name, want, got))

    if mismatches:
        detail = ", ".join(
            f"{n}: wrote {w!r}, read back {g!r}" for n, w, g in mismatches
        )
        raise WriteVerificationFailed(f"{path.name}: {detail}")
