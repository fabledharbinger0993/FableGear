# DJMTGO USB Format Inspection — Phase A Ground Truth

Date: 2026-06-12
Tooling: `cli.py usb-inspect` (commit 0109a0e) + pyrekordbox 0.4.4 direct ANLZ parsing.
Drive inspected read-only. All track titles, file paths, and library-identifying
data are redacted from this report.

## usb-inspect verdict

```
USB inspection: /Volumes/DJMTGO
  PIONEER/ directory:  ✓
  DeviceSQL (CDJ-3000): ✓  DeviceSQL header OK — page size 4096, 20 tables, 610,304 bytes
  OneLibrary (OMNIS):   ⚠  present but not plain SQLite (possibly encrypted) — UNVERIFIED
  ANLZ analysis files:  360 track(s)
  Settings files:       MYSETTING.DAT, MYSETTING2.DAT, DEVSETTING.DAT, DJMMYSETTING.DAT
  Verdict: DUAL-FORMAT — boots on both fleets
```

Volume root also contains `ALPHATHETA REC`, `PIONEER REC`, and `Contents`
directories alongside `PIONEER/` — consistent with a drive that has been used
for on-deck recording on both Pioneer and AlphaTheta firmware generations.

## ANLZ inventory

Per-track ANLZ sets under `PIONEER/USBANLZ/`:

| Extension | Count | Role |
|---|---|---|
| `.DAT` | 362 | Base analysis (beat grid, cues, mono waveform) |
| `.EXT` | 362 | Extended analysis (color waveforms, extended cues, **song structure**) |
| `.2EX` | 362 | CDJ-3000 extension (3-band waveforms) |

360 of the 362 sets correspond to tracks counted by usb-inspect (the inspector
counts track directories; two sets appear orphaned or duplicated — worth a
follow-up diff against the DeviceSQL track table in a later phase).

## .2EX contents — corrected ground truth

**Finding: on this drive, `.2EX` does NOT carry phrase/lighting (PSSI) data.**
Every one of the 360 sampled `.2EX` files parses to exactly four tags:

| Tag | Header/tag size (representative) | Contents |
|---|---|---|
| `PPTH` | 16 / 174 | UTF-16 path of the audio file (`len_path`, `path`) — redacted |
| `PWV7` | 24 / 70,674 | 3-band waveform **detail**: `len_entry_bytes=3`, `len_entries=23550`, `unknown=9830400`, `entries` = raw bytes (3 bytes/entry, one entry per ~frame) |
| `PWV6` | 20 / 3,620 | 3-band waveform **preview**: `len_entry_bytes=3`, `len_entries=1200`, `entries` = 3,600 raw bytes (fixed 1,200 columns) |
| `PWVC` | 14 / 20 | Waveform color/key summary: `unknown=0`, `data` = 3 small ints (e.g. `[88, 81, 127]`) |

pyrekordbox 0.4.4 notes:
- `AnlzFile.parse_file()` handles the XOR unmasking transparently.
- `anlz.tags` is a **list** of tag objects in 0.4.4, not a dict — iterate, don't `.keys()`.
- Tag payloads live under `tag.struct.content` (construct Container).

A raw fourcc sweep found one `.2EX` containing the byte sequence `PSSI`, but it
sits mid-stream inside a `PWV7` waveform blob with a nonsense header length —
a coincidental byte run, not a tag. pyrekordbox parses that file to the same
four tags as every other `.2EX`.

## Where the phrase data actually is: PSSI in .EXT

**All 360 sampled `.EXT` files carry a PSSI (song structure) tag.** This is the
phrase data the CDJ-3000 lighting controller consumes. A representative `.EXT`
parses to: `PPTH, PWV3, PCOB×2, PCO2×2, PQT2, PWV5, PWV4, PSSI`.

PSSI fields exposed by pyrekordbox 0.4.4:

```
len_entry_bytes: 24        # bytes per phrase entry
len_entries:     19        # number of phrases (varies per track)
mood:            2         # 1=high, 2=mid, 3=low (rekordbox mood classification)
end_beat:        571       # last beat covered by the structure
bank:            0         # lighting bank (CDJ-3000 lighting style selector)
u1/u2/u3:        unknowns  # unparsed padding/reserved containers
entries[]: per-phrase containers with fields:
  index, beat, kind,       # phrase number, start beat, phrase kind (intro/verse/…)
  k1, k2, k3, b,           # kind modifiers (mood-dependent meaning)
  beat_2, beat_3, beat_4,  # sub-division beats within the phrase
  fill, beat_fill,         # fill-in flag + fill start beat
  u1–u5                    # unknowns
```

This matches the known rekordbox song-structure layout: `bank` + `mood` +
per-phrase `kind` codes are exactly what a future "export with phrase data"
feature must write.

## Format state notes

- DeviceSQL (`export.pdb`-style) database verified structurally sound: page
  size 4096, 20 tables, 610,304 bytes.
- OneLibrary (OMNIS / AlphaTheta) database is present but not plain SQLite —
  likely SQLCipher-encrypted. Phase A cannot verify its contents; treat as
  opaque until the OMNIS key derivation is implemented.
- Full Pioneer settings complement present (MYSETTING, MYSETTING2, DEVSETTING,
  DJMMYSETTING) — drive boots with player/mixer preferences on both fleets.

## Implications for the write implementation (Phase B+)

1. A CDJ-3000-complete export must write **three** ANLZ files per track; the
   `.2EX` needs PWV6/PWV7 3-band waveforms (3 bytes/entry; preview fixed at
   1,200 entries) plus PPTH and the 20-byte PWVC summary.
2. Phrase/lighting export targets **PSSI in `.EXT`**, not `.2EX`. The PSSI
   writer needs mood/bank plus 24-byte phrase entries.
3. Several PSSI and PWVC fields are still `unknown` in pyrekordbox 0.4.4 —
   byte-for-byte round-trip fidelity (copy-through of unknowns) is safer than
   regeneration for any rewrite path.
