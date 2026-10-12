# FableGear — instructions for Claude

## Iron / Anvil (tempo, key, beat-grid, and tag-I/O detection)

**Before touching tempo, key, beat-grid, or downbeat/meter detection code (`iron/`), tag
I/O code (`anvil/`), or evaluating any third-party tempo/BPM/beat-tracking/tag library,
read `docs/IRON_RESEARCH.md` in full.** It's the primary, consolidated research log —
current status, real accuracy findings against real Rekordbox ground truth, root causes,
what's been tried and reverted (with reasons, so you don't re-attempt a dead end), and the
active next step. Three other docs exist (`docs/ANVIL_IRON_STATUS.md`,
`docs/IRON_HANDOVER_2026-08-24.md`, `docs/iron/RESEARCH.md`) — all three are superseded and
folded into `docs/IRON_RESEARCH.md`, kept only for history.

For third-party tool license/technique due diligence specifically, `docs/IRON_RESEARCH.md`
links to `docs/IRON_TEMPO_RESEARCH.md`, a detailed per-tool catalog. **Add an entry there,
in its documented format, whenever you evaluate a new tempo/beat-tracking tool** — check
it first so you don't re-research one already listed.

**Why this matters**: Iron and Anvil exist specifically to get FableGear off essentia
(AGPL-3.0), librosa (weak accuracy, kept only as an already-measured baseline), and
mutagen (GPL-2.0) — because FableGear is for-sale, proprietary software, and a copyleft
or noncommercial license on a runtime dependency is a real commercial risk, not a
formality. **Never add a third-party MIR, beat-tracking, or tag-I/O library as a runtime
dependency without checking its license against this constraint first** — including
model/weight files, which are sometimes licensed separately and more restrictively than
the code around them (see madmom's entry in the tool catalog for a concrete example).

**Multiple Claude sessions may be working on Iron/Anvil at once.** Check
`docs/IRON_RESEARCH.md` and this branch's recent commits before starting work, so effort
doesn't collide or get silently duplicated. If you find work that conflicts with what
you're about to add, don't silently overwrite it — reconcile it in the text, or flag the
conflict in a new section and let a human resolve it.

When you finish a research or validation pass in this area, leave the repo in the state
`docs/IRON_RESEARCH.md`'s own "How to add to this doc" section asks for: append a new
dated section rather than editing existing conclusions, and keep its "Current status" and
"Things NOT to re-litigate" sections current.
