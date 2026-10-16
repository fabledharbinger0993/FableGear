"""
Regression coverage for importer.import_directory()'s live-progress plumbing.

No existing test exercised this function at all (tests/test_fablegear_importer.py
covers the *other* importer, fablegear_database.importer). That gap let a
NameError-class regression ship: _p_count was referenced (`_p_count += 1`)
without ever being initialized, so import_directory() crashed on the first
track of every real, non-dry-run import.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import importer
from importer import TrackImportResult
from scanner import TrackInfo


def _track(name: str) -> TrackInfo:
    return TrackInfo(path=Path(f"/fake/{name}"), duration_seconds=180.0, title=name)


def _run(tracks, *, dry_run=False, resume=False):
    with (
        patch.object(importer, "scan_directory", return_value=iter(tracks)),
        patch.object(importer, "_load_progress", return_value=set()),
        patch.object(importer, "_save_progress"),
        patch.object(importer, "_clear_progress"),
        patch.object(importer, "_import_track") as mock_import,
    ):
        mock_import.side_effect = lambda track, db: TrackImportResult(path=track.path, success=True)
        db = MagicMock()
        return importer.import_directory(Path("/fake"), db, dry_run=dry_run, resume=resume)


def test_import_directory_processes_single_track_without_crashing():
    """The core regression: _p_count must be initialized before the loop."""
    report = _run([_track("a.mp3")])
    assert report.imported == 1
    assert report.failed == 0


def test_import_directory_processes_many_tracks_across_progress_interval():
    """Exercise the _p_count % PROGRESS_ITEM_INTERVAL throttle boundary directly."""
    tracks = [_track(f"t{i}.mp3") for i in range(50)]
    report = _run(tracks)
    assert report.imported == 50


def test_import_directory_dry_run_still_reports_imported():
    """dry_run continues before _p_count += 1, so it never hit the crash —
    verify it still produces a sane report."""
    report = _run([_track("a.mp3")], dry_run=True)
    assert report.imported == 1
