# Iron accuracy testbed harness

These scripts run Iron against real music, using the owner's Rekordbox 7 library as ground truth.
They expect a local testbed at `~/FableGearTestbed` (copies of audio, manifests, results). Nothing here
writes to the Passport library or to the live Rekordbox database.

- `iron_db.py`: run Iron over the FableGear app database (read from a snapshot). DB tags are not ground truth.
- `build_rb_set.py`: copy Rekordbox-analysed tracks into a named set, recording Rekordbox's BPM, key and downbeat.
- `live_collect.py`: watch the live Rekordbox library and collect tracks as it analyses them (snapshots only).
- `iron_real.py`: run Iron on a Rekordbox set, then the Anvil hand-off check on copies.
- `key_features.py`, `key_eval.py`: cache whole-track chroma spectra and score key-profile ideas offline.
- `process_file_real.py`: FableGear's real `process_file` on copies, with tags read back.

Reference rule: Rekordbox BPM and key are taken from the snapshot row matched by exact file path.
Passport tag values are not used as truth.
