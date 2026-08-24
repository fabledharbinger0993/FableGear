<<<<<<< HEAD
# FableGear — instructions for Claude

## Iron / Anvil (tempo, key, beat-grid, and tag-I/O detection)

**Before touching tempo, key, beat-grid, or downbeat/meter detection code (`iron/`), tag
I/O code (`anvil/`), or evaluating any third-party tempo/BPM/beat-tracking/tag library,
read `docs/IRON_RESEARCH.md` in full.** It's the primary, consolidated research log —
current status, real accuracy findings against real Rekordbox ground truth, root causes,
what's been tried and reverted (with reasons, so you don't re-attempt a dead end), and the
active next step. Two other docs exist (`docs/ANVIL_IRON_STATUS.md`,
`docs/IRON_HANDOVER_2026-08-24.md`) — both are superseded, kept only for history.

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

When you finish a research or validation pass in this area, leave the repo in the state
`docs/IRON_RESEARCH.md`'s own "How to add to this doc" section asks for: append a new
dated section rather than editing existing conclusions, and keep its "Current status" and
"Things NOT to re-litigate" sections current.
=======
# FableGear

## Working on Iron (BPM/key detection)?

Read `docs/iron/RESEARCH.md` before touching `audio_processor.py` or any
tempo/key-detection code. It's a living document — multiple Claude sessions
have worked on Iron in parallel, and it exists specifically so that work
doesn't get silently duplicated or overwritten.

- **Read it first.** It has the baseline accuracy numbers, what's already
  been fixed, what's been tried and rejected, and why (e.g. why Iron has to
  stay clean-room and can't borrow from essentia's AGPL-3.0 source).
- **Append, don't overwrite.** If you find something new, add a section
  with a date and what you did — don't replace another session's findings
  just because they're not yours. If you correct a previous claim (yours or
  someone else's), say so explicitly rather than quietly editing it away —
  see the doc's own `kicksPerBeat` section for the pattern to follow.
- **If multiple sessions are active at once**, check the doc and recent
  commits on this branch before starting work, so effort doesn't collide.
>>>>>>> origin/anvil
