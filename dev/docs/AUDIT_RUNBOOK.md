# FableGear audit runbook

How to run the two standing audits on this repo: Iron/Anvil/Quench analysis
accuracy (tempo + key vs Essentia and Rekordbox), and the Record Room UI/UX
audit. Written from what's actually in the repo as of commit `fc9d066`
(2026-11-15) — paths and commands below were confirmed against the files
themselves, not assumed. Where something couldn't be confirmed directly,
it's marked **inferred** or **unconfirmed**.

This doc is the "how to run it" companion to the standing docs that already
own the content: `dev/docs/IRON_RESEARCH.md` owns Iron's research history,
`dev/docs/DESIGN.md` and `dev/docs/PRODUCT.md` own the Record Room/Chop Shop
UX contract. Don't duplicate their conclusions here — read them.

---

## Part 1 — Iron / Anvil / Quench accuracy audit

**Read `dev/docs/IRON_RESEARCH.md` §1 ("Current status") and §5 ("Things NOT
to re-litigate") in full before touching any tempo/key/beat-grid code or
re-running a benchmark.** It is the primary research log; this section only
covers *how to execute* the tools it describes.

### 1.1 Setup

Same as `CLAUDE.md`'s standing setup, repeated here because the benchmark
scripts need it too:

```bash
python3.13 -m venv venv
venv/bin/python -m pip install -r fablegear/requirements.txt -r dev/requirements-dev.txt
command -v ffmpeg fpcalc && node --version   # expect node v22.x
```

The benchmark scripts live in `dev/scripts/` and are run directly with
`python3`/`venv/bin/python`, not through pytest — they're standalone reports,
not test assertions. (The handful of pytest tests that *do* exist for this
area — `test_iron_tempo.py`, `test_iron_key.py`, `test_iron_beats.py`,
`test_iron_dsp.py`, `test_iron_stability.py`, `test_benchmark_key_bpm_vs_rekordbox.py`,
`test_anvil_*.py`, `test_quench_*.py` — run with everything else via
`python -m pytest dev/tests -q` and check code correctness, not analysis
accuracy.)

### 1.2 Pick the reference database — do this first

Per `dev/docs/IRON_RESEARCH.md` §21/§31.5 and this project's own memory: use
a **local Rekordbox 7 `master.db`**, matched to library files by exact
`FolderPath`. Do **not** use the Passport drive's `master.db` snapshot — its
`FolderPath` values go stale after any library reorganization on the
Passport drive, which is the documented root cause of unreliable matches in
`benchmark_iron_genre_diverse.py`'s own docstring.

Snapshot it safely (never read the live file while Rekordbox is running):

```bash
cp ~/Library/Pioneer/rekordbox/master.db{,-wal,-shm} /path/to/snapshot/
```

The canonical reference-DB choice (Rekordbox 7 DB vs. Passport snapshot) was
asked of Marshall per project memory but — as far as this doc's author could
verify — not yet confirmed in writing. Treat "local Rekordbox 7 DB" as the
working default until Marshall says otherwise.

### 1.3 Run the benchmarks

All of these live in `dev/scripts/`. Pass `--help` to any of them for the
authoritative, current argument list — the summaries below are read from
each script's own docstring/argparse, not from having run them in this
session, so treat exact flag names as a starting point to verify, not gospel.

| Script | What it measures | Typical invocation |
|---|---|---|
| `benchmark_key_bpm_vs_rekordbox.py` | Iron vs Essentia, both scored against a Rekordbox-exported JSON manifest ground truth. Reports exact/within-1%/MIREX for BPM (with double/half/3:2/2:3 miss-class breakdown) and Camelot-relation-weighted key score. | `python3 dev/scripts/benchmark_key_bpm_vs_rekordbox.py --manifest set/manifest.json --engines iron,essentia --workers 6 --out results/bench.jsonl --summary-out results/bench_summary.json` |
| `benchmark_iron_tempo.py` | Iron tempo only, vs a live Rekordbox DB (or CSV). Prints exact/within-1%/MIREX against the historical Essentia (91.4/94.8/98.3%) and librosa (13.4/36.8/90.7%) baselines quoted in IRON_RESEARCH.md. | `python3 dev/scripts/benchmark_iron_tempo.py --rekordbox-db /path/to/master.db --sample 300` |
| `benchmark_iron_key.py` | Iron key vs Rekordbox `DjmdContent.KeyName`, A/B'd in the same run against the older CQT+Krumhansl-Kessler detector. | `python3 dev/scripts/benchmark_iron_key.py --rekordbox-db /path/to/master.db --sample 300` |
| `benchmark_iron_genre_diverse.py` | Tempo+key with **no DB dependency** — ground truth read straight from embedded file tags, stratified by BPM band → genre → length. | `python3 dev/scripts/benchmark_iron_genre_diverse.py --root /Volumes/Passport/DATABASE --count 1000 --bpm-min 60 --bpm-max 180` — **the `--bpm-min`/`--bpm-max` flags are not optional in practice**; its own defaults don't inherit Iron's tuned default range, a documented gotcha in IRON_RESEARCH.md §9.2. Omitting them has previously produced a false "DnB is a weak spot" reading. |
| `live_compare_iron_essentia.py` | Live Iron vs librosa (Essentia isn't runnable in CI/this environment) vs a Rekordbox bonus column, with SIGALRM hang detection for pathological files. | `python3 dev/scripts/live_compare_iron_essentia.py --root /Volumes/Passport/DATABASE --sample 1000` or `--format-stratified --formats mp3,aiff --per-format 100` |
| `categorize_iron_bpm_mismatches.py` | Post-hoc: classifies a prior benchmark JSONL's BPM misses into match/2x/half/3:2/2:3/4:3/3:4/other, grouped by folder and ground-truth value. Run this on any benchmark output before concluding "Iron is just wrong" on a batch of misses — most mismatches turn out to be a clean octave/compound-meter relationship, not noise. | `python3 dev/scripts/categorize_iron_bpm_mismatches.py --in results/bench.jsonl` |
| `validate_iron_golden.py` | Schema-validates `dev/docs/iron/golden/iron_golden_labels.jsonl`, the small set of ear-verified ground-truth rows. | `python3 dev/scripts/validate_iron_golden.py` |
| `dev/scripts/testbed/refresh_rb_bench.py` | Snapshot → run → score against a Rekordbox snapshot, end to end. | see script `--help` |
| `dev/scripts/experiments/tempo_holdout.py` | The current freshest tempo hold-out tool (sample/run/score subcommands) — this is what produced the 83.0%/92.4% exact/within-4% numbers in IRON_RESEARCH.md's "Current status." | see script `--help` |
| `dev/scripts/experiments/key_lab_extract.py` + `key_lab_scale.py` | Key-net retraining/feature pipeline, used together per §31.5's reproduce steps. | see scripts `--help` |

The music library referenced by `--root` is `/Volumes/Passport/DATABASE` —
project memory notes this only works while the Passport drive is mounted.

### 1.4 Reading the half-time / 2:3 results specifically

This project's main open accuracy gap is drum & bass / jungle tempo, where
Iron tends to under-read by a clean octave (half-time) or a 2:3 ratio versus
Rekordbox. Before changing any detection code over a DnB mismatch:

1. Run `categorize_iron_bpm_mismatches.py` on the benchmark output first —
   it separates genuine errors from clean ratio relationships automatically.
2. Check whether the mismatch is **half-time** or **2:3**. Per
   `dev/docs/IRON_RESEARCH.md` §19.9 (the section behind the ear-check
   already in project memory): on the half-time group, 5/6 sampled tracks
   confirmed Rekordbox's faster reading (the drum pulse) as the
   beatmatch-safe convention, with 1/6 ("Papa T Good and Bad") confirming
   Iron's slower reading was actually correct. On the 2:3 group, **all 6**
   sampled tracks confirmed Rekordbox was right and Iron's reading was a
   genuine error, not a tempo-convention difference.
3. There is an open, unresolved product question in IRON_RESEARCH.md: for
   160–185 BPM DnB, should FableGear report the drum-pulse tempo
   (Rekordbox's convention, beatmatch-safe) or the felt/dance tempo? The doc
   currently recommends drum-pulse but this is not finalized. Don't "fix"
   half-time cases by nudging detection toward Iron's number without
   checking this question first — some of those are a convention
   disagreement, not a bug.
4. A ready-made worklist for continuing this ear-check exists at
   `~/FableGearTestbed/results/tempo_earcheck.json` (on Marshall's machine,
   not in the repo) — 91 Iron-disagreements at Rekordbox 160–185 BPM, with
   an empty `owner_label` field meant for Marshall to fill in by ear.

### 1.5 Logging what you find

`dev/docs/IRON_RESEARCH.md` has its own required format for additions (from
its own "How to add to this doc" section, quoted): append a new dated
section (`## N. <short title>`) rather than editing existing sections'
conclusions — if you disprove something above, add a note pointing at it
rather than rewriting history — and keep §1 ("Current status") and §5
("Things NOT to re-litigate") current, since those are the two sections
everyone actually reads.

Multiple sessions may be working on Iron at once — check recent commits and
this doc's latest dated entries before starting, so two sessions don't
silently duplicate or collide on the same experiment.

---

## Part 2 — Record Room UI/UX audit

Record Room is the database-layer surface (library browsing, playlists,
metadata, staging) and must read as a curated library tool, not a file
browser. Chop Shop is the physical file-layer surface (conversion, renaming,
fingerprinting, dedup). This split is deliberate, defined in
`dev/docs/PRODUCT.md` ("deliberate architectural and UX boundary, not just a
folder name") and given a visual contract in `dev/docs/DESIGN.md`:

> Record Room is database precision — Rekordbox truth, read carefully,
> written cautiously, lit in **Signal Cyan** (`#00d4e8`). Chop Shop is
> physical file work — hands on the actual audio, lit in **Chop Magenta**
> (`#ff2d78`).

DESIGN.md calls this **"the One Room, One Light Rule"**: only one room's
accent color is active at a time, never both in the same chrome. It also
names an explicit anti-reference — Lexicon (light, gradient-heavy library
apps) — as what FableGear should never read as.

### 2.1 Where Record Room actually lives in the repo

- `fablegear/templates/partials/record_room/deck.html` — the one Record Room
  template file found; most of the room's behavior is JS-driven rather than
  server-templated.
- `fablegear/static/record_room/`: `context_menu.js`, `deck.js` (~37KB — the
  main player/deck logic, worth starting here), `library_editor.js`,
  `library_mode.js`, `usb_export.js`.
- Chop Shop's templates live at `fablegear/templates/partials/physical_library/`
  (`duplicate_prune.html`, `file_converter.html`, `file_rename.html`,
  `fingerprinting.html`, `novelty.html`, `organizer.html`,
  `pipeline_wizard.html`) and its JS at `fablegear/static/chop_shop/`.
  **Flag**: the template folder is named `physical_library`, not
  `chop_shop` — a naming mismatch against the product terminology that's
  worth raising with Marshall rather than silently "fixing," since renaming
  it touches routes and tests.
- `fablegear/static/fablegear.css` is one 290KB stylesheet shared by both
  rooms (not split per room). `DESIGN.md` already documents real
  inconsistencies here — roughly 20 hardcoded `border-radius` values instead
  of tokens, and a duplicated `.btn-xs` rule defined twice with disagreeing
  values at two different line locations. Both are concrete, pre-identified
  starting points for a CSS pass; re-verify current line numbers before
  editing, since the file is large and has likely moved since DESIGN.md was
  written.
- Player/library routes: `fablegear/routes_player.py` (75KB),
  `fablegear/library_browser/` directory, `fablegear/playlist_linker.py`,
  `fablegear/playlist_recovery.py`.

### 2.2 What to actually check

Audit against the "curated library, not Finder" standard means walking the
Record Room flows and asking, concretely:

1. **Folder-first chrome creeping in.** Does any Record Room view expose raw
   filesystem paths, a tree/folder picker, or "browse to a file" affordance
   as the *primary* interaction, instead of track/record metadata (title,
   artist, key, BPM, playlist)? A file path appearing as *metadata on a
   record* is fine; a folder tree as the *navigation model* is not.
2. **Room identity bleed.** Any view where Chop Magenta and Signal Cyan both
   appear as active accents in the same chrome — a One Room, One Light Rule
   violation. Check modals/wizards that might be shared between rooms
   (`undo_wizard.html`, `staging_panel.html` are shared partials, not
   room-scoped — worth checking first).
3. **Track/record centrality.** In library browsing, import, playlists,
   staging, and metadata-action flows, is the track/record the thing you
   act on, or is it a file you happen to also see metadata for? Read
   `deck.js` and `library_mode.js` for the actual interaction model before
   forming a view here — don't infer this from templates alone, since the
   room is JS-driven.
4. **CSS debt called out in DESIGN.md** — the `.btn-xs` duplicate and the
   hardcoded `border-radius` values — plus any new instances found while
   reading `fablegear.css`.

### 2.3 Running the relevant tests

Record-Room-named pytest tests already exist and should pass before and
after any change:

```
test_record_room_context_actions.py
test_record_room_device_export.py
test_record_room_iron_anvil.py
test_record_room_live_refresh.py
test_record_room_path_escaping.py
test_record_room_player_wiring.py
```

These run with the rest of the suite via `python -m pytest dev/tests -q`.
There is **no separate JS test suite specific to Record Room** —
`dev/tests/test_static_js_syntax.py` parses *every* shipped `.js` file
(Record Room's, Chop Shop's, and shared) with `node --check`, and skips
entirely if `node` isn't installed rather than failing. CLAUDE.md's mention
of "JS syntax, Record Room and installer tests" using node refers to this
one umbrella test plus the separate installer tests
(`test_installer_wizard.py` / `test_installer_launcher.py`, which are
Python, not JS, despite exercising the installer's JS-adjacent behavior) —
there are not three distinct JS suites. Verify this stays accurate if the
test layout changes.

### 2.4 Standard checks

Same three CI gates as everything else, confirmed against
`.github/workflows/ci.yml`:

```bash
ruff check .
pyright .                                # must be 0 errors
venv/bin/python -m pytest dev/tests -q   # check for "skipped" — means ffmpeg or node is missing
```

CI's `test` job runs on `macos-latest`, installs ffmpeg via Homebrew, pins
Node 22 via `actions/setup-node`, and writes a stub `~/.fablegear/config.json`
before running pytest (required because `config.py` reads it at import
time) — replicate that stub locally if you hit import-time config errors
running tests standalone.

---

*This runbook describes tooling and docs as found in the repo; it does not
replace reading `dev/docs/IRON_RESEARCH.md`, `dev/docs/DESIGN.md`, or
`dev/docs/PRODUCT.md` directly before doing substantive work in either area.*
