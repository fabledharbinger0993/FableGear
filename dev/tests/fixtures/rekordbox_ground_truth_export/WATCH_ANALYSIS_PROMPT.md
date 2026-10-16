# High-value prompt — watch Rekordbox analyze a track (live capture)

## Goal
Observe, in real time and strictly read-only, everything Rekordbox writes when
it **analyzes** a track (not exports). Analysis is the source of beat grid,
waveforms, phrase/song-structure, key, and any AI artifacts. Capturing it tells
us whether Rekordbox produces data that (a) is needed for a hardware-faithful
export, or (b) is "special / for Pioneer down the road" and not in the USB export
we already captured — so FableGear can regenerate or at least account for it.

## What we already know (static, before watching)
- Local analysis store: `~/Library/Pioneer/rekordbox/share/PIONEER/USBANLZ/`,
  folder scheme `xxx/uuid/ANLZ0000.*` (UUID-based, unlike the export's
  `P###/8HEXID/`).
- Per track locally: `.DAT`, `.EXT`, `.2EX` (PMAI tag chains, same tags as the
  export) **plus `.3EX`** — a MessagePack blob keyed `"embedding"` (AI track
  vector; NOT written to the USB export). ~2900 tracks have all four.
- Master DB: `master.db` (SQLCipher, rekordbox 6, ~781 MB, live WAL). Read-only;
  we do not decrypt it.

## Watch procedure (execute now — Rekordbox is running, pid 12722)
1. **Baseline** the analysis store: record every existing ANLZ dir + each file's
   size + mtime (fast; no hashing of ~12k files).
2. **Arm a filesystem monitor** on the store (and note master.db WAL growth) so
   every new/changed file during analysis surfaces as an event.
3. **User action:** in Rekordbox, right-click a track → *Analyze Track* (or add a
   fresh track and let auto-analysis run). Prefer a track WITH phrase/vocal
   detection enabled so PSSI + `.3EX` get produced. Tell Claude which track.
4. **Capture:** diff the store against baseline → the new/changed ANLZ dir(s).
   For each new file: parse `.DAT/.EXT/.2EX` (PMAI tags, byte layout), and
   characterize `.3EX` (MessagePack keys/shape). Compare the tag set + any new
   tag/field against the exported ground truth.
5. **Report:** list exactly what analysis wrote, flag anything NOT present in the
   USB export, and classify each as "export-relevant" vs "Rekordbox-internal /
   future-hardware."

## Guardrails
- Read-only against all Rekordbox data. Never write to the store or the DB, never
  decrypt master.db, never modify Rekordbox settings.
- The store is the user's MAIN library — observe only.
