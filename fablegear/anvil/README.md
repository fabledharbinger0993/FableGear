# Anvil

Audio tag I/O for FableGear. Replaces mutagen (GPL-2.0) with a library scoped
to exactly what FableGear reads and writes.

Anvil **reads and writes** what a track says about itself. It does not listen
to audio — tempo, beat, and key detection belong to **Iron**, a separate
package, and arrive here as ordinary candidate values. Anvil cannot tell
whether a value came from Iron or from a caller typing it in by hand, and
treats both identically.

```python
from anvil import read_fields, write_fields, TrackFields

fields = read_fields(path)

result = write_fields(path, TrackFields(bpm=128.5), force={"bpm"})
result.written        # {"bpm": 128.5}
result.db_companion   # {"BPM": 12850} — ready for master.db, never written here
result.sync_state     # "file_only"
```

## Two rules that govern every write

**Field-level merge.** Writing `bpm` touches `bpm` and nothing else. A
hand-set `energy_level` or `mix_descriptor` survives a later tempo
re-analysis untouched.

**A candidate is not an overwrite.** A value arriving for a field that already
has one is kept out unless the caller explicitly forces it — `force=True` for
everything, or `force={"bpm"}` for one field. This is the library-native form
of `audio_processor.py`'s `force_bpm` / `force_key` flags.

Passing `None` in a `TrackFields` means "not writing this", never "erase
this". Erasure is a separate call, `clear_fields(path, ["bpm"])`, so a
partially-populated `TrackFields` can never silently blank what it omits.

## Write safety

Every write: serialize to a temp file → `fsync` → atomic rename over the
original → `fsync` the directory → read the file back and confirm it says what
was written. There is no unsafe-but-faster variant.

A crash therefore lands either before the rename (original untouched) or after
it (new file complete). The worst outcome is an orphaned `.anvil-*.tmp` file,
which `safety.cleanup_orphans()` clears.

An optional `checkpoint` hook fires **after** verification passes — wire it to
`checkpoint.py` and every tag write becomes undoable at the point of writing.
A write that failed verification produces no undo entry.

## Supported today

Three container families, dispatched transparently by `read_fields()` /
`write_fields()` / `clear_fields()` from the file's own bytes (never its
extension):

| Family | Containers | Tag structure |
|---|---|---|
| A | MP3, WAV, AIFF | ID3v2.3 / ID3v2.4 |
| B | FLAC, Ogg Vorbis, Ogg Opus | Vorbis comments |
| C | M4A, M4P, MP4, M4V | MP4/iTunes atoms |

Raw ADTS AAC (`.aac`) and WavPack (`.wv`) have no writer yet — see Known
limitations.

**Family A (ID3v2), MP3/WAV/AIFF:**

| Field | ID3 representation |
|---|---|
| `title` / `artist` / `album` | `TIT2` / `TPE1` / `TALB` — native |
| `initial_key` | `TKEY` — native |
| `bpm` | `TBPM` (spec-compliant integer) + `TXXX:BPM_PRECISE` |
| `mix_descriptor` / `track_role` / `energy_level` | `TXXX` — non-native |
| `downbeat_offset` / `time_signature` | `TXXX` — non-native |
| cover art (read) | `APIC` |

**Family B (Vorbis comments), FLAC/Ogg Vorbis/Ogg Opus:**

