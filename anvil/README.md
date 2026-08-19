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

Container family A: **ID3v2.3 and ID3v2.4** over **MP3, WAV, AIFF**.

| Field | ID3 representation |
|---|---|
| `title` / `artist` / `album` | `TIT2` / `TPE1` / `TALB` — native |
| `initial_key` | `TKEY` — native |
| `bpm` | `TBPM` (spec-compliant integer) + `TXXX:BPM_PRECISE` |
| `mix_descriptor` / `track_role` / `energy_level` | `TXXX` — non-native |
| `downbeat_offset` / `time_signature` | `TXXX` — non-native |
| cover art (read) | `APIC` |

BPM is stored twice on purpose. `TBPM` is defined by the spec as an integer
and is what every other tool in the chain reads, including Rekordbox. But half
a BPM drifts a full beat within a few bars, so full precision is kept in a
companion `TXXX` frame. Reads prefer the precise value only while it still
agrees with `TBPM` — if another tool rewrote `TBPM` alone, the file's plain
statement wins over our stale companion.

## Known limitations

- **ID3v2.2 is rejected**, not parsed. Its three-character frame IDs would be
  silently misread by a v2.3 parser, so `UnsupportedFormat` is raised instead.
- **No MP4 / Vorbis / FLAC yet.** Container families B–D are designed
  (see the Anvil spec) but not implemented. `sniff()` raises
  `UnsupportedFormat` for them today.
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
pytest tests/test_anvil_id3.py tests/test_anvil_mutagen_interop.py
```

`test_anvil_id3.py` proves Anvil is self-consistent. `test_anvil_mutagen_interop.py`
proves it is *correct*, by checking its bytes against an independent
implementation of the same spec — a confidently-wrong implementation would
pass the first suite and fail the second. The interop suite skips itself once
mutagen is gone, so the test guarding the migration does not outlive it.
