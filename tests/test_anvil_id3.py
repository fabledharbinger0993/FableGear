"""
Anvil ID3v2 tests -- container family A (MP3, WAV, AIFF).

These tests are deliberately self-contained: no config import, no pyrekordbox,
no audio codec. Anvil is a standalone library and its suite says so.

The property under test throughout is that Anvil changes exactly the tag bytes
it means to and nothing else. Audio payloads and unrelated chunks are compared
byte-for-byte before and after every write.
"""

import struct
from pathlib import Path

import pytest

import anvil
from anvil import TrackFields, containers, id3
from anvil.errors import UnsupportedFormat, WriteVerificationFailed

# ─── Fixture builders ─────────────────────────────────────────────────────────
#
# Real containers, synthesized. The audio payloads are not decodable streams --
# they are recognizable byte patterns, which is what lets a test assert the
# audio came through a tag write untouched.

FAKE_MPEG = b"\xff\xfb\x90\x44" + bytes(range(256)) * 4


def make_mp3(tmp_path: Path, name: str = "t.mp3", tag: bytes = b"") -> Path:
    path = tmp_path / name
    path.write_bytes(tag + FAKE_MPEG)
    return path


def _chunked(container: bytes, form: bytes, chunks, endian: str) -> bytes:
    body = b""
    for cid, payload in chunks:
        body += cid + len(payload).to_bytes(4, endian) + payload
        if len(payload) & 1:
            body += b"\x00"
    return container + (len(form) + len(body)).to_bytes(4, endian) + form + body


def make_wav(tmp_path: Path, name: str = "t.wav", odd: bool = False) -> Path:
    fmt = struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16)
    # An odd-length data chunk exercises RIFF word-alignment padding, which is
    # the detail a naive chunk writer gets wrong.
    audio = b"\xaa\xbb\xcc" * (17 if odd else 16)
    path = tmp_path / name
    path.write_bytes(_chunked(b"RIFF", b"WAVE", [(b"fmt ", fmt), (b"data", audio)], "little"))
    return path


def make_aiff(tmp_path: Path, name: str = "t.aiff") -> Path:
    comm = struct.pack(">hIh", 2, 1000, 16) + b"\x40\x0e\xac\x44\x00\x00\x00\x00\x00\x00"
    ssnd = struct.pack(">II", 0, 0) + b"\x11\x22\x33\x44" * 20
    path = tmp_path / name
    path.write_bytes(_chunked(b"FORM", b"AIFF", [(b"COMM", comm), (b"SSND", ssnd)], "big"))
    return path


ALL_BUILDERS = {"mp3": make_mp3, "wav": make_wav, "aiff": make_aiff}


def audio_signature(path: Path) -> list[tuple[bytes, bytes]]:
    """Everything in the file that is not the ID3 tag, for before/after diffing."""
    data = path.read_bytes()
    kind = containers.sniff(data)
    if kind == "mp3":
        blob = containers.extract_id3(data, kind)
        return [(b"audio", data[len(blob):] if blob else data)]
    endian = "little" if kind == "wav" else "big"
    return [c for c in containers._parse_chunks(data, endian) if c[0].lower() != b"id3 "]


# ─── Synchsafe integers ───────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [0, 1, 127, 128, 255, 256, 100_000, 268_435_455])
def test_synchsafe_round_trip(value):
    assert id3.synchsafe_decode(id3.synchsafe_encode(value)) == value


def test_synchsafe_never_sets_high_bit():
    """The whole point of the encoding: no byte may look like an MPEG sync."""
    for value in range(0, 300_000, 997):
        assert all(b & 0x80 == 0 for b in id3.synchsafe_encode(value))


def test_synchsafe_rejects_oversized_value():
    with pytest.raises(ValueError):
        id3.synchsafe_encode(1 << 28)


def test_synchsafe_strict_rejects_high_bit():
    with pytest.raises(anvil.CorruptHeader):
        id3.synchsafe_decode(b"\x00\x00\x00\xff", strict=True)


# ─── Round trips across every container ───────────────────────────────────────

