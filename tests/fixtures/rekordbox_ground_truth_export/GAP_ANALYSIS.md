# Rekordbox ↔ FableGear export parity — gap analysis

Status: **Phase 0 COMPLETE** (ground truth captured from the *finished* export).
FableGear-side column is established statically from code (definitive).
Rekordbox-side column is measured from the real completed export. The "present
but different" byte-level rows still require an actual FableGear export (blocked:
must run on the MacBook, not this Mac Studio).

## Machine / capture context (FINAL)
- Ground-truth drive: `/Volumes/DJMTGO`, **FAT32 (MS-DOS)**, not reformatted by
  Rekordbox (Media Read-Only: No; Rekordbox used the existing FAT32 filesystem).
- Captured after export completed + Rekordbox released the drive (clean
  WAL-checkpointed OneLibrary DB). Fixture = byte-exact copy of `PIONEER/`.
- **Final size: 1266 files / 145,687,254 bytes** — 1239 USBANLZ, 5 rekordbox DB
  files, 16 Artwork, 5 settings/profile, 1 gcred.dat.
- Library: **412 ANLZ track dirs / 611 audio files** (mixed AIFF/MP3/WAV).
  Larger than the brief's 10–20 track ask.
- Manifest with per-file SHA256: `parsed/manifest.json`. Parsed dumps in
  `parsed/` (pdb_export, pdb_exportExt, anlz_tags, settings, onelibrary,
  two_ex_xor).

## ⭐ Headline finding — the two databases hold DIFFERENT track counts
| Store | Tracks | Role |
|---|---|---|
| `exportLibrary.db` (OneLibrary) | **413** | complete library |
| `USBANLZ/` (ANLZ analysis) | **412** | full analysis for whole library |
| `export.pdb` (DeviceSQL) | **57** | **partial/legacy subset** (~520 KB) |

Verified by brute-scanning all 127 pages of `export.pdb` (not a reader bug —
57 track rows exist, period). Modern Rekordbox treats OneLibrary as the complete
library and writes only a *partial* DeviceSQL `export.pdb`. **Implication:**
"mirror Rekordbox's `export.pdb`" may mean a partial pdb; whether a CDJ-3000
reads `export.pdb` at all when OneLibrary is present is a Phase 4 hardware
question that should be answered before investing in a full DeviceSQL writer.
(Which 57 tracks / why = open question.)

## What each side writes directly under `PIONEER/`

| Path | Rekordbox (ground truth) | FableGear (from code) |
|---|---|---|
| `rekordbox/export.pdb` (DeviceSQL) | ✅ ~452 KB | ❌ **no writer exists** |
| `rekordbox/exportExt.pdb` (DeviceSQL ext) | ✅ ~72 KB | ❌ no writer |
| `rekordbox/exportLibrary.db` (OneLibrary) | ✅ + `-wal` (~2.4 MB) + `-shm` | ✅ `onelibrary_writer.py` (no WAL/shm) |
| `USBANLZ/*/*/ANLZ0000.DAT` | ✅ 350 | ⚠️ writer exists, **not wired into export route** |
| `USBANLZ/*/*/ANLZ0000.EXT` | ✅ 350 | ⚠️ writer exists, not wired in |
| `USBANLZ/*/*/ANLZ0000.2EX` | ✅ 350 | ❌ **never written** |
| `MYSETTING.DAT` | ✅ 148 B | ❌ not written |
| `MYSETTING2.DAT` | ✅ 148 B | ❌ not written |
| `DEVSETTING.DAT` | ✅ 140 B | ❌ not written |
| `DJMMYSETTING.DAT` | ✅ 160 B | ❌ not written |
| `djprofile.nxs` | ✅ 160 B (mtime older — possibly pre-existing) | ✅ `device_identity.write_dj_profile` (160 B) |
| `rekordbox/RBFLTR.DAT` | ❌ **absent everywhere on drive** | ✅ `device_identity.write_rbfltr` — **EXTRANEOUS** |
| `Artwork/` | ✅ 8 files | ❌ not written |
| `extracted/gcred.dat` | ✅ 66 B (opaque token) | ❌ not written |

## ANLZ tag inventory (per track) — MEASURED from ground truth

Measured via `tools/capture_export.py` across all 354 track dirs (every tag
present on 100% of tracks). Placement is exact, not inferred:

| File | Rekordbox tags (measured) | FableGear writer tags |
|---|---|---|
| `.DAT` | `PPTH, PVBR, PQTZ, PWAV, PWV2, PCOB×2` | `PPTH, PQTZ, PCOB×2` |
| `.EXT` | `PPTH, PWV3, PCOB×2, PCO2×2, PQT2, PWV5, PWV4, PSSI` | `PPTH, PCO2×2` |
| `.2EX` | `PPTH, PWV6, PWV7, PWVC` | *(no .2EX written)* |

Per-tag, and which side has it:

