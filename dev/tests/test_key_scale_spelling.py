"""
D♭ minor has one Rekordbox spelling: ``Dbm``.

Rekordbox's own export names the key ``Dbm`` (ground-truth fixture, key_id 17), and
config.CAMELOT_TO_RB / OPENKEY_TO_RB already emit it. Before this was pinned, the
Camelot path returned ``Dbm`` while the alias path turned the same text into ``C#m``,
so a stick could carry two key rows for one key (and ``Dbm`` failed the canonical
guard in key_mapper._get_or_create_key_row). These tests keep every source on one spelling.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2] / "fablegear"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config
import key_mapper
from fablegear_database.onelibrary_writer import _IdAllocator


def test_every_mapping_target_is_a_canonical_scale_name():
    targets = (set(config.CAMELOT_TO_RB.values())
               | set(config.OPENKEY_TO_RB.values())
               | set(config.STANDARD_KEY_ALIASES.values()))
    assert targets - key_mapper.CANONICAL_SCALE_NAMES == set()


@pytest.mark.parametrize("raw", ["12A", "12a", "5m", "5M", "Dbm", "C#m", "C#min", "Dbmin"])
def test_d_flat_minor_normalises_to_dbm(raw):
    assert key_mapper.notation_to_scale_name(raw) == "Dbm"


def test_d_flat_minor_from_every_source_shares_one_key_row():
    # The OneLibrary writer dedupes key rows on the normalised name; one key, one ID.
    keys = _IdAllocator()
    ids = {keys.get(key_mapper.notation_to_scale_name(raw) or raw) for raw in ["12A", "5m", "Dbm", "C#m"]}
    assert len(ids) == 1
    assert keys.rows() == [(1, "Dbm")]


def test_d_sharp_minor_name_is_not_emitted_anywhere():
    assert "C#m" not in key_mapper.CANONICAL_SCALE_NAMES
    assert "C#m" not in set(config.CAMELOT_TO_RB.values()) | set(config.OPENKEY_TO_RB.values())
