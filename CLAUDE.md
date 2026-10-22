# FableGear — instructions for Claude

## Repository layout (since v1.3.0)

- **`fablegear/`** is the app, and the only folder the installer ships
  (`build_release.sh` packages `git archive HEAD:fablegear`). Every runtime file goes here.
- **`dev/`** is everything else: `dev/tests/` (run `python -m pytest dev/tests`),
  `dev/docs/` (research logs, audits, design notes), `dev/scripts/` (benchmarks and
  experiments), `dev/icon-source/` (icon build inputs), `dev/legacy/` (retired install
  paths), and `dev/requirements-dev.txt`.
- The repo root holds only tool config, `build_release.sh`, and a `launch.sh` shim for
  launchers created before the move. `dev/tests/test_payload_boundary.py` enforces all of this.
- In a source checkout the venv, `.fablegear_ready` and `fablegear.log` stay at the repo
  root; an installed copy keeps them beside the code.

## Iron / Anvil / Quench (tempo, key, beat-grid, tag I/O, and loudness)

**Before touching tempo, key, beat-grid, or downbeat/meter detection code (`fablegear/iron/`), tag
I/O code (`fablegear/anvil/`), or evaluating any third-party tempo/BPM/beat-tracking/tag library,
read `dev/docs/IRON_RESEARCH.md` in full.** It's the primary, consolidated research log —
current status, real accuracy findings against real Rekordbox ground truth, root causes,
what's been tried and reverted (with reasons, so you don't re-attempt a dead end), and the
active next step. Three other docs exist (`dev/docs/ANVIL_IRON_STATUS.md`,
`dev/docs/IRON_HANDOVER_2026-08-24.md`, `dev/docs/iron/RESEARCH.md`) — all three are superseded and
folded into `dev/docs/IRON_RESEARCH.md`, kept only for history.

For third-party tool license/technique due diligence specifically, `dev/docs/IRON_RESEARCH.md`
links to `dev/docs/IRON_TEMPO_RESEARCH.md`, a detailed per-tool catalog. **Add an entry there,
in its documented format, whenever you evaluate a new tempo/beat-tracking tool** — check
it first so you don't re-research one already listed.

**Why this matters**: Iron, Anvil and Quench exist specifically to get FableGear off
essentia (AGPL-3.0, removed in v1.2.0 — do not re-add it), librosa (weak accuracy; still a hard
requirement for the fallback BPM/key path, waveform decoding and the USB first-beat estimate),
and mutagen (GPL-2.0; still the general metadata reader and Anvil's read fallback). Do not
remove either without moving those callers first (dev/docs/IRON_RESEARCH.md §29.2) — because FableGear is for-sale, proprietary software, and a
copyleft or noncommercial license on a runtime dependency is a real commercial risk, not a
formality. `fablegear/quench/` (loudness measurement and levelling on ffmpeg, see `fablegear/quench/README.md`)
is the third in-house tool; Forge runs all three and each skips what a file already has. **Never add a third-party MIR, beat-tracking, or tag-I/O library as a runtime
dependency without checking its license against this constraint first** — including
model/weight files, which are sometimes licensed separately and more restrictively than
the code around them (see madmom's entry in the tool catalog for a concrete example).

**Multiple Claude sessions may be working on Iron/Anvil at once.** Check
`dev/docs/IRON_RESEARCH.md` and this branch's recent commits before starting work, so effort
doesn't collide or get silently duplicated. If you find work that conflicts with what
you're about to add, don't silently overwrite it — reconcile it in the text, or flag the
conflict in a new section and let a human resolve it.

When you finish a research or validation pass in this area, leave the repo in the state
`dev/docs/IRON_RESEARCH.md`'s own "How to add to this doc" section asks for: append a new
dated section rather than editing existing conclusions, and keep its "Current status" and
"Things NOT to re-litigate" sections current.
