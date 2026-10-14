# Brief for Codex: DeviceSQL `export.pdb` writer, milestone 2 (round trip)

You are working on FableGear, a local-first Rekordbox toolkit (Python 3.11-3.13, Flask UI, macOS).
This brief is self-contained. Read it fully, then read `docs/devicesql_export_writer.md` before
touching code.

## The goal behind the task

Iron (our BPM/key detector) and Anvil (our tag writer) produce BPM and musical key. The owner's
CDJ-3000 (firmware 3.2x) reads **only `export.pdb` / `exportExt.pdb`** (DeviceSQL format) from a
USB stick. It ignores `exportLibrary.db` (OneLibrary). Hardware-tested 2026-10-11: a FableGear
stick with only `exportLibrary.db` showed nothing; a Rekordbox stick with `export.pdb` worked.
So FableGear needs its own **DeviceSQL writer**. Nothing in the repo writes one yet.

Plan and evidence: `docs/devicesql_export_writer.md` (milestones 1-5). Milestone 1 (reader recovers
every row) is done and merged. **Your task is milestone 2: round trip.**

## Task

Build a model + serialiser so that this holds on the real Rekordbox capture:

    parse(export.pdb)  ->  model  ->  serialise  ->  bytes  ->  parse  ->  same model

and the serialised bytes are compared against Rekordbox's original, with **every deliberate
difference listed explicitly** (in a test or a doc table), not silently tolerated.

Start from `chop_shop/devicesql_reader.py` (the read-only parser) and its tests
`tests/test_devicesql_reader.py`, `tests/test_devicesql_pdb_rowwalk.py`,
`tests/test_devicesql_real_export.py`. Put the writer in a new module (suggested:
`chop_shop/devicesql_writer.py`); keep the reader read-only. Prefer extending the reader's
structures over duplicating them.

Reference data (do not modify):
- `tests/fixtures/rekordbox_21track_export/PIONEER/` (21 tracks; the primary reference). It is a
  **post-deck working-stick capture**, not raw Rekordbox output: the CDJ rewrote both files after
  Rekordbox exported them (`export.pdb` grew from 229,376 to 241,664 bytes). So any layout you see
  may come from the deck, and a byte difference from our output is not proof of a Rekordbox
  convention. Read its README.
- `tests/fixtures/rekordbox_ground_truth_export/` (more material; see its `GAP_ANALYSIS.md`,
  `WATCH_ANALYSIS_FINDINGS.md`, `parsed/`)

Page layout facts already established (demonstrated, see the doc section 3): 4,096-byte pages;
header with `page_size`, `num_tables`, `next_unused_page` (`export.pdb` has 20 tables; `exportExt.pdb`
has 9 with a different schema, see `devicesql_reader.py`, so do not assume one table layout for both); table pointer array; rows in a page
heap from offset 0x28; row groups of 36 bytes at the page end (16 reversed u16 offsets, u16 presence
bitmask, u16 unknown); short ASCII strings use header byte `((len+1) << 1) | 1`. The keys table
rows are `id:u32, id2:u32, name`, with `id2 == id`.

Out of scope for this task: building a pdb from the FableGear database (milestone 3), KEY/BPM
columns from Iron, playlists UI, artwork, USB export wiring, anything touching the UI.

## Done means

1. A round-trip test on the 21-track capture: parse -> serialise -> parse gives an equal
   model, for `export.pdb` and `exportExt.pdb`.
2. A byte-level comparison against the captured file (remembering it is post-deck) with an explicit, reviewed list of differences. If
   the output is byte-identical, say so; if not, each difference is named and explained or marked
   **unknown** (never guessed).
3. Fields we do not understand are carried through verbatim, not dropped or defaulted.
4. New tests, plus the existing suite, pass. `ruff check .` clean, `pyright` 0 errors.
5. A short section appended to `docs/devicesql_export_writer.md` stating what the round trip
   proved and what is still unknown. Label claims as demonstrated / inferred / unknown, as that doc does.

Do **not** claim the deck will accept the output. Nothing here is verified on hardware; only the
owner can run a stick on the CDJ-3000. Say "parses and round-trips", not "works".

## Rules (these have all been broken before; please keep them)

- **Never write to a real drive.** `/Volumes/Passport` is the owner's live library. Tests and
  experiments write only under `tmp_path` or a scratch directory. If a test touches
  `config.ARCHIVE_ROOT`, `SAVEPOINTS_DIR` or `MUSIC_ROOT`, redirect it to tmp in an autouse
  fixture. Never run anything against the owner's real `~/.fablegear` or `master.db`.
- **No new runtime dependency** without checking its licence. FableGear is proprietary and for
  sale; copyleft or noncommercial runtime dependencies are a real commercial risk (see
  `CLAUDE.md`). Do **not** copy code from `rekordcrate` or `crate-digger`; the format is
  documented by Deep Symmetry's "DJ Link Ecosystem Analysis", which the reader already cites.
  Write from the spec and from our own captures.
- **Do not guess at unknown bytes.** A wrong guess here produces a file the deck rejects with no
  useful error. Round-trip against real files is the safeguard.
- **Git hygiene.** Work on a new branch off `main` (e.g. `devicesql-writer-roundtrip`). Stage
  specific files by name, never `git add -A`; the working tree may hold the owner's uncommitted
  work. Do not push to `main`. Open a PR; CI runs tests, ruff and pyright; Copilot reviews once;
  fix real findings before merging.
- **Style.** Match the surrounding code (comment density, naming, type hints on public functions,
  no bare `except`, context managers for files). Docs and comments in plain English; label
  demonstrated / inferred / unknown.
- **Report faithfully.** If a check fails or a step is skipped, say so with the output.

## Checks to run

    python -m pytest tests -q            # whole suite (1117 passed at the time of writing)
    ruff check .
    pyright

## Historical material

The owner mentioned an earlier writer attempt this year. A search of the repo found **no writer
code**: only the reader (`chop_shop/devicesql_reader.py`), the plan doc, the two captures, and
commits such as `b961ff3` (plan), `b2c60fb` (row-index fix) and `7e8276d` (playlist recovery). If
you find anything older (`git log --all -S"export.pdb"`, other branches, `docs/`), read it and cite
it in your PR, but treat the captures as the source of truth over any older notes.
