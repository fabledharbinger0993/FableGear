"""
Tests for fablegear_database/device_identity.py.

_RBFLTR_BYTES and _DJPROFILE_HEADER were verified byte-for-byte against
real files on a connected drive at the time this was written (not
committed to this repo — see module docstring). These tests pin the
writer's behavior; they cannot re-verify against the real files on their
own (no fixture binary is committed here either — same HONESTY LIMIT
pattern used throughout this campaign's other tests).
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fablegear_database.device_identity import (  # noqa: E402
    write_rbfltr,
    write_dj_profile,
    _RBFLTR_BYTES,
    _DJPROFILE_HEADER,
    _DJPROFILE_TOTAL_SIZE,
)


def test_write_rbfltr_is_byte_identical_to_the_template(tmp_path):
    path = write_rbfltr(tmp_path / "PIONEER")
    assert path == tmp_path / "PIONEER" / "rekordbox" / "RBFLTR.DAT"
    assert path.read_bytes() == _RBFLTR_BYTES
    assert len(path.read_bytes()) == 232


def test_write_dj_profile_default_is_fabled_branded(tmp_path):
    path = write_dj_profile(tmp_path / "PIONEER")
    data = path.read_bytes()
    assert len(data) == _DJPROFILE_TOTAL_SIZE
    assert data[:0x20] == _DJPROFILE_HEADER  # header preserved exactly
    name = data[0x20:0x30].split(b"\x00", 1)[0].decode("ascii")
    assert name == "Fabled Guthrie"


def test_write_dj_profile_custom_name(tmp_path):
    path = write_dj_profile(tmp_path / "PIONEER", display_name="DJ Fabled")
    data = path.read_bytes()
    name = data[0x20:0x30].split(b"\x00", 1)[0].decode("ascii")
    assert name == "DJ Fabled"
    # Everything past the name slot is still zero padding.
    assert data[0x30:] == b"\x00" * (_DJPROFILE_TOTAL_SIZE - 0x30)


def test_write_dj_profile_long_name_truncates_without_raising(tmp_path):
    path = write_dj_profile(tmp_path / "PIONEER", display_name="A Very Long Fabled Harbinger Name Indeed")
    data = path.read_bytes()
    assert len(data) == _DJPROFILE_TOTAL_SIZE  # file size never changes
    name_slot = data[0x20:0x30]
    assert len(name_slot) == 16
    assert name_slot[-1:] == b"\x00"  # always null-terminated
    name = name_slot.split(b"\x00", 1)[0].decode("ascii")
    assert len(name) <= 15


def test_write_dj_profile_short_name_zero_pads_slot(tmp_path):
    path = write_dj_profile(tmp_path / "PIONEER", display_name="Hi")
    data = path.read_bytes()
    assert data[0x20:0x30] == b"Hi" + b"\x00" * 14
