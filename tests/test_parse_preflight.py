"""
Tests for the export parse pre-flight (`_parsed_status` / `_parse_preflight`).

A track is "parsed" only when it has BOTH a beat grid (in the DB) and a waveform
cache (on disk). These pin that a track missing either piece is reported as
unparsed, and that ``--require-parsed`` turns that into a hard abort.
"""

import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Inject a fake waveform_generator BEFORE importing cli, so the pre-flight's
# `import waveform_generator` resolves to our stub (no real DSP / cache files).
_FAKE_WG = types.ModuleType("waveform_generator")
_WAVE_CACHE = {1: {"PWV3": b"x"}}  # only content id 1 has cached waveforms
_FAKE_WG.load_waveform_cache = lambda cid: _WAVE_CACHE.get(cid)
sys.modules["waveform_generator"] = _FAKE_WG

import cli


class _Track:
    def __init__(self, i, name):
        self.id = i
        self.file_name = name


class _FakeDB:
    """Three tracks; `grids` maps content-id -> a truthy grid list."""
    def __init__(self, grids):
        self._grids = grids
        self._tracks = [_Track(1, "one.mp3"), _Track(2, "two.mp3"), _Track(3, "three.mp3")]

    def get_content_with_relations(self, ids):
        if ids is None:
            return list(self._tracks)
        return [t for t in self._tracks if t.id in ids]

    def get_beatgrid_for_content(self, cid):
        return self._grids.get(cid, [])


def test_parsed_status_flags_missing_grid_or_waveforms():
    # id1: grid + waveforms → parsed. id2: grid only. id3: neither.
    db = _FakeDB({1: ["beat"], 2: ["beat"]})
    total, unparsed = cli._parsed_status(db, None)
    assert total == 3
    by_id = {u[0]: u[2] for u in unparsed}
    assert set(by_id) == {2, 3}
    assert by_id[2] == ["no waveforms"]
    assert set(by_id[3]) == {"no beat grid", "no waveforms"}


def test_parsed_status_all_parsed_is_empty():
    db = _FakeDB({1: ["beat"]})
    total, unparsed = cli._parsed_status(db, [1])  # only id1, which is fully parsed
    assert total == 1
    assert unparsed == []


def test_require_parsed_aborts():
    db = _FakeDB({1: ["beat"]})  # ids 2 & 3 unparsed
    with pytest.raises(SystemExit) as exc:
        cli._parse_preflight(db, None, require_parsed=True)
    assert exc.value.code == 1


def test_advisory_does_not_abort():
    db = _FakeDB({1: ["beat"]})
    # require_parsed=False → warns, returns normally (no SystemExit).
    cli._parse_preflight(db, None, require_parsed=False)
