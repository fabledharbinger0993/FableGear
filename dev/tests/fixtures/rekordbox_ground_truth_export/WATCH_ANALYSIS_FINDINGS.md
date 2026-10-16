# Watch-Rekordbox-analysis — findings (live capture, 2026-07-26)

Executed the plan in `WATCH_ANALYSIS_PROMPT.md`: baselined the local analysis
store (11,830 ANLZ files), armed an mtime monitor, and captured a live batch
analysis of ~7 tracks. Read-only throughout; no Rekordbox data modified.

## What analysis writes per track
Rekordbox writes **four** files into
`~/Library/Pioneer/rekordbox/share/PIONEER/USBANLZ/<xxx>/<uuid>/`:

| File | Format | Tags / contents | In USB export? |
|---|---|---|---|
| `ANLZ0000.DAT` | PMAI | `PPTH, PVBR, PQTZ, PWAV, PWV2, PCOB×2` | ✅ yes |
| `ANLZ0000.EXT` | PMAI | `PPTH, PWV3, PCOB×2, PCO2×2, PQT2, PWV5, PWV4, PSSI` | ✅ yes |
| `ANLZ0000.2EX` | PMAI | `PPTH, PWV6, PWV7, PWVC` | ✅ yes |
| `ANLZ0000.3EX` | **MessagePack** | AI embedding bundle (below) | ❌ **NOT exported** |

Observed behaviour:
- **`.EXT` is written in stages** — first a small version (~95 KB: structural
  tags/beat grid/cues), then rewritten larger (~287 KB) once the waveform tags
  (`PWV3/4/5`) and `PSSI` are appended. A watcher/writer must treat `.EXT` as
  finalized only after the size stabilizes.
- The PMAI tag set produced by analysis is **identical** to what the finished
  USB export carries. Nothing hardware-relevant is hidden in analysis and
  dropped from export — the export is faithful.
- Local folder scheme is `<3hex>/<uuid>/` (content-UUID based), unlike the
  export's `P###/<8HEXID>/`. Rekordbox remaps folders at export time.

## `.3EX` decoded (the one non-exported artifact)
MessagePack: `{ "embedding": { … } }` with fields (sample track):
- `d5`: **41×64 float matrix** — per-segment embeddings (≈per-phrase feature vectors)
- `d6`: 64-float vector — whole-track embedding
- `d7`: 2-float vector — likely a 2-D mood/similarity projection
- `d11`: `130.0` — BPM · `d4`: `613.4` — duration (s)
- `d3`: `d79b94b0…` — 32-hex fingerprint · `d2`: `168921450` — track id
- `d8`,`d12`: scores (0–1) · `d9`,`d10`: small int counts

This is Rekordbox's **track-similarity / recommendation / automix** feature data
(feeds `networkAnalyze6.db` + `networkRecommend.db`). Reference sample committed
at `analysis_sample/local_share_956_104a8/` (+ `_3EX_structure.json`).

## Answer to "anything special needed for export or Pioneer down the road?"
- **For CDJ-3000 export & playback: NO hidden requirement.** Everything the CDJ
  uses (waveforms, beat grid, cues, phrase/PSSI) is in the PMAI `.DAT/.EXT/.2EX`,
  which is exactly what the export carries. FableGear's parity target is fully
  defined by those three files (+ the OneLibrary/DeviceSQL databases).
- **`.3EX` is optional / future-facing.** Not required for hardware playback
  (absent from the working export). It would only matter if FableGear later wants
  Rekordbox-side AI features (related tracks, automix, newer Lighting AI) — which
  need the proprietary embedding model and are out of scope for hardware parity.

## Net effect on the campaign
No change to the Phase 2/3 gap list for hardware parity: FableGear must still
generate the PMAI waveform/`PSSI` tags and wire ANLZ into the export. `.3EX` is
explicitly **out of scope** for CDJ-3000 parity and noted as a future item only.