@pytest.mark.parametrize("kind", ["mp3", "wav", "aiff"])
@pytest.mark.parametrize("version", [3, 4])
def test_round_trip(tmp_path, kind, version):
    path = ALL_BUILDERS[kind](tmp_path)
    fields = TrackFields(
        title="Midnight Drive",
        artist="Test Artist",
        album="Test Album",
        bpm=128.0,
        initial_key="Am",
        mix_descriptor="Extended Mix",
        track_role="Instrumental",
        energy_level=7,
        downbeat_offset=0.512,
        time_signature="4/4",
    )
    result = anvil.write_fields(path, fields, version=version)

    assert result.container == kind
    assert result.verified
    assert set(result.written) == set(fields.present())

    back = anvil.read_fields(path)
    assert back.title == "Midnight Drive"
    assert back.artist == "Test Artist"
    assert back.album == "Test Album"
    assert back.bpm == pytest.approx(128.0)
    assert back.initial_key == "Am"
    assert back.mix_descriptor == "Extended Mix"
    assert back.track_role == "Instrumental"
    assert back.energy_level == 7
    assert back.downbeat_offset == pytest.approx(0.512)
    assert back.time_signature == "4/4"


@pytest.mark.parametrize("kind", ["mp3", "wav", "aiff"])
def test_audio_survives_write_byte_exact(tmp_path, kind):
    """A tag write must not touch a single byte of audio or any other chunk."""
    path = ALL_BUILDERS[kind](tmp_path)
    before = audio_signature(path)

    anvil.write_fields(path, TrackFields(title="One"))
    anvil.write_fields(path, TrackFields(bpm=174.0))
    anvil.write_fields(path, TrackFields(title="Two"), force=True)

    assert audio_signature(path) == before


def test_wav_odd_length_chunk_keeps_alignment(tmp_path):
    """An odd-length chunk needs a pad byte that is not counted in its size."""
    path = make_wav(tmp_path, odd=True)
    before = audio_signature(path)
    anvil.write_fields(path, TrackFields(title="Odd"))

    assert audio_signature(path) == before
    data = path.read_bytes()
    assert int.from_bytes(data[4:8], "little") == len(data) - 8


def test_aiff_form_size_updated(tmp_path):
    path = make_aiff(tmp_path)
    anvil.write_fields(path, TrackFields(title="Aiff"))
    data = path.read_bytes()
    assert int.from_bytes(data[4:8], "big") == len(data) - 8


def test_rewrite_does_not_accumulate_chunks(tmp_path):
    path = make_wav(tmp_path)
    anvil.write_fields(path, TrackFields(title="A"))
    first = len(containers._parse_chunks(path.read_bytes(), "little"))
    anvil.write_fields(path, TrackFields(title="B"), force=True)
    assert len(containers._parse_chunks(path.read_bytes(), "little")) == first


def test_tagless_file_gets_a_tag(tmp_path):
    """A fresh rip with no tag block at all is an ordinary input, not an error."""
    path = make_mp3(tmp_path)
    assert anvil.read_fields(path).is_empty()

    anvil.write_fields(path, TrackFields(bpm=128.0))
    assert anvil.read_fields(path).bpm == pytest.approx(128.0)


# ─── Merge semantics ──────────────────────────────────────────────────────────

def test_write_does_not_overwrite_existing_field(tmp_path):
    path = make_mp3(tmp_path)
    anvil.write_fields(path, TrackFields(bpm=128.0))

    result = anvil.write_fields(path, TrackFields(bpm=999.0))

    assert result.written == {}
    assert result.kept == {"bpm": pytest.approx(128.0)}
    assert anvil.read_fields(path).bpm == pytest.approx(128.0)


def test_force_overwrites(tmp_path):
    path = make_mp3(tmp_path)
    anvil.write_fields(path, TrackFields(bpm=128.0))
    anvil.write_fields(path, TrackFields(bpm=174.0), force=True)
    assert anvil.read_fields(path).bpm == pytest.approx(174.0)


