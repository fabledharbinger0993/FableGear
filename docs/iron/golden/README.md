# Iron golden set (BPM labels)

`iron_golden_labels.jsonl` holds ear-verified tempo labels, one JSON object per line.
Labels only, no audio. Match rows to files by `track` (file name stem).

Every row comes from an owner ear check already recorded in `docs/IRON_RESEARCH.md`
(§19.9, §20.7, §20.8). Nothing here was measured by a detector or taken from Rekordbox.

Fields: `track`, `truth_bpm_min`/`truth_bpm_max` (accepted range; ear checks give ranges, not exact
values), `convention` (`drum_pulse` or `felt_tempo`, see §19.9), `iron_verdict`
(`correct`, `wrong_half_time`, `wrong_two_thirds`), `label_source`, `evidence`, `note`.

## Known gaps

- Only 12 tracks; the 2:3 group has 2 of 6 named in the research log and the half-time group 5 of 6.
  The remaining sampled tracks need their names added from the owner's notes.
- Key, downbeat and meter labels are not yet present. Those need ear verification too.
- Ranges for `Chill Vibes` rows use the file label from §20.8; replace with real file stems.
- Include hard cases when extending: tempo drift, non-4/4, ambiguous intros.

Validate with `python3 scripts/validate_iron_golden.py`.
