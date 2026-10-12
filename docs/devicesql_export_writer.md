# DeviceSQL `export.pdb` writer: evidence, plan, and risks

Status: **plan, nothing built yet.** Written 2026-10-11 after a hardware test. Every statement is
labelled **[D]** demonstrated (measured on hardware or from a file we hold), **[S]** from a cited
source, **[I]** inferred, or **[U]** unknown.

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
  page index and (partly) track rows. **Gap [D]:** on the 21-track capture it recovers 3 of 21 track rows.

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