def test_selective_force_leaves_other_fields_alone(tmp_path):
    """force={'bpm'} is the library-native form of force_bpm."""
    path = make_mp3(tmp_path)
    anvil.write_fields(path, TrackFields(bpm=128.0, initial_key="Am"))

    result = anvil.write_fields(
        path, TrackFields(bpm=174.0, initial_key="Gm"), force={"bpm"}
    )

    assert set(result.written) == {"bpm"}
    assert set(result.kept) == {"initial_key"}
    back = anvil.read_fields(path)
    assert back.bpm == pytest.approx(174.0)
    assert back.initial_key == "Am"


def test_field_level_merge_preserves_untouched_fields(tmp_path):
    """Writing bpm touches bpm. A hand-set energy_level must survive."""
    path = make_mp3(tmp_path)
    anvil.write_fields(
        path, TrackFields(title="Keep Me", energy_level=9, mix_descriptor="VIP")
    )
    anvil.write_fields(path, TrackFields(bpm=140.0))

    back = anvil.read_fields(path)
    assert back.title == "Keep Me"
    assert back.energy_level == 9
    assert back.mix_descriptor == "VIP"
    assert back.bpm == pytest.approx(140.0)


def test_no_op_write_leaves_file_untouched(tmp_path):
    """Nothing to write means no rewrite -- identical bytes, not just equal ones."""
    path = make_mp3(tmp_path)
    anvil.write_fields(path, TrackFields(title="Same"))
    before = path.read_bytes()

    result = anvil.write_fields(path, TrackFields(title="Different"))

    assert result.written == {}
    assert not result.changed
    assert path.read_bytes() == before


def test_none_never_clears_a_field(tmp_path):
    """A partially-populated TrackFields must not blank what it omits."""
    path = make_mp3(tmp_path)
    anvil.write_fields(path, TrackFields(title="Held", artist="Also Held"))
    anvil.write_fields(path, TrackFields(bpm=128.0))

    back = anvil.read_fields(path)
    assert back.title == "Held"
    assert back.artist == "Also Held"


def test_clear_fields_is_explicit(tmp_path):
    path = make_mp3(tmp_path)
    anvil.write_fields(path, TrackFields(title="Gone", bpm=128.0))

    anvil.clear_fields(path, ["bpm"])

    back = anvil.read_fields(path)
    assert back.bpm is None
    assert back.title == "Gone"


# ─── Text encoding correctness ────────────────────────────────────────────────

def test_v23_uses_utf16_not_utf8_for_unicode(tmp_path):
    """
    v2.3 has no UTF-8. Writing encoding byte 0x03 into a v2.3 tag produces a
    file some readers render as mojibake -- the exact bug a version-blind
    writer ships. Japanese is used here because it is genuinely outside
    latin-1; accented Western European text is not (see the latin-1 test).
    """
    path = make_mp3(tmp_path)
    anvil.write_fields(path, TrackFields(title="\u65e5\u672c\u8a9e"), version=3)

    _kind, tag, _data = anvil.read_tag(path)
    assert tag.version == 3
    assert tag.get("TIT2").data[0] == id3.ENC_UTF16_BOM
    assert anvil.read_fields(path).title == "\u65e5\u672c\u8a9e"


def test_v24_uses_utf8_for_unicode(tmp_path):
    path = make_mp3(tmp_path)
    anvil.write_fields(path, TrackFields(title="\u65e5\u672c\u8a9e"), version=4)

    _kind, tag, _data = anvil.read_tag(path)
    assert tag.get("TIT2").data[0] == id3.ENC_UTF8
    assert anvil.read_fields(path).title == "\u65e5\u672c\u8a9e"


def test_ascii_prefers_latin1(tmp_path):
    """Plain ASCII should not pay for a Unicode encoding it does not need."""
    path = make_mp3(tmp_path)
    anvil.write_fields(path, TrackFields(title="Plain"), version=4)
    _kind, tag, _data = anvil.read_tag(path)
    assert tag.get("TIT2").data[0] == id3.ENC_LATIN1