| Field | Comment key |
|---|---|
| `title` / `artist` / `album` | `TITLE` / `ARTIST` / `ALBUM` |
| `bpm` | `bpm` — full precision, no companion field needed (unlike ID3/MP4, a Vorbis comment isn't spec-typed to an integer) |
| `initial_key` | `initialkey` — matches the spelling `audio_processor.py`'s mutagen-based writer already used |
| `mix_descriptor` / `track_role` / `energy_level` / `downbeat_offset` / `time_signature` | `MIXDESCRIPTOR` / `TRACKROLE` / `ENERGYLEVEL` / `DOWNBEATOFFSET` / `TIMESIGNATURE` |
| cover art (read) | FLAC `PICTURE` metadata block only — Ogg has no binary comment field |

Ogg is re-paged from scratch on every write (`ogg.py`): growing or shrinking
the comment packet shifts every later page boundary, and Ogg's page-and-CRC
structure has to be rebuilt to match, not patched in place. The identification
packet is kept alone on the stream's first page and header packets are kept
off the first audio page, matching the encapsulation rules real decoders
expect — a detail a naive re-implementation gets away with skipping only
because most files are short enough to hide the bug.

**Family C (MP4 atoms), M4A/M4P/MP4/M4V:**

| Field | Atom |
|---|---|
| `title` / `artist` / `album` | `©nam` / `©ART` / `©alb` |
| `bpm` | `tmpo` (spec-typed 16-bit integer) + `----:com.apple.iTunes:BPM_PRECISE` |
| `initial_key` | `----:com.apple.iTunes:initialkey` — matches the existing on-disk spelling |
| `mix_descriptor` / `track_role` / `energy_level` / `downbeat_offset` / `time_signature` | `----:com.apple.iTunes:MIXDESCRIPTOR` etc. |

Only `moov` is ever rebuilt; `mdat` and every other top-level box are copied
byte-for-byte untouched. When `moov` sits before `mdat` (the common
`+faststart` layout) and rewriting `ilst` changes moov's size, every
`stco`/`co64` chunk-offset table in every `trak` is patched by exactly that
delta — those tables store absolute file offsets into `mdat`, and a missed
patch there is silent audio corruption, not a loud failure.

BPM is stored twice in families A and C, on purpose: `TBPM` and `tmpo` are
both defined by their specs as integers, and are what every other tool in the
chain reads, including Rekordbox. But half a BPM drifts a full beat within a
few bars, so full precision is kept in a companion frame/atom. Reads prefer
the precise value only while it still agrees with the coarse one — if another
tool rewrote `TBPM`/`tmpo` alone, the file's plain statement wins over Anvil's
stale companion. Family B needs no such companion: a Vorbis comment isn't
spec-typed to an integer, so full precision fits directly in `bpm`.

## Known limitations

- **ID3v2.2 is rejected**, not parsed. Its three-character frame IDs would be
  silently misread by a v2.3 parser, so `UnsupportedFormat` is raised instead.
- **Raw AAC and WavPack have no writer.** Raw ADTS AAC has no reliable tag
  container of its own; WavPack was never in scope. `sniff()` raises
  `UnsupportedFormat` for both.
- **Cover art write is read-only everywhere.** `read_cover_art()` covers ID3
  `APIC` and FLAC `PICTURE`; nothing writes embedded art yet, and Ogg/MP4
  cover art isn't read either (Ogg has no binary comment field; MP4's `covr`
  atom was never implemented). Worth checking whether the live app writes
  embedded art at all before building this — it may not be needed.
- **`beat_map` is not a tag field and never will be.** A variable-tempo beat
  map is not tag-shaped data in any container — Rekordbox keeps it in ANLZ
  files. It belongs in `db_companion`, headed for the Rekordbox-DB layer.
- **Anvil never writes the database.** `db_companion` says what a value *would*
  be in `master.db` shape; applying it stays with `db_connection.py` and
  `key_mapper.py`, behind the same Rekordbox-must-be-closed gate as everything
  else.
- **Frame flags are written as zero.** Anvil never emits compressed,
  encrypted, or grouped frames, and preserving a compression flag it did not
  honour would describe the payload incorrectly.

## Tests

```
pytest tests/test_anvil_id3.py tests/test_anvil_flac.py tests/test_anvil_ogg.py \
       tests/test_anvil_mp4.py tests/test_anvil_dryrun.py tests/test_anvil_mutagen_interop.py
```

`test_anvil_id3.py` (and its family B/C counterparts) prove Anvil is
self-consistent. `test_anvil_mutagen_interop.py` proves it is *correct*, by
checking its bytes against an independent implementation of the same specs —
a confidently-wrong implementation would pass the first suites and fail this
one. The interop suite skips itself once mutagen is gone, so the test
guarding the migration does not outlive it. The FLAC/Ogg/MP4 suites need
`ffmpeg` to synthesize real encoded fixtures (mutagen only tags existing
audio, it doesn't encode) and skip cleanly if it isn't installed.