| Tag | Meaning | RB file | FableGear |
|---|---|---|---|
| `PPTH` | file path | .DAT/.EXT/.2EX | ✅ .DAT/.EXT |
| `PVBR` | VBR seek index | .DAT | ❌ |
| `PQTZ` | beat grid (1st gen) | .DAT | ✅ .DAT (only if beatgrid) |
| `PWAV` | mono waveform preview (small) | .DAT | ❌ |
| `PWV2` | mono waveform preview | .DAT | ❌ |
| `PCOB` | cues (1st gen) | .DAT ×2 **and** .EXT ×2 | ✅ .DAT ×2 only |
| `PCO2` | cues (2nd gen, color/comment) | .EXT ×2 | ✅ .EXT ×2 |
| `PQT2` | beat grid (2nd gen) | .EXT | ❌ |
| `PWV3` | mono waveform detail | .EXT | ❌ |
| `PWV4` | color waveform preview | .EXT | ❌ |
| `PWV5` | color waveform detail | .EXT | ❌ |
| `PSSI` | song structure / **phrase lighting** | **.EXT** (not .2EX!) | ❌ |
| `PWV6` | 3-band waveform preview (CDJ-3000) | .2EX | ❌ |
| `PWV7` | 3-band waveform detail (CDJ-3000) | .2EX | ❌ |
| `PWVC` | 3-band waveform color summary | .2EX | ❌ |

Note: the brief placed `PSSI` in `.2EX`; ground truth shows it in **`.EXT`**.
The `.2EX` file carries only the 3-band (PWV6/7/C) waveform + PPTH.

## OneLibrary content (MEASURED, FINAL — completed export)
Row counts from the real `exportLibrary.db` (WAL-checkpointed copy):
`content=413, cue=0, playlist=11, playlist_content=434, key=24, myTag=52,
myTag_content=0, history=0, hotCueBankList=0, image=231, album=150, artist=148,
genre=35, label=45, color=8`.

**⚠ CONFIRMED critical finding:** `cue=0` on the *completed* export. Real
Rekordbox stores hot cues/loops **only in ANLZ** (PCOB/PCO2), leaving the
OneLibrary `cue` table empty. FableGear does the opposite — writes cues into the
OneLibrary `cue` table and (currently) no ANLZ cues, and doesn't wire ANLZ into
the export at all. Net: **FableGear's cues would not appear on a CDJ**, which
reads them from ANLZ. Schema itself matches FableGear's verbatim `_SCHEMA_SQL`
copy (content columns line up).

Other confirmed divergences:
- **ANLZ folder id ≠ content_id.** Real `analysisDataFilePath` uses the master
  rekordbox content id (e.g. `/PIONEER/USBANLZ/P040/0001C418/ANLZ0000.DAT` for
  content_id=1). FableGear derives the folder from the OneLibrary content_id.
  Internally consistent on each side, but the folder ids won't match Rekordbox.
- **Key vocabulary (ground truth `key` table):** `Gm, D, Cm, G, Fm, Abm, E,
  F#m, …` (standard ScaleName strings). `key_mapper.py` output must resolve to
  these exact spellings.
- **Playlists flat** in this library (11 rows, all attribute=0, parent=0); ids
  have gaps (deleted playlists). No folder nesting to test hierarchy here.

## Preliminary gap list, tagged (hardware-impact ordered)

1. **missing on FableGear side — `export.pdb` (DeviceSQL).** No writer exists.
   CDJ-3000 on firmware < 3.19 cannot read OneLibrary; without `export.pdb` such
   a player sees nothing. Biggest single gap. *Overturns the brief's premise that
   FableGear writes `export.pdb` and we merely diff it.*
2. **missing on FableGear side — `exportExt.pdb`.** Same class as #1.
3. **missing on FableGear side — all waveform data.** No `PWV*` tags anywhere →
   CDJ shows no waveform / forces re-analysis, or falls back to low-res.
4. **missing on FableGear side — `.2EX` file + `PSSI`.** No phrase lighting; the
   `.2EX` XOR-mask work the brief describes is moot until a `.2EX` is written at all.
5. **missing on FableGear side — ANLZ not wired into export.** Even `.DAT`/`.EXT`
   (which the writer supports) are not emitted by `cmd_export_onelibrary`.
6. **missing on FableGear side — the four `*SETTING*.DAT` files.**
7. **missing on FableGear side — `Artwork/`, `extracted/gcred.dat`.**
8. **missing on FableGear side — `PQT2`, `PVBR`.**
9. **present on FableGear side / absent on Rekordbox — `RBFLTR.DAT`.** Extraneous.
   Resolves the brief's flagged discrepancy: Rekordbox uses the four `*SETTING*`
   files; FableGear substitutes `RBFLTR.DAT`, which real Rekordbox never writes.
10. **present but different — `exportLibrary.db`** (schema/cue/key encoding).
    Needs a real FableGear export to diff. FableGear omits `-wal`/`-shm`.
11. **present but different — `djprofile.nxs`** (160 B both; needs byte diff).

## `.2EX` masking — brief correction (MEASURED)
Real `.2EX` files begin with **plaintext** `PMAI` (`504d4149`) + normal PMAI
header + `PPTH` tag. They are **NOT whole-file XOR-masked**. The brief's XOR base
pattern (`CB E1 EE FA …`) can at most mask the pixel-data region *inside* the
`PWV6`/`PWV7` 3-band waveform tags, not the file. Any `.2EX` writer must emit a
normal PMAI structure; confirm the masked region byte-for-byte against a
ground-truth `PWV6`/`PWV7` payload before claiming parity.

## Key strategic fork (blocks Phase 3 scope)
Does CDJ-3000 parity target **OneLibrary only** (bet on fw 3.19+), or must
FableGear also generate **DeviceSQL `export.pdb`** for the whole fleet? The
latter means building an entire binary `export.pdb` writer from scratch —
FableGear currently has only a *reader*.