@pytest.mark.parametrize("version", [3, 4])
def test_accented_text_stays_latin1(tmp_path, version):
    """
    "Cafe Munster" with accents fits latin-1, so it should use the compact
    single-byte encoding rather than doubling the frame with UTF-16. Artist
    names in this range are extremely common in a DJ library.
    """
    path = make_mp3(tmp_path)
    anvil.write_fields(path, TrackFields(title="Caf\xe9 M\xfcnster"), version=version)

    _kind, tag, _data = anvil.read_tag(path)
    assert tag.get("TIT2").data[0] == id3.ENC_LATIN1
    assert anvil.read_fields(path).title == "Caf\xe9 M\xfcnster"


@pytest.mark.parametrize("text", ["日本語のタイトル", "Ελληνικά", "emoji \U0001f3a7", "Ñoño"])
def test_unicode_round_trip(tmp_path, text):
    path = make_mp3(tmp_path)
    anvil.write_fields(path, TrackFields(title=text))
    assert anvil.read_fields(path).title == text


def test_existing_tag_keeps_its_version(tmp_path):
    """Silently upgrading someone's v2.3 tag could break their other tools."""
    path = make_mp3(tmp_path)
    anvil.write_fields(path, TrackFields(title="A"), version=3)
    anvil.write_fields(path, TrackFields(bpm=128.0))

    _kind, tag, _data = anvil.read_tag(path)
    assert tag.version == 3


# ─── BPM precision ────────────────────────────────────────────────────────────

def test_fractional_bpm_survives(tmp_path):
    """
    Half a BPM drifts a full beat within a few bars. TBPM is integer per spec,
    so precision has to live somewhere -- but TBPM must stay spec-compliant for
    every other tool that reads it.
    """
    path = make_mp3(tmp_path)
    anvil.write_fields(path, TrackFields(bpm=128.5))

    assert anvil.read_fields(path).bpm == pytest.approx(128.5)

    _kind, tag, _data = anvil.read_tag(path)
    assert id3.decode_text(tag.get("TBPM").data) == "128"


def test_db_companion_keeps_centi_bpm(tmp_path):
    """round(bpm * 100) lives in one place, and 128.5 must not become 12800."""
    path = make_mp3(tmp_path)
    result = anvil.write_fields(path, TrackFields(bpm=128.5, initial_key="Am"))

    assert result.db_companion == {"BPM": 12850, "key_notation": "Am"}
    assert result.sync_state == anvil.SYNC_FILE_ONLY


def test_third_party_tbpm_rewrite_wins_over_stale_precise(tmp_path):
    """
    If another tool rewrites TBPM alone, our stored precise value is stale and
    must not override what the file now plainly says.
    """
    path = make_mp3(tmp_path)
    anvil.write_fields(path, TrackFields(bpm=128.5))

    _kind, tag, data = anvil.read_tag(path)
    tag.set("TBPM", id3.encode_text("174", tag.version))
    path.write_bytes(containers.install_id3(data, "mp3", id3.serialize_tag(tag)))

    assert anvil.read_fields(path).bpm == pytest.approx(174.0)


def test_decimal_in_tbpm_is_tolerated(tmp_path):
    """Plenty of DJ software writes a decimal despite the spec. Read it anyway."""
    path = make_mp3(tmp_path)
    tag = id3.ID3Tag(4)
    tag.set("TBPM", id3.encode_text("128.5", 4))
    path.write_bytes(containers.install_id3(path.read_bytes(), "mp3", id3.serialize_tag(tag)))

    assert anvil.read_fields(path).bpm == pytest.approx(128.5)


# ─── Real-world malformed input ───────────────────────────────────────────────

