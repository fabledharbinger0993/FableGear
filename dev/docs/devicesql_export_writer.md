# DeviceSQL `export.pdb` writer: evidence, plan, and risks

Status (2026-10-20): **milestones 1-3 built; deck tests A and B not yet run** (section 6). Written
2026-10-11 after a hardware test; sections 1-5 are kept as written then, with corrections in section 6.
Every statement is labelled **[D]** demonstrated (measured on hardware or from a file we hold), **[S]**
from a cited source, **[I]** inferred, or **[U]** unknown.

## 1. Why this exists

Goal: Iron's BPM and key, written to tags by Anvil, must travel to a USB stick and show on the
owner's CDJ-3000 the way Rekordbox's do.

What the 2026-10-11 hardware test showed (CDJ-3000, firmware 3.2x, same stick throughout):

| Stick | Result on the deck |
|---|---|
| FableGear export: `exportLibrary.db` (OneLibrary) only, 20 tracks, verified readable | **Nothing showed.** The deck never opened `exportLibrary.db`; it only added `TrashBox` and `.CacheData/` (on the first stick it also created its own empty `export.pdb`). [D] |
| Rekordbox 7 export (`export.pdb` + `exportExt.pdb` + OneLibrary), 21 tracks | **"Everything worked perfectly."** The deck rewrote `export.pdb` and `exportExt.pdb` and never touched `exportLibrary.db`. [D] |

So the deck, stick, cable and filesystem (FAT32 on GPT) are fine, and the only missing piece is a
DeviceSQL `export.pdb`. [D for the result; I for "that is the only difference", since the two sticks
also differ in ANLZ completeness, artwork and settings files.]

