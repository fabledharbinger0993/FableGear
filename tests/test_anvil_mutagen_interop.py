"""
Anvil <-> mutagen interoperability.

This is the migration safety net. Anvil exists to replace mutagen, and the
claim only holds if the two agree about what a file says: tags Anvil writes
must be readable by the rest of the ecosystem, and tags written by anything
else must survive an Anvil write.

Self-retiring by design. Once no module imports mutagen and it leaves
requirements.txt, importorskip turns this file into a skip rather than a
failure -- the test that guards a migration should not outlive the migration.

Deliberately asymmetric with test_anvil_id3.py: that suite proves Anvil is
self-consistent, which a confidently-wrong implementation would also pass.
This one proves Anvil is *correct*, by checking its bytes against an
independent implementation of the same spec.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

import anvil
from anvil import TrackFields

mutagen_id3 = pytest.importorskip("mutagen.id3", reason="mutagen not installed")

FAKE_MPEG = b"\xff\xfb\x90\x44" + bytes(range(256)) * 4


@pytest.fixture
def mp3(tmp_path: Path) -> Path:
    path = tmp_path / "interop.mp3"
    path.write_bytes(FAKE_MPEG)
    return path


@pytest.fixture
def wav(tmp_path: Path) -> Path:
    fmt = struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16)
    audio = b"\xaa\xbb\xcc" * 16
    body = b""
    for chunk_id, payload in [(b"fmt ", fmt), (b"data", audio)]:
        body += chunk_id + len(payload).to_bytes(4, "little") + payload
        if len(payload) & 1:
            body += b"\x00"
    path = tmp_path / "interop.wav"
    path.write_bytes(b"RIFF" + (4 + len(body)).to_bytes(4, "little") + b"WAVE" + body)
    return path


# ─── Anvil writes, mutagen reads ──────────────────────────────────────────────

@pytest.mark.parametrize("version", [3, 4])
def test_mutagen_reads_anvil_tags(mp3, version):
    anvil.write_fields(
        mp3,
        TrackFields(
            title="Midnight Drive",
            artist="Test Artist",
            album="Nocturne",
            bpm=128.5,
            initial_key="Am",
        ),
        version=version,
    )

    tag = mutagen_id3.ID3(str(mp3))
    assert tag["TIT2"].text[0] == "Midnight Drive"
    assert tag["TPE1"].text[0] == "Test Artist"
    assert tag["TALB"].text[0] == "Nocturne"
    assert tag["TKEY"].text[0] == "Am"
    # TBPM stays a spec-compliant integer for every other tool in the chain;
    # the half-BPM lives in a companion frame.
    assert tag["TBPM"].text[0] == "128"


@pytest.mark.parametrize("version", [3, 4])
def test_mutagen_reads_anvil_dj_fields(mp3, version):
    anvil.write_fields(
        mp3,
        TrackFields(
            bpm=128.5,
            mix_descriptor="Extended Mix",
            track_role="Instrumental",
            energy_level=7,
        ),
        version=version,
    )

    txxx = {f.desc: f.text[0] for f in mutagen_id3.ID3(str(mp3)).getall("TXXX")}
    assert txxx["MIXDESCRIPTOR"] == "Extended Mix"
    assert txxx["TRACKROLE"] == "Instrumental"
    assert txxx["ENERGYLEVEL"] == "7"
    assert float(txxx["BPM_PRECISE"]) == pytest.approx(128.5)


@pytest.mark.parametrize(
    "version,text",
    [
        (3, "日本語"),          # beyond latin-1: v2.3 must use UTF-16
        (4, "日本語"),          # beyond latin-1: v2.4 may use UTF-8
        (3, "Caf\xe9 M\xfcnster"),          # fits latin-1
        (4, "Caf\xe9 M\xfcnster"),
    ],
)
def test_mutagen_reads_anvil_unicode(mp3, version, text):
    """
    The encoding-byte choice is only correct if an independent reader recovers
    the original string. Picking UTF-8 on a v2.3 tag would pass a self-round-trip
    and fail here, which is exactly why this test exists.
    """
    anvil.write_fields(mp3, TrackFields(title=text), version=version)
    assert mutagen_id3.ID3(str(mp3))["TIT2"].text[0] == text


def test_mutagen_reads_anvil_wav_tags(wav):
    """WAV carries ID3 inside a RIFF chunk -- the case scanner.py distrusts today."""
    anvil.write_fields(wav, TrackFields(title="Wav Title", bpm=126.0))

    wave = pytest.importorskip("mutagen.wave")
    tag = wave.WAVE(str(wav))
    assert tag["TIT2"].text[0] == "Wav Title"
    assert tag["TBPM"].text[0] == "126"


# ─── mutagen writes, Anvil reads ──────────────────────────────────────────────

@pytest.mark.parametrize("version", [3, 4])
def test_anvil_reads_mutagen_tags(mp3, version):
    tag = mutagen_id3.ID3()
    tag.add(mutagen_id3.TIT2(encoding=3, text=["Written By Mutagen"]))
    tag.add(mutagen_id3.TPE1(encoding=3, text=["M Artist"]))
    tag.add(mutagen_id3.TBPM(encoding=3, text=["174"]))
    tag.add(mutagen_id3.TKEY(encoding=3, text=["Gm"]))
    tag.add(mutagen_id3.TXXX(encoding=3, desc="ENERGYLEVEL", text=["9"]))
    tag.save(str(mp3), v2_version=version)

    fields = anvil.read_fields(mp3)
    assert fields.title == "Written By Mutagen"
    assert fields.artist == "M Artist"
    assert fields.bpm == pytest.approx(174.0)
    assert fields.initial_key == "Gm"
    assert fields.energy_level == 9


def test_anvil_write_keeps_file_readable_by_mutagen(mp3):
    """An Anvil write must not leave a tag only Anvil can parse."""
    tag = mutagen_id3.ID3()
    tag.add(mutagen_id3.TIT2(encoding=3, text=["Original"]))
    tag.add(mutagen_id3.TCON(encoding=3, text=["Techno"]))
    tag.save(str(mp3))

    anvil.write_fields(mp3, TrackFields(bpm=140.0))

    after = mutagen_id3.ID3(str(mp3))
    assert after["TIT2"].text[0] == "Original"
    assert after["TCON"].text[0] == "Techno"   # untouched frame survived
    assert after["TBPM"].text[0] == "140"


def test_anvil_respects_mutagen_written_values(mp3):
    """Merge semantics apply to values another tool wrote, not just our own."""
    tag = mutagen_id3.ID3()
    tag.add(mutagen_id3.TBPM(encoding=3, text=["120"]))
    tag.save(str(mp3))

    result = anvil.write_fields(mp3, TrackFields(bpm=175.0))

    assert result.written == {}
    assert result.kept["bpm"] == pytest.approx(120.0)
    assert mutagen_id3.ID3(str(mp3))["TBPM"].text[0] == "120"