def test_v24_frame_with_plain_uint32_size_is_recovered():
    """
    A known family of encoders writes plain uint32 frame sizes into tags they
    label v2.4. Size 300 is the nasty case: as plain bytes it still looks like
    a legal synchsafe integer, but decodes to 172 and walks the parser into
    garbage. Anvil arbitrates on where each reading lands.
    """
    payload = id3.encode_text("x" * 298, 4)
    assert len(payload) == 299

    good = id3.ID3Tag(4)
    good.set("TIT2", payload)
    correct = id3.serialize_tag(good, padding=0)

    body = bytearray(correct[10:])
    body[4:8] = struct.pack(">I", len(payload))  # plain, not synchsafe
    broken = correct[:10] + bytes(body)

    assert id3.synchsafe_decode(struct.pack(">I", len(payload))) != len(payload)

    parsed = id3.parse_tag(broken)
    assert len(parsed.frames) == 1
    assert id3.decode_text(parsed.get("TIT2").data) == "x" * 298


def test_unsynchronised_tag_is_decoded():
    tag = id3.ID3Tag(3)
    tag.set("TIT2", id3.encode_text("\xff\xfe test", 3))
    plain = id3.serialize_tag(tag, padding=0)

    body = plain[10:].replace(b"\xff", b"\xff\x00")
    header = plain[:6] + id3.synchsafe_encode(len(body))
    header = header[:5] + bytes([0x80]) + header[6:]   # set the unsync flag

    parsed = id3.parse_tag(header + body)
    assert id3.decode_text(parsed.get("TIT2").data) == "\xff\xfe test"


def test_deunsynchronise_is_a_straight_substitution():
    assert id3.deunsynchronise(b"\xff\x00\xfb") == b"\xff\xfb"
    assert id3.deunsynchronise(b"\x01\x02") == b"\x01\x02"


def test_id3v22_is_rejected_explicitly():
    """v2.2 has three-character frame IDs; parsing it as v2.3 misreads silently."""
    blob = b"ID3" + bytes([2, 0, 0]) + id3.synchsafe_encode(0)
    with pytest.raises(UnsupportedFormat, match=r"v2\.2"):
        id3.parse_tag(blob)


def test_garbage_is_unsupported_not_corrupt(tmp_path):
    path = tmp_path / "junk.bin"
    path.write_bytes(b"not audio at all, just some bytes")
    with pytest.raises(UnsupportedFormat):
        anvil.read_fields(path)


def test_truncated_frame_keeps_earlier_frames():
    """One malformed frame at the end should not cost the frames before it."""
    tag = id3.ID3Tag(4)
    tag.set("TIT2", id3.encode_text("Survivor", 4))
    tag.set("TPE1", id3.encode_text("Artist", 4))
    blob = id3.serialize_tag(tag, padding=0)

    truncated = blob[:-4]
    parsed = id3.parse_tag(truncated)
    assert id3.decode_text(parsed.get("TIT2").data) == "Survivor"


def test_extension_is_not_trusted_over_bytes(tmp_path):
    """A .wav that is really an MP3 must be handled as an MP3."""
    path = tmp_path / "liar.wav"
    path.write_bytes(FAKE_MPEG)
    anvil.write_fields(path, TrackFields(title="Actually MP3"))
    assert containers.sniff(path.read_bytes()) == "mp3"
    assert anvil.read_fields(path).title == "Actually MP3"


# ─── Third-party tag coexistence ──────────────────────────────────────────────

def test_foreign_txxx_frames_are_preserved(tmp_path):
    """
    TXXX frames are unique per description, not per frame id. A blanket
    remove('TXXX') would delete every user-defined field another tool wrote.
    """
    path = make_mp3(tmp_path)
    tag = id3.ID3Tag(4)
    tag.frames.append(id3.Frame("TXXX", id3.encode_txxx("REPLAYGAIN_TRACK_GAIN", "-6.5 dB", 4)))
    path.write_bytes(containers.install_id3(path.read_bytes(), "mp3", id3.serialize_tag(tag)))

    anvil.write_fields(path, TrackFields(energy_level=8, mix_descriptor="Dub"))

    _kind, after, _data = anvil.read_tag(path)
    descriptions = {id3.decode_txxx(f.data)[0] for f in after.get_all("TXXX")}
    assert "REPLAYGAIN_TRACK_GAIN" in descriptions
    assert anvil.read_fields(path).energy_level == 8


