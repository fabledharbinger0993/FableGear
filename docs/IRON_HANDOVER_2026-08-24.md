# Iron tempo accuracy: handover brief (2026-08-24)

**Read this first if you're picking up Iron/tempo work.** `docs/ANVIL_IRON_STATUS.md` is
the prior session's snapshot (2026-08-20) and is still useful background, but this doc
supersedes it on current state and next steps for tempo accuracy specifically.

**Repo location: work in `~/FableGear`, not the Downloads copy.** If you're in a
`.claude/worktrees/...` path under `Downloads/FableGear-main`, that's a stale, separate
clone — everything below happened in `~/FableGear` and isn't there.

**See also `docs/IRON_TEMPO_RESEARCH.md`** — the living catalog of every external
tempo/beat-tracking tool evaluated (license, algorithm, verdict), including all 10 tools
referenced in step 1 below in full detail. Add to that doc, not this one, when you
research another tool; this doc is a dated snapshot, that one is meant to keep growing.

---

## TL;DR

- Built `iron/beats.py` (downbeat_offset + time_signature detection). Works, tested,
  committed. Not the current priority.
- Ran Iron against essentia live, on the same real 150-track sample, for the first time
  ever (not just against old historical numbers). **Iron is meaningfully better than
  librosa but far behind essentia**, and — more importantly — **worse than librosa on
  MIREX**, which is a real, diagnosable problem, not noise.
- Root-caused it: **Iron's raw tempo pick (before any correction) systematically lands on
  HALF the true tempo on real music.** Genre-band correction is NOT the cause — it's
  currently the only thing keeping Iron's numbers as good as they are (removing it roughly
  halves every accuracy metric). This was a wrong hypothesis last session, tested and
  disproven with an ablation — see below.
- **Blocked** on a user-provided 15,000+-track USB drive (`Passport`, incl. Rekordbox
  ANLZ beat-grid exports) that isn't mounting. Needs the user to check the physical
  connection.
- **Next step, once unblocked or even without it**: fix Pass 1's half-time bias in
  `iron/tempo.py`. Two candidate approaches below, not yet attempted.

---

## What happened this session, in order

1. Reviewed 8 external tempo/beat-tracking projects for license + technique, comparing
   against Iron: `realtime-bpm-analyzer`, `bpm-detector` (libraz), Beat-and-Tempo-Tracking
   (BTT, Krzyzaniak), `loop-tempo-estimator` (Audacity/saintmatthieu),
   `web-audio-beat-detector`, SoundTouch's `BPMDetect`, `phip1611/beat-detector`,
   `BeatNet`, `beat_this` (CPJKU), `madmom` (CPJKU).
   - **madmom**: code is permissively licensed, but its pretrained RNN/DBN models are
     **CC-BY-NC-SA — a hard commercial blocker**, same problem class as essentia's AGPL.
   - **beat_this**: MIT for both code AND published weights — cleanest license found.
     User's call: use it as an **offline validation oracle only**, never a runtime
     dependency. `scripts/benchmark_iron_beats.py` implements this (needs
     `pip install beat-this`, not in any requirements file, dev-only tool).
   - Mined two techniques (read the published method, clean-room reimplemented — never
     copied code, same posture Iron already used for essentia): BTT's cumulative
     beat-strength tracking, and loop-tempo-estimator's tatum-hypothesis idea (scoring
     small-integer groupings by onset alignment).

2. Built `iron/beats.py` — detects `downbeat_offset` (beat-grid anchor) and
   `time_signature` (4/4 vs 3/4), opt-in via `analyze(want=(..., "downbeat_offset"))`.
   Built on `iron.dsp.track_beats` (an existing, already-tested DP phase-locker) plus a
   new `iron.dsp.band_energy` (kick-band accent feature). Found and fixed two real bugs
   along the way (not just tuning):
   - `track_beats`'s DP penalty is scale-dependent and was "double-timing" (locking onto
     both kicks and the hi-hats between them) when fed `onset_envelope`'s raw units —
     fixed by normalizing to unit scale before tracking.
   - Sampling the kick-band accent feature at the phase-locked frame missed the actual
     transient entirely — broadband flux peaks ~150-200ms *after* a percussive
     transient's true attack (an STFT-windowing artifact for transients spanning several
     hops), so a raw energy feature sampled there lands in the decay tail. Fixed with
     `_accent_strength`'s backward local-max search.
   - End-to-end validated on a synthetic BPM sweep (`tests/test_iron_beats.py`), not yet
     against real music.