Sources on firmware (retrieved 2026-10-11; these are late-2025 reports and a newer firmware may exist):
- AlphaTheta, [Important Notice for Customers Using USB Devices with Our DJ Equipment](https://alphatheta.com/en/information/important-notice-for-customers-using-usb-devices-with-our-dj-equipment/):
  the plain CDJ-3000 and XDJ-XZ read the original Device Library format; the CDJ-3000X is the OneLibrary
  model; rekordbox 7.2.11 and later write both formats to an exported drive [S].
- DJ Mag, [AlphaTheta suspends CDJ-3000 firmware update distribution following playlist issues](https://djmag.com/tech/alphatheta-suspends-cdj-3000-firmware-update-distribution-following-playlist-issues)
  and Digital DJ Tips, [AlphaTheta pulls CDJ-3000 firmware after playlist issues](https://www.digitaldjtips.com/alphatheta-pulls-cdj-3000-firmware-after-playlist-issues/):
  CDJ-3000 firmware 3.30 (21 Oct 2025) added OneLibrary and was withdrawn on 4 Nov 2025, reportedly because
  playlists went blank on sticks holding both formats [S].
The deck under test is on 3.2x [D, user-reported]. The July 2026 note in
`HARDWARE_TEST_ONELIBRARY.md` ("CDJ-3000 plays OneLibrary-only") is therefore unexplained: the deck was
probably on 3.30, or that stick already held a Rekordbox-made `export.pdb` [I].

## 2. What is already in place

- Iron BPM/key detection, Anvil tag writing, and the exporter's tag fallback for key/BPM
  (`fablegear_database/tag_fallback.py`, used by the OneLibrary writer and the ANLZ stage). [D, tested]
- Key notation: Iron emits Camelot (`8A`); `key_mapper.notation_to_scale_name` converts to the
  Rekordbox scale name (`Am`). [D]
- ANLZ writer (beat grid, cues, waveform) runs in the CLI export (`export-onelibrary --with-anlz`). [D] Whether the Record Room UI export route calls it too is not verified. [U]
- A read-only DeviceSQL reader, `chop_shop/devicesql_reader.py`. It parses the header, table pointers,
  page index and (partly) track rows. **Fixed in milestone 1 [D]:** the row-index decoder read the 36-byte row groups backwards, so on the 21-track capture it recovered only 3 of 21 track rows. It now recovers all 21 and every lookup table, and the result matches the same stick's OneLibrary database.

## 3. What the Rekordbox capture shows

Fixture: `tests/fixtures/rekordbox_21track_export/` (see its README for the post-deck caveat).

`export.pdb` [D]: 241,664 bytes = 59 pages of 4,096. Header: 4 zero bytes, `page_size`=4096,
`num_tables`=20, `next_unused_page`=60, then two more u32 (`5`, `91`) before a zero. The table pointer
array follows. Row counts by table type (from the page headers):

| type | rows | | type | rows |
|---|---|---|---|---|
| 0 tracks | 21 | | 7 playlist_tree | 1 |
| 1 genres | 1 | | 8 playlist_entries | 20 |
| 2 artists | 8 | | 13 artwork | 6 |
| 3 albums | 1 | | 16 columns | 27 |
| 4 labels | 1 | | others (9-12, 14, 15, 17-19) | 0 |
| 5 keys | 10 | | | |
| 6 colors | 8 | | | |

(`playlist_entries` = 20 for 21 tracks: one entry fewer than tracks. [U] Possibly the deck changed
it, or one track is not in the playlist.)

`exportExt.pdb` [D]: 73,728 bytes, 9 tables, `next_unused_page`=21.

**Keys, the point of all this [D]:**
- The keys table has rows `id:u32, id2:u32, name` where `id2 == id`, and `name` is a short ASCII
  DeviceSQL string (`Abm`, `Ebm`, `Dbm`, `F#m`, `Em`, `F`, `Gm`, `E`, `Am`, `A`).
- The ids and names are identical to OneLibrary's `key` table in the same stick.
- A track row carries a `key_id` into that table. Decoded rows: *Unbreakable* → 1 → `Abm`,
  *You Are My Life* → 8 → `E`, *Don't Walk Away* → 10 → `A`; each matches OneLibrary.
- Rekordbox builds the key table from the keys present in the export, in encounter order (not a fixed
  24-entry table). [I, consistent with the comment in `key_mapper.py` about `DjmdKey.Seq`.]

Row storage [D, partly]: rows sit in a page heap starting at offset 0x28; each page ends with row
groups of 36 bytes (16 reversed u16 offsets, a u16 presence bitmask, a u16 unknown). A string uses a
header byte `((len+1) << 1) | 1` for short ASCII.

## 4. Plan

Each milestone ends with a check that does not need the deck, then a deck test where noted.

1. **Read it all.** Make the reader recover every row of every table in the capture (tracks first:
   title, artist, album, genre, key, BPM, paths, ANLZ path, and the ids). Check: row counts equal the
   page headers; resolved keys match OneLibrary; strings match.
2. **Round trip.** Parse the capture into a model and serialise it back. Check: our serialisation
   re-parses to the same model; compare byte layout against Rekordbox's, and list every deliberate
   difference. This is the main protection against writing a file the deck rejects.
3. **Write a minimal `export.pdb`/`exportExt.pdb`** from the FableGear database for a few tracks,
   with unused tables laid out the way Rekordbox lays them out. Check: the reader parses it; structure
   matches the capture. **Deck test A: does the stick list the tracks and play?**
4. **Key and BPM columns, then playlists.** **Deck test B: do KEY/BPM show Iron's values?** Then
   playlists, artwork, and the remaining settings files.
5. **Decide the dual-format question** (section 5).

Acceptance for "done": a stick written only by FableGear lists, plays, and shows Iron's key and BPM
on the CDJ-3000 on firmware 3.2x.

## 5. Risks and open questions

- **The deck is strict [I].** A malformed file likely gives an error or an empty list. Every fix needs a
  physical test, so deck test A should use as few tracks and as few features as possible.
- **Both formats on one stick.** Rekordbox 7.2.11 writes both ([AlphaTheta notice](https://alphatheta.com/en/information/important-notice-for-customers-using-usb-devices-with-our-dj-equipment/)) [S]. The firmware 3.30 problem
  (blank playlists with both) is a reason to keep checking this on any deck that updates [S, U for
  current firmware]. FableGear should probably write both, as Rekordbox does, but that is undecided.
- **Partial pdb at scale [U].** An older capture (413 tracks in OneLibrary, 57 in `export.pdb`) suggests
  Rekordbox's old-format file can be a subset for large libraries. At 21 tracks it is complete. What
  decides this, and what the deck shows in the large case, is unknown.
- **Unknown fields.** Several header and row fields are not understood [U]. Round trip against real
  files is the safeguard; guessing is not.
- **Reference quality.** Only one small capture was made after a deck session. Another untouched
  Rekordbox export (and ideally a larger one) would be better.
- **Licensing/provenance.** The format is documented by the Deep Symmetry "DJ Link Ecosystem
  Analysis" project, which `devicesql_reader.py` already cites. Nothing here copies code from
  `rekordcrate` or `crate-digger`.

## 6. Built, 2026-10-20 (milestones 2 and 3)

Code: `fablegear/chop_shop/devicesql_writer.py` (writer), `fablegear/chop_shop/devicesql_canned.py`
(fixed rows, generated by `dev/scripts/devicesql_canned_extract.py`), `fablegear/device_export.py`
(Record Room export). Tests: `dev/tests/test_devicesql_writer.py`, `test_device_export_cli.py`,
`test_record_room_device_export.py`.

### 6.1 Correction: the untouched Rekordbox export is complete, not a 57-track subset [D]

Section 5's "partial pdb at scale" risk came from `rekordbox_ground_truth_export/GAP_ANALYSIS.md`
("export.pdb: 57 tracks" of 413). That count was taken with the reader before milestone 1 fixed the
row-group decoding (3 of 21 rows recovered on the stick). The fixed reader recovers **413 track rows,
413 unique ids** from that same `export.pdb`, plus 148 artists, 150 albums, 35 genres, 45 labels,
24 keys, 231 artwork rows and 434 playlist entries. Rekordbox writes the whole library into the
Device Library. The risk is closed.

### 6.2 Milestone 2: round trip [D]

`parse_image` / `serialise_image` model every page (header, table pointers, data pages with row
slots, presence and transaction bits and row bytes, index pages with all 1,004 entry slots). Derived
fields are recomputed, not copied: the packed row counts, `free_size`, `used_size`, the row-group
masks. All four committed Rekordbox files (stick and ground truth, `export.pdb` and `exportExt.pdb`)
serialise back **byte for byte with no page carried verbatim**; a mutation test flips one presence bit
and checks the counts follow. Byte-identical output means there is no list of differences to explain.

Layout facts this established, beyond section 3 (all [D] on both captures):
- `free_size = page_size - 0x28 - used_size - (2 * row_offsets + 4 * row_groups)`: the index occupies
  only the slots in use, and Rekordbox lets the heap grow into the unused part of the last group.
- Index page body at 0x20: u16 0x1FFF, u16 0x1FFF, u16 **1004 = the entry capacity** (Deep Symmetry's
  "magic 0x03EC"), u16 next entry offset, u32 page index, u32 first data page (0x03FFFFFF for an empty
  table), u32 0x03FFFFFF, u32 0, u16 entry count, u16 first empty slot, then 1004 u32 entries (empty =
  0x1FFFFFF8) and 20 zero bytes. Rekordbox's own multi-page genre/artist/album/label/artwork tables
  carry **zero** entries; entries appear only on tables with deleted rows (and tracks).
- Pages: table *i*'s index page is 2*i*+1 and its first data page 2*i*+2 (a zeroed "empty candidate"
  when the table is empty); further pages are appended from page 41 in insert order; the last data
  page's `next_page` is a zeroed empty-candidate page; the file ends after the last non-blank page and
  `next_unused_page` counts the unwritten trailing candidate.
- `index_shift` (bytes 2-3 of track, artist, album, history and My Tag rows) = row slot * 0x20.
- Track-row string offsets start at **0x5E** (Deep Symmetry's table says 0x64; the captures and the
  reader agree on 0x5E). 0x58 colour u8, 0x59 rating u8, 0x5A file type u16, 0x5C u16 = 3.
- Long strings (0x40 ASCII > 126 chars, 0x90 UTF-16LE) start on a 4-byte boundary of their row, with
  zero padding; short strings are not aligned. The ISRC slot has its own form (0x90, length, 0x00, 0x03,
  ASCII, NUL).
- Artist/album rows end with zero padding to align(end + 6) after a short name (297 of 297 rows) and
  align(end + 4) after a long name (2 of 2 rows: thinly evidenced).
- Colours, the 27 browse columns and table 0x12 are byte-identical in both exports; table 0x11 (the
  browse-menu layout, a user preference) differs. The writer copies the stick's versions.

### 6.3 Milestone 3: a fresh Device Library from FableGear [D for the file, U for the deck]

`build_export_pdb` lays tables out by the rules above and the row encoders reproduce Rekordbox's bytes:
keys, genres, labels, artists (148 + 8), albums (150 + 1), the history row, playlist rows, and every
numeric field and string of all 21 + 413 track rows. Named differences, all unknown fields copied from
the stick the deck loaded: track u32 at 0x14 (varies per track in Rekordbox's files; we write a 28-bit
hash of the drive path), the u16 pair at 0x18 (per export; the stick's values), string slots 2/3/4
(small numbers, the stick's "2"/"3"/"" for freshly analysed tracks with no cues), slot 6 "ON" (a
per-track KUVO preference in Rekordbox), file-header u32 at 0x10 (4, the ground truth's value), and
no zero slack after track strings (Rekordbox leaves 44-51 bytes). `write_device_library` re-reads every
file it built and refuses to land one that does not read back with the same titles, paths, ANLZ paths,
BPM and key.

Wiring:
- `cli.py export-onelibrary` now writes `export.pdb` + `exportExt.pdb` beside `exportLibrary.db` by
  default (`--no-device-library` skips; `--force` replaces; refusal is checked before anything is
  written). Both databases come from one pass of `OneLibraryWriter`, so track ids, audio paths and the
  `PIONEER/USBANLZ/P{id//2048:03d}/{id:08X}/ANLZ0000.DAT` paths agree; `--with-anlz` writes those ANLZ
  files (PPTH = the pdb's audio path, PQTZ beat grid at the exported BPM). OneLibrary's `bitrate` is
  now kbps as Rekordbox stores it (FableGear holds bits per second; it was writing 320000).
- The Record Room's "Export to USB" writes the same CDJ-ready stick onto a blank drive or a stick
  FableGear wrote before (`PIONEER/rekordbox/FABLEGEAR_EXPORT.txt`), from a throwaway database:
  FableGear's own analysis (Iron's BPM/key, grids, cues) wins over Rekordbox's values. A Rekordbox-made
  stick is never written. The old `PIONEER/Master/master.db` path is unchanged.

### 6.4 Still open

1. **Deck test A** (does a FableGear-only stick list and play on the CDJ-3000, firmware 3.2x?) and
   **deck test B** (do KEY and BPM show Iron's values?). Suggested: 5-10 tracks, `cli.py
   export-onelibrary /Volumes/STICK/PIONEER/rekordbox/exportLibrary.db --stage-audio --with-anlz
   --playlist <name>`, or the Record Room export onto a blank stick. Bring the stick back so its
   post-deck `export.pdb` can be diffed like the 21-track capture.
2. Artwork is not exported (the artwork table is empty; track `artwork_id` 0).
3. Settings files (`MYSETTING.DAT`, `DEVSETTING.DAT`, `DJMMYSETTING.DAT`) are not written; Rekordbox
   writes them. Unknown whether the deck needs them.
4. The deck-accepted values of the unknown fields above are carried, not understood.