def test_unknown_frames_are_preserved(tmp_path):
    """Frames Anvil has no opinion about must round-trip untouched."""
    path = make_mp3(tmp_path)
    tag = id3.ID3Tag(4)
    tag.set("TCON", id3.encode_text("Techno", 4))
    tag.set("COMM", b"\x00eng\x00a comment")
    path.write_bytes(containers.install_id3(path.read_bytes(), "mp3", id3.serialize_tag(tag)))

    anvil.write_fields(path, TrackFields(bpm=130.0))

    _kind, after, _data = anvil.read_tag(path)
    assert id3.decode_text(after.get("TCON").data) == "Techno"
    assert after.get("COMM").data == b"\x00eng\x00a comment"


def test_repeated_writes_do_not_stack_frames(tmp_path):
    """Without delall-before-set, a second write leaves two TBPM frames."""
    path = make_mp3(tmp_path)
    for bpm in (120.0, 130.0, 140.0):
        anvil.write_fields(path, TrackFields(bpm=bpm), force=True)

    _kind, tag, _data = anvil.read_tag(path)
    assert len(tag.get_all("TBPM")) == 1
    assert len([f for f in tag.get_all("TXXX")
                if id3.decode_txxx(f.data)[0] == "BPM_PRECISE"]) == 1


def test_cover_art_extraction(tmp_path):
    path = make_mp3(tmp_path)
    image = b"\xff\xd8\xff\xe0JFIF-ish payload"
    apic = b"\x00" + b"image/jpeg" + b"\x00" + b"\x03" + b"cover" + b"\x00" + image

    tag = id3.ID3Tag(4)
    tag.set("APIC", apic)
    path.write_bytes(containers.install_id3(path.read_bytes(), "mp3", id3.serialize_tag(tag)))

    assert anvil.read_cover_art(path) == (image, "image/jpeg")


# ─── Write safety ─────────────────────────────────────────────────────────────

def test_failed_verification_raises(tmp_path, monkeypatch):
    path = make_mp3(tmp_path)

    monkeypatch.setattr(anvil.api, "read_fields", lambda _p: TrackFields(bpm=1.0))
    with pytest.raises(WriteVerificationFailed):
        anvil.write_fields(path, TrackFields(bpm=128.0))


def test_write_leaves_no_temp_files(tmp_path):
    path = make_mp3(tmp_path)
    anvil.write_fields(path, TrackFields(title="Clean"))
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".anvil-")]
    assert leftovers == []


def test_checkpoint_fires_only_after_success(tmp_path):
    seen = []
    path = make_mp3(tmp_path)

    anvil.write_fields(path, TrackFields(bpm=128.0), checkpoint=seen.append)
    assert seen == [path]

    # A no-op write has nothing to undo and must not create a checkpoint.
    anvil.write_fields(path, TrackFields(bpm=999.0), checkpoint=seen.append)
    assert seen == [path]


def test_checkpoint_failure_does_not_fail_the_write(tmp_path):
    """The bytes on disk are correct; an undo-log problem is not a write error."""
    path = make_mp3(tmp_path)

    def boom(_p):
        raise RuntimeError("checkpoint store unavailable")

    result = anvil.write_fields(path, TrackFields(bpm=128.0), checkpoint=boom)
    assert result.written["bpm"] == 128.0
    assert anvil.read_fields(path).bpm == pytest.approx(128.0)


def test_file_mode_is_preserved(tmp_path):
    path = make_mp3(tmp_path)
    path.chmod(0o640)
    anvil.write_fields(path, TrackFields(title="Modes"))
    assert path.stat().st_mode & 0o777 == 0o640


def test_orphan_cleanup(tmp_path):
    from anvil.safety import cleanup_orphans

    (tmp_path / ".anvil-abc.tmp").write_bytes(b"junk")
    (tmp_path / "keep.mp3").write_bytes(b"keep")

    assert cleanup_orphans(tmp_path) == 1
    assert (tmp_path / "keep.mp3").exists()