3. Committed everything to `main` in `~/FableGear` (2 commits — the working tree had a
   lot of other pre-existing uncommitted work too, now landed):
   - `e20a581` — Anvil FLAC/MP4/OGG/Vorbis-comment support (pre-existing, not written this
     session, just committed)
   - `755bf5d` — Iron's tempo stability verification (pre-existing) + the new beat-grid
     work (this session)

4. **Ran a live 150-track benchmark**: Iron vs essentia's actual production function
   (`audio_processor._detect_bpm_essentia`), both scored against the same random sample
   of real Rekordbox ground-truth BPMs from the user's live 209,079-track library
   (`~/Library/Pioneer/rekordbox/master.db`). Script: `scripts/live_compare_iron_essentia.py`.

   | | Iron | essentia (live, same sample) | librosa (historical, 12,687 tracks) |
   |---|---|---|---|
   | exact (±0.6 BPM) | 42.0% | 83.0% | 13.4% |
   | within 1% | 48.0% | 88.4% | 36.8% |
   | MIREX (±4%) | 50.0% | 92.5% | 90.7% |

   essentia's own live number (83.0%) also came in below its historical baseline (91.4%)
   — this library is far more genre-diverse (jazz standards, acoustic rock, disco, funk,
   hip-hop all mixed in with house/techno) and messier (several corrupt/truncated files)
   than a curated benchmark set.

5. **The real finding: Iron loses to librosa's historical MIREX number (50.0% vs
   90.7%)**, and with n=150 that gap is far outside sampling noise. Formed a hypothesis:
   genre-band correction, tuned around DJ/EDM tempo clusters, must be actively hurting on
   this genre-diverse library. **Tested it with an ablation and it was wrong.**

6. Ablation (`scripts/ablate_genre_bands.py`): same 150-track sample, each file decoded
   once, `iron.tempo.detect_tempo()` run twice on the same audio — once normally, once
   with genre-band correction disabled via monkeypatching `tempo._in_genre_band` to
   always return `True` (no source changes).

   | | band ON (current) | band OFF |
   |---|---|---|
   | exact | 42.0% | **21.3%** |
   | within 1% | 48.0% | **23.3%** |
   | MIREX | 50.0% | **24.7%** |

   Disabling the correction **roughly halves every metric**. Of the 97 tracks where it
   changed the answer: **42 fixed, only 4 broken** — a 10.5:1 net positive. Genre-band
   correction is load-bearing, not the problem.

   The actual pattern, visible in the per-track ratio breakdown: Iron's **raw** (Pass-1,
   pre-correction) pick is overwhelmingly landing at **~0.5x the true tempo** — a
   systematic half-time bias. Genre-band correction happens to fix a lot of these by
   coincidence (doubling a raw half-time miss back into a defined band), but only helps
   when the true tempo falls inside one of the 7 hand-picked bands. Tracks outside all of
   them (slower soul/jazz/funk, mostly) get no help and stay wrong.

   Also surfaced a **secondary, minor bug**: 4 of the 150 tracks got WORSE with
   correction on — cases where Pass 1's raw answer was already correct, but Pass 2's
   correction changed `chosen_lag` before Pass 4 (breakdown-duration structural fit) got
   to run, and Pass 4 then landed somewhere worse than it would have on the original
   lag. Rare, not urgent given the 10.5:1 ratio, but a real Pass-2→Pass-4 interaction
   worth a look eventually. See the printed per-track table in the ablation script's
   output for the specific tracks (`Turquoise Hexagon Sun`, `No Clue`, `Pansit Acid`,
   `Rennie Foster - FREE EDITS`).

---

## Why the half-time bias is plausible, not just a bug

This isn't unique to Iron — half/double-time tempo ambiguity is a famously hard, general
MIR problem (essentia's own multifeature ensemble handles it much better, but even it
dropped from 91.4% to 83.0% live on this messier, more diverse library). The specific
mechanism in `iron/tempo.py`'s harmonic-sum scoring (`_harmonic_score` in
`iron/tempo.py`): a candidate at HALF the true tempo can "borrow" credit from the true
tempo's own strong autocorrelation peak, because that peak sits at exactly 2× the
half-time candidate's own lag — i.e. it looks like the half-time candidate's "2nd
harmonic" even though it's actually the real fundamental. The synthetic kick+hi-hat test
fixtures (`tests/test_iron_tempo.py`) don't expose this as severely — real music has much
richer harmonic/rhythmic content (basslines, snares, offbeat elements) that the clean
synthetic pattern doesn't reproduce.

## Next step (not yet attempted)

Fix Pass 1's half/double disambiguation directly. Two candidate approaches, not mutually
exclusive:

1. **Strengthen the half-time penalty in harmonic-sum scoring** — the current formula
   doesn't distinguish "this candidate's multiples are real periodicities of its own
   pulse train" from "this candidate's 2nd harmonic happens to be a different, stronger
   fundamental." Needs a term that specifically checks whether a candidate's OWN
   fundamental-lag strength (not just its harmonics) is what's actually driving its score.
2. **Add a log-Gaussian tempo prior** (BTT-inspired, not yet built) — a continuous,
   not box-shaped, bias against implausibly slow picks when a faster candidate scores
   comparably. Different mechanism than genre bands; could catch true tempos that fall
   between or outside the current 7 bands (this library has plenty of those — jazz
   ballads, slower funk/soul).

**This is real surgery on Iron's most heavily-tested component.** Before and after any
change, run:
```
pytest tests/test_iron_tempo.py tests/test_iron_dsp.py tests/test_iron_beats.py -v
```
(12/13 synthetic BPM sweep + the documented 190 BPM xfail must stay green — a regression
there means the fix broke something the synthetic fixtures WERE catching correctly.) Then
re-run `scripts/ablate_genre_bands.py` and `scripts/live_compare_iron_essentia.py` on the
same seed=42 sample to measure real impact before declaring victory.

---

## Current blocker: `Passport` USB drive not mounting

User attached a drive (referred to as `Passport`) with 15,000+ tracks intended as a much
larger real-world bounce-test set, plus these specific paths flagged as worth checking:

```
/Volumes/Passport/PIONEER/Master/share/PIONEER/USBANLZ
/Volumes/Passport/.Spotlight-V100
/Volumes/Passport/DJMT_PIONEER
/Volumes/Passport/FableGear Archive/Reports
/Volumes/Passport/FableGearToolAudit_20260504_140402
```

**`USBANLZ` is worth real attention when it's accessible**: that's Rekordbox's binary
ANLZ beat-grid export format (what a CDJ actually reads off a prepared USB). It would give
actual CDJ-grade downbeat/beat-grid ground truth for validating `iron/beats.py` —
materially better than the `beat_this`-oracle approach, since it's Rekordbox's own
professional analysis rather than a third-party estimate. `pyrekordbox` (already a
FableGear dependency) has ANLZ-parsing support — check its `anlz` module.

As of this session's end, the drive was **not detected** — checked `/Volumes`,
`diskutil list`, and USB/Thunderbolt device enumeration (`system_profiler`), nothing
named `Passport` showed up anywhere. (There's an unrelated volume mounted as `I` on this
Mac's existing Time Machine backup disk — not touched, not relevant, permission-protected
anyway.) **First thing to do: ask the user to confirm it's physically connected and
mounted**, then explore those five paths — especially `USBANLZ` and the two `FableGear`-
named folders (`Archive/Reports`, `ToolAudit_20260504_140402`), which may contain prior
audit findings or ground-truth data worth reusing before rebuilding anything from scratch.

---

## Tools available for continuing this work

- `scripts/live_compare_iron_essentia.py --sample N --seed S` — Iron vs essentia, live,
  same sample, same ground truth. Use this to measure real progress on any tempo.py change.
- `scripts/ablate_genre_bands.py --sample N --seed S` — genre-band on/off ablation with
  per-track ratio-bucket diagnostics (`~0.5x`, `~2x`, etc.) and a HELPED/HURT/neutral
  breakdown. Reusable for testing ANY future Pass 1/2 change, not just this one.
- `scripts/benchmark_iron_tempo.py` — the original, simpler Iron-only benchmark against
  Rekordbox ground truth (prints old historical essentia/librosa numbers for reference,
  doesn't run them live).
- `scripts/benchmark_iron_beats.py` — beat-grid/downbeat validation against `beat_this`
  as ground truth (needs `pip install beat-this`, not yet run — no real beat/downbeat
  ground truth was validated against real music this session, only synthetic fixtures).

All four take `--seed 42` by default for reproducibility; the 150-track sample referenced
throughout this doc is `--seed 42 --sample 150` specifically, reconstructible from the
live 209,079-row `master.db`.

## Things NOT to re-litigate

- Genre-band correction is net-positive (10.5:1). Don't remove or weaken it without first
  confirming the same ablation shows a net-negative result on whatever's actually broken.
- `beat_this` stays offline/dev-only — user explicitly chose this over adding it as a
  runtime dependency.
- `madmom` is out — its models are CC-BY-NC-SA, incompatible with a for-sale app.
- Iron's `time_signature`/`downbeat_offset` work is validated on synthetic fixtures only;
  don't claim real-music accuracy for it without running `benchmark_iron_beats.py` (or,
  once available, the ANLZ ground truth) first.
