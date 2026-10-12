# Rekordbox 7 export, 21 tracks (CDJ-3000 firmware 3.2x loaded it correctly)

A genuine Rekordbox-written export of one album (21 tracks), captured 2026-10-11 and then
**loaded and played correctly on a CDJ-3000 running firmware 3.2x**. It is the reference for the
DeviceSQL (`export.pdb`) writer. See `docs/devicesql_export_writer.md`.

- `PIONEER/rekordbox/export.pdb`, `exportExt.pdb`: the Device Library files the deck reads.
  **Caveat:** these are the copies *after* the deck's session. The deck rewrote them (mtime 2013,
  the deck's unset clock): `export.pdb` grew from 229,376 to 241,664 bytes. Treat them as "what a
  working stick looks like", not as Rekordbox's raw output.
- `PIONEER/rekordbox/exportLibrary.db`: the OneLibrary database (SQLCipher). The deck never wrote to it.
- No audio, ANLZ or artwork is committed (size, and the audio is commercial music).
- Tracks and artists are named inside the pdb; this fixture holds metadata only.
