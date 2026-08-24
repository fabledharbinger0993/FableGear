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
