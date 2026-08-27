# Iron research log (consolidated, living document)

**If you are a Claude instance picking up work on `iron/` (tempo or key detection): read
this whole file before touching `iron/tempo.py`, `iron/dsp.py`, `iron/beats.py`, or
`iron/api.py`.** It replaces `docs/ANVIL_IRON_STATUS.md` (2026-08-20 snapshot),
`docs/IRON_HANDOVER_2026-08-24.md` (same-day, earlier session), and `docs/iron/RESEARCH.md`
(a third, independently-started log — folded in at §7) as the single up-to-date research
record — all three are kept for archaeology but are superseded by this file. Add your own
findings to this file (append, don't delete — see "How to add to this doc" at the bottom)
rather than starting a new dated handover doc; that's exactly the fragmentation this file
exists to stop. Multiple same-day sessions have independently produced real, non-
overlapping findings on the same code before any of them knew about the others — that gap
is what this file is for. `CLAUDE.md` points here; if you find yet another stray research
doc that isn't listed above, fold it in the same way rather than leaving a fourth copy.

**Repo location: work in `~/FableGear`.** If you're in a `.claude/worktrees/...` path or a
`Downloads/FableGear-main` copy, that's a separate clone — none of this history is there.

---

## 0. Why Iron and Anvil exist at all

`iron/` (tempo + key detection) and `anvil/` (audio-file tag I/O) are clean-room, in-house
replacements for functionality the app currently gets from **essentia** (AGPL-3.0 /
commercial dual-licensed), **librosa**'s tempo fallback, and **mutagen** (GPL-2.0) — all
copyleft licenses that are a real risk for a for-sale, closed-source app. Both packages are
independent implementations of published, non-proprietary methods (see each module's
docstring for citations), not ports of any dependency's internals.

**Neither package is wired into the live app yet.** `audio_processor.py`,
`waveform_generator.py`, and every other existing caller still use mutagen/essentia/librosa
exactly as before. This is deliberate — see "Current status" below for what's blocking that
decision. Nothing about working on `iron/`/`anvil/` risks the shipping app.

---

## 1. Current status (as of 2026-08-27)

**Anvil**: functionally complete. ID3v2.3/2.4 (MP3/WAV/AIFF), Vorbis comments (FLAC/OGG),
and MP4/M4A ilst tags all implemented, tested against real files, and cross-validated
against mutagen for read/write round-trips. Not a currently active area of research —
`docs/ANVIL_IRON_STATUS.md` has the implementation detail if you need it.

**Iron tempo**: three independent accuracy problems have been found across sessions, on
different real-music samples. Read all three — they are not the same bug, and a fix for one
is not guaranteed to fix another:

1. **The 2:3 compound-meter ("disco cluster") problem** — §2 below. A real, validated fix
   candidate (`energy_flux`, §2.4) exists and is **still not merged** — see §8.7, this is
   unchanged from before this session.
2. **The half-time bias problem** — §3 below. §3's own "next step" list is now **superseded
   by §8**: multiband scoring and a cyclic-tempogram octave correction (two of that list's
   candidate directions) have since been implemented and validated; a third candidate
   (DP transition-penalty variance) was tried and reverted for a new, distinct reason — see
   §8.2-§8.4. The specific list in §3 is kept for history but should not be read as current.
3. **A large-scale (996-track), genre-diverse, non-Rekordbox-sourced benchmark** — §8.1/§8.6
   — surfaces real, still-largely-unexplained error (43.7% of wrong answers don't fit any
   clean octave/compound-meter ratio at all). This is the current largest open problem, not
   covered by any fix above.

**Open question, still not tested**: does the §2.4 `energy_flux` onset feature swap
interact with (help, hurt, or duplicate) §8's multiband scoring and cyclic tempogram? Nobody
has tried combining or comparing them yet — see §8.7.

**A candidate technique for the §2 disco-cluster problem that still hasn't been tried**: the
kick-onset-interval ("four-on-the-floor") gate from §7.2 — still not ported.

**Iron key**: no longer purely an unaddressed gap — see §8.5. A CQT-based chroma
(`iron.dsp.chroma_cqt`) is now in production use in `iron/key.py`, validated at real,
meaningful improvement (see §8.5 for numbers). The segment-wise/window gaps flagged in §7.4
remain unaddressed.

**Both `iron/tempo.py`'s default search range (`bpm_min`/`bpm_max`) and `_GENRE_BANDS`
changed this session** — see §8.6 for what changed and the validated real-world impact.

---

## 2. The 2:3 compound-meter ("disco cluster") problem

### 2.1 What it is

On a 130-track real Rekordbox-verified set (75-160 BPM range, `bpm_min=75, bpm_max=160`
passed to `detect_tempo`), Iron's baseline accuracy was:

| | exact (±0.6 BPM) | within 1% | MIREX (±4%) |
|---|---|---|---|
| Iron (spectral_flux, current production) | 69.2% | 70.8% | 70.8% |
| essentia (live, same sample) | 93.1% | 96.9% | 98.5% |
| librosa (live, same sample) | 18.5% | 43.8% | 89.2% |

(Key detection, same sample: Iron 18.5% exact match vs Rekordbox, librosa 24.6% — a
separate, still-open, still-unaddressed gap. Not investigated further this session.)

Of Iron's disagreements, ~29 tracks (concentrated in disco/soul/nu-disco edit-pack
material — "Audiowhores" edits, "That's Not An Edit" comps, "Word Of Mouth" white labels)
shared a specific, consistent pattern: Iron's answer was almost exactly **2/3 of the true
tempo** (ratio ≈ 0.665-0.668), not a clean octave (0.5x/2x) error. This is a genuine
metrical ambiguity, not a bug: these tracks' rhythm section genuinely supports both a
"fast" 3-feel reading and a "slow" 2-feel reading of the same pulse train (a real compound-
meter/hemiola pattern common in disco/soul), and Iron's autocorrelation-plus-harmonic-sum
scoring (see `iron/tempo.py`'s module docstring) has no mechanism to prefer one over the
other — both readings share overlapping harmonic content.

### 2.2 What was tried and failed (all reverted, all documented in code — do not re-attempt
blind, read the cited docstring first for exactly why each failed)

1. **Compound-meter harmonic-credit weighting** — adding a term to `_harmonic_score` in
   `iron/tempo.py` giving asymmetric credit for 1.5x-related lags. Reverted: didn't flip the
   target case (both candidates share too much overlapping harmonic content for an
   across-the-board weight to separate them) and broke an existing synthetic regression
   test. See the docstring on `_harmonic_score` in `iron/tempo.py`.
2. **DP beat-tracking cross-period comparison** — using `dsp.track_beats`'s path score to
   compare candidate periods directly. Reverted: a real, confirmed bias toward faster
   periods (more beat slots in a path = more chances at favorable alignment, independent of
   whether the period is musically real) that survived both raw and baseline-corrected
   normalization, and broke several already-correct synthetic cases in a full sweep. See
   the docstring on `dsp.track_beats`. `track_beats` itself is still used, but only as a
   validated single-period phase-locking primitive (and now also by `iron/beats.py`'s
   downbeat/meter detection, §4) — never for cross-period comparison.
3. **Gemini's low-band gate** (external AI proposal, first round) — tested directly against
   real data, did not hold up. Not committed; not documented further since it's a dead end,
   not a partial win.

### 2.3 What was tried and kept (real, validated, conservative, currently in production)

- **Genre-band padding + full-range rival scan** (Pass 2 in `detect_tempo`) — fixed the
  original octave-halving bug this investigation started from. Validated at scale, held up
  through everything since.
- **Breakdown-duration bar-fit** (Pass 4 in `detect_tempo`, using
  `dsp.find_breakdown_duration`) — a narrow, strictly-gated structural-convention check
  (breakdown/bridge length should land near a conventional bar count — 8/16/32 — for the
  true tempo). Fixes at least one concrete real case, deliberately conservative (one-sided
  tight/loose gate, not "whichever is closer"), doesn't move the aggregate number much on
  its own. See the long Pass 4 comment in `iron/tempo.py` for the exact gating logic and
  why a naive comparison was rejected.
- **Body-window decoding** (`iron/api.py`'s `_pick_body_window` — analyze roughly 1/3
  through to 90% of the track, not a fixed window from 0:00) — built so Pass 4 would have a
  real mid-track breakdown to find. Unplanned bonus: nearly doubled key-detection accuracy
  too (10.0% → 18.5%) despite no change to key-detection logic, presumably because the
  track body is more harmonically representative than an intro.
- **Long-baseline tempo stability check** (`iron.analyze(..., verify_stability=True)`,
  `iron/api.py::_check_stability`) — projects the found BPM forward to where beats
  32/64/128/256/512 should fall and re-derives tempo from scratch there. Not a fix for the
  disco-cluster problem (empirically tested: window-stability showed <0.5 BPM variance
  across whole tracks in the cluster, ruling out "it's actually a mid-track tempo change" as
  the explanation) — its real value is telling a DJ-mix/live-recording file apart from an
  ordinary track, validated on a real 1-hour DJ mix that genuinely changes tempo partway
  through.

### 2.4 The energy_flux finding (2026-08-24, same day as this doc — NOT YET MERGED)

External AI review (see §2.5) converged on citing Zapata, Davies & Gómez (2014) — the
actual published mechanism behind essentia's `RhythmExtractor2013(multifeature)` — as a
blueprint: essentia doesn't pick a single onset-detection feature, it runs ~5 independent
ones (complex spectral difference, energy flux, mel-band spectral flux, beat-emphasis,
modified-information-gain spectral flux) and selects by cross-feature beat-sequence
agreement, not by a single global periodicity ranking.

As a diagnostic (not yet a production change), two of those independent features were
implemented and tested — see `scripts/experiment_energy_flux_onset.py` for the exact code:

- **`energy_flux`**: broadband RMS-energy novelty, no per-bin log compression — coarser,
  magnitude-only, structurally different from `dsp.onset_envelope`'s log-magnitude spectral
  flux.
- **`complex_domain_flux`**: complex-domain onset detection (Duxbury/Bello) — predicts each
  STFT bin's next value from constant-magnitude + constant-phase-increment, scores the
  deviation. Sensitive to phase discontinuities spectral flux ignores entirely.

**Step 1 — pairwise diagnostic** (does the feature favor the correct answer over the known-
wrong rival, on the specific ambiguous lag pair, using the existing `_harmonic_score`):

| | disco cluster (n=29) | control, already-correct (n=25) |
|---|---|---|
| spectral_flux (current) | 21% correct | 76% correct |
| **energy_flux** | **93% correct** | **96% correct** |
| complex_domain | 69% correct | 92% correct |

**Step 2 — full pipeline swap**: `dsp.onset_envelope` monkeypatched to `energy_flux`,
every existing pass in `detect_tempo` (harmonic-sum, genre-band, breakdown bar-fit) run
completely unchanged on top of it, across the full 130-track real set:

| | exact | within 1% | MIREX |
|---|---|---|---|
| spectral_flux (current) | 69.2% | 70.8% | 70.8% |
| **energy_flux swap** | **77.7%** | **86.9%** | **88.5%** |

Net change: **31 tracks fixed, 8 regressed** (net +23 correct, 90→113 of 130 under MIREX
tolerance). The 8 regressions cluster in "That's Not An Edit" and "Word Of Mouth" material
— not yet root-caused.

**This is real and validated, but not merged.** `energy_flux` isn't just "differently
biased" the way a genuine multi-feature-*agreement* mechanism would predict — it's outright
more accurate than spectral_flux on both groups, which is a bigger and different claim than
what was being tested for. Two ways to proceed, neither attempted yet:

1. **Straight swap** — replace `dsp.onset_envelope` with `energy_flux` in `iron/tempo.py`,
   accept the 8 regressions for the net +23 gain. Simple, but doesn't investigate whether
   the 8 regressions have a fixable cause first.
2. **Real multi-feature arbitration** — keep spectral_flux as the primary signal, add
   energy_flux as an independent second vote, only override on disagreement + margin
   (closer to the actual Zapata/essentia mechanism, and to how Pass 4 is already gated in
   this codebase). More work, needs its own validation pass, but might recover the 8
   regressions that a straight swap accepts as a cost.

Either way, before merging: (a) look at the 8 regression tracks specifically to understand
why energy_flux fails them, (b) re-run the full synthetic suite
(`tests/test_iron_tempo.py`, `tests/test_iron_dsp.py`) to make sure nothing there breaks,
(c) run `scripts/experiment_energy_flux_onset.py --feature energy_flux` against the §3
150-track genre-diverse sample too, since that sample's failure mode (half-time bias) might
or might not be the same underlying issue.

### 2.5 External AI collaboration

Multiple rounds of proposals from Gemini, Perplexity, and ChatGPT ("Caelum") were solicited
and evaluated against real data rather than accepted on faith — a brief was drafted
instructing them to ground proposals in real failure data and cite published research only
(no invented techniques), with a mandatory falsifiable validation plan attached to any
suggestion. First-round proposals (including Gemini's low-band gate, §2.2) failed direct
validation. Second-round responses from all three independently converged on the same
citation (Zapata, Davies & Gómez 2014) and the same recommended experiment
(multi-feature agreement) — which is what §2.4 above actually tested, with a genuinely
positive real result. If soliciting further external help: the pattern that worked was
"here is the real failure data, here is what we've already tried and why it failed, propose
something citable and falsifiable" — not an open-ended "how would you fix this."

---

## 3. The half-time bias problem

Found on a **different, genre-diverse, unconstrained-BPM-range 150-track sample** (jazz
standards, acoustic rock, disco, funk, hip-hop mixed with house/techno — not the disco-
cluster-heavy, BPM-constrained sample in §2), drawn live from the same
`~/Library/Pioneer/rekordbox/master.db`:

| | Iron | essentia (live, same sample) | librosa (historical, 12,687 tracks) |
|---|---|---|---|
| exact (±0.6 BPM) | 42.0% | 83.0% | 13.4% |
| within 1% | 48.0% | 88.4% | 36.8% |
| MIREX (±4%) | 50.0% | 92.5% | 90.7% |

The striking number: **Iron's MIREX (50.0%) is far below librosa's historical MIREX
(90.7%)** — with n=150 that gap is far outside sampling noise, and needed explaining.

**Wrong hypothesis, tested and disproven**: genre-band correction (tuned around DJ/EDM
tempo clusters) actively hurting on this more genre-diverse library. An ablation
(`scripts/ablate_genre_bands.py` — same 150-track sample, `detect_tempo` run twice per
track, once normally and once with `tempo._in_genre_band` monkeypatched to always return
`True`) showed the opposite:

| | band ON (current) | band OFF |
|---|---|---|
| exact | 42.0% | 21.3% |
| within 1% | 48.0% | 23.3% |
| MIREX | 50.0% | 24.7% |

Disabling genre-band correction **roughly halves every metric** (42 tracks fixed, only 4
broken by having it on — 10.5:1 net positive). **Genre-band correction is load-bearing, not
the problem — do not remove or weaken it without a fresh ablation showing net-negative on
whatever's actually broken.**

**Actual root cause**: Iron's raw Pass-1 pick (before any correction) systematically lands
at **~0.5x the true tempo** on real music outside the 7 hand-picked genre bands (mostly
slower soul/jazz/funk). Genre-band correction fixes a lot of these by coincidence — doubling
a raw half-time miss back into a defined band — but only for tracks whose true tempo falls
inside one of the bands. Mechanism: in `_harmonic_score`, a half-time candidate's lag sits
at exactly half the true tempo's lag, so the true tempo's own strong autocorrelation peak
looks like the half-time candidate's "2nd harmonic" and lends it undeserved credit — real
music's richer harmonic content (bass, snare, offbeat elements) exposes this far more than
the synthetic kick+hi-hat test fixtures do.

Also surfaced: a rare secondary bug where Pass 2's octave correction changes `chosen_lag`
before Pass 4 (breakdown bar-fit) runs, and Pass 4 then lands somewhere worse than it would
have on the original lag pick. Affects 4 of 150 tracks in this sample (`Turquoise Hexagon
Sun`, `No Clue`, `Pansit Acid`, `Rennie Foster - FREE EDITS` — see
`scripts/ablate_genre_bands.py`'s per-track output for exact numbers). Not urgent given the
10.5:1 overall ratio, but a real Pass 2 → Pass 4 interaction worth a look eventually.

**Next step, not yet attempted** (two candidates, not mutually exclusive — and now a third,
per §2.4's open question):

1. Strengthen the half-time penalty in `_harmonic_score` directly — needs a term
   distinguishing "this candidate's multiples are real periodicities of its own pulse
   train" from "this candidate's 2nd harmonic happens to be a different, stronger
   fundamental."
2. A continuous log-Gaussian tempo prior (not box-shaped like genre bands) biasing against
   implausibly slow picks when a faster candidate scores comparably — could catch true
   tempos falling between/outside the current 7 bands (this sample has plenty: jazz
   ballads, slower funk/soul).
3. **Test whether §2.4's energy_flux swap also fixes this** — same underlying mechanism
   (log-magnitude spectral flux's harmonic-credit-leakage) could plausibly be at fault for
   both the half-time bias here and the 2:3 ambiguity in §2, but this has NOT been tested on
   this sample yet. Do this first — it's nearly free (the script already exists) and might
   make (1)/(2) unnecessary or at least sharpen what's left to fix.

**Before/after any change to Pass 1/2, run**:
```bash
pytest tests/test_iron_tempo.py tests/test_iron_dsp.py tests/test_iron_beats.py -v
```
(the synthetic BPM sweep + documented 190 BPM xfail must stay green), then re-run
`scripts/ablate_genre_bands.py` and `scripts/live_compare_iron_essentia.py` on the same
`--seed 42` sample to measure real impact before declaring victory.

---

## 4. Beat-grid / meter detection (`iron/beats.py`)

Built and committed (`755bf5d`): `downbeat_offset` (beat-grid anchor) and `time_signature`
(4/4 vs 3/4) detection, opt-in via `iron.analyze(want=(..., "downbeat_offset"))`. Built on
`dsp.track_beats` (validated phase-locking primitive, §2.2) plus a new `dsp.band_energy`
(kick-band accent feature, `_KICK_BAND_FMAX = 120.0` Hz in `iron/api.py`).

Two real bugs found and fixed during development (not just tuning):
- `track_beats`'s DP penalty is scale-dependent and was "double-timing" (locking onto both
  kicks and the hi-hats between them) when fed `onset_envelope`'s raw units — fixed by
  normalizing to unit scale before tracking.
- Sampling the kick-band accent feature at the phase-locked frame missed the actual
  transient — broadband flux peaks ~150-200ms *after* a percussive transient's true attack
  (an STFT-windowing artifact for transients spanning several hops), landing in the decay
  tail instead. Fixed with `_accent_strength`'s backward local-max search.

**Validated on synthetic fixtures only** (`tests/test_iron_beats.py`, a BPM sweep) — **not
yet validated against real music.** Do not claim real-music accuracy for this feature
without running `scripts/benchmark_iron_beats.py` against real ground truth first (see
`beat_this` note below).

**License research done alongside this** (8 external tempo/beat-tracking projects reviewed
for technique + license against Iron): `madmom`'s pretrained RNN/DBN models are
**CC-BY-NC-SA — a hard commercial blocker**, same problem class as essentia's AGPL, ruled
out entirely. `beat_this` (CPJKU) has MIT code AND MIT published weights — the cleanest
license found — but is used **only as an offline validation oracle, never a runtime
dependency** (user's explicit call). `scripts/benchmark_iron_beats.py` implements this
(needs `pip install beat-this`, dev-only, not in any requirements file). Two techniques were
read from published methods and clean-room reimplemented (never copied): BTT's
(Beat-and-Tempo-Tracking, Krzyzaniak) cumulative beat-strength tracking, and
loop-tempo-estimator's tatum-hypothesis idea (scoring small-integer groupings by onset
alignment) — neither has been built into `iron/` yet, flagged as available technique if
needed.

A 15,000+-track USB drive (`Passport`, containing Rekordbox's binary `USBANLZ` beat-grid
export — genuine CDJ-grade ground truth, materially better than the `beat_this`-oracle
approach) was not mounting as of the session that investigated this. If it's available now:
`pyrekordbox` (already a dependency) has ANLZ-parsing support — check its `anlz` module —
and the five paths flagged as worth checking were `/Volumes/Passport/PIONEER/Master/share/
PIONEER/USBANLZ`, `/Volumes/Passport/.Spotlight-V100`, `/Volumes/Passport/DJMT_PIONEER`,
`/Volumes/Passport/FableGear Archive/Reports`, `/Volumes/Passport/
FableGearToolAudit_20260504_140402` (the last two may contain reusable prior audit
findings).

---

## 5. Things NOT to re-litigate

- Genre-band correction is net-positive on real, genre-diverse data (10.5:1, §3). Don't
  remove or weaken it without a fresh ablation showing net-negative on whatever's actually
  broken.
- Compound-meter harmonic-credit weighting, DP cross-period comparison using
  `dsp.track_beats`' raw/normalized *score* for octave disambiguation, and Gemini's low-band
  gate (§2.2) are all tested, failed, and reverted — don't re-attempt any of them unchanged;
  read the cited docstring for exactly why each one failed before trying a variant.
- DP transition-*penalty-variance* (not the same thing as the score comparison above — see
  §8.4) was also tried, for octave disambiguation, and reverted: broke 9/12 synthetic
  regression cases by flipping to exactly half-tempo. Root cause is specific to
  `dsp.track_beats`' search window scaling with the candidate period — a wrong, slower
  candidate's wider window lets the tracker silently phase-lock onto the true, faster
  rhythm's own spacing while still being scored against the wrong target period, producing
  deceptively *low* variance. `dsp.track_beats_with_penalty_variance` itself is fine and
  still used for its original, narrower purpose (self-consistency of a path at an
  already-known-correct period) — just not for this cross-period comparison, at least not as
  tried. See the long comment in `iron/tempo.py` where this pass used to live.
- `beat_this` stays offline/dev-only — explicit user choice over adding it as a runtime
  dependency.
- `madmom` is out — CC-BY-NC-SA models, incompatible with a for-sale app.
- `iron/beats.py`'s `time_signature`/`downbeat_offset` output is validated on synthetic
  fixtures only. Don't claim real-music accuracy for it without running
  `scripts/benchmark_iron_beats.py` (or real ANLZ ground truth) first.
- Neither Iron nor Anvil is wired into the live app (`audio_processor.py`,
  `waveform_generator.py`, etc.) — don't wire either in without explicit user sign-off; that
  decision hasn't been made yet and is separate from the accuracy work above.

---

## 6. Tools available for continuing this work

- `scripts/experiment_energy_flux_onset.py [--feature energy_flux|complex_domain|
  spectral_flux]` — §2.4's diagnostic; swaps the onset feature and runs the real
  end-to-end `detect_tempo` pipeline against a Rekordbox ground-truth JSON.
- `scripts/live_compare_iron_essentia.py --sample N --seed S` — Iron vs essentia, live,
  same sample, same ground truth, drawn fresh from `master.db`.
- `scripts/ablate_genre_bands.py --sample N --seed S` — genre-band on/off ablation with
  per-track ratio-bucket diagnostics (`~0.5x`, `~2x`, etc.) and a HELPED/HURT/neutral
  breakdown. Reusable for testing any future Pass 1/2 change, not just genre bands.
- `scripts/benchmark_iron_tempo.py` — the original, simpler Iron-only benchmark against
  Rekordbox ground truth.
- `scripts/benchmark_iron_beats.py` — beat-grid/downbeat validation against `beat_this` as
  ground truth (needs `pip install beat-this`).
- `anvil_iron_test_tracks/` (gitignored, real audio + real Rekordbox-diffed ground truth,
  local to this machine only) — the 130-track disco/soul set behind §2's numbers, plus
  `rekordbox_fresh_comparison.json` (per-track iron/essentia/librosa results) and
  `rekordbox_fresh_ground_truth.json` (the ground-truth input every script above expects at
  its default `--ground-truth` path). Not reproducible from a fresh clone — rebuild by
  diffing a Rekordbox `master.db` snapshot before/after analyzing a fresh batch of tracks in
  Rekordbox itself (see git history around commit `9376103` for the exact before/after
  diffing approach used the first time).
- `scripts/benchmark_iron_key.py --rekordbox-db PATH [--sample N] [--seed S]` — Iron's key
  detector vs. Rekordbox `KeyName` ground truth, live A/B against the pre-CQT linear-chroma
  path in the same run (monkeypatches `key.dsp.chroma_cqt` to `key.dsp.chroma`), so library
  drift between separate runs can't confound the comparison. §8.5.
- `scripts/benchmark_iron_genre_diverse.py --root PATH [--count N] [--scan-limit N]
  [--bpm-min X] [--bpm-max Y] [--workers N] [--out results.jsonl]` — genre-diverse tempo +
  key benchmark that reads ground truth straight from each file's own embedded tags (Anvil
  for bpm/key, mutagen for genre), not from any Rekordbox database. **Use this, not a
  Rekordbox DB, whenever a database's `FolderPath` records might be stale relative to the
  actual files on disk** — see §8.1 for why this exists and what it found. Round-robin
  samples across genre buckets so no single dominant genre crowds out a fixed-size sample;
  folder-level scan parallelism with an early stop (`--scan-limit`, default `count * 6`)
  rather than walking an entire library; per-track analysis has an overall timeout
  (`--out` writes results incrementally, so a run that times out or gets killed doesn't lose
  completed work). §8's 996-track before/after numbers came from this script.

All `--seed`-taking scripts default to `--seed 42` for reproducibility.

---

## 7. Folding in `docs/iron/RESEARCH.md` (2026-08-25)

A third research doc, `docs/iron/RESEARCH.md`, was started independently of this file and
never merged in — `CLAUDE.md` had an unresolved merge conflict pointing different sessions
at different docs (this file vs. `docs/iron/RESEARCH.md`), which is exactly the
fragmentation this file exists to prevent. `CLAUDE.md` is fixed to point here only. The rest
of this section folds in `docs/iron/RESEARCH.md`'s findings that aren't captured anywhere
above; the file itself is left in place for archaeology, per this doc's own convention for
superseded docs.

### 7.1 — Analysis-window bug: already landed, no further action needed

`docs/iron/RESEARCH.md` documented a real bug — `audio_processor.py`'s librosa/key decode
path (`_load_audio_ffmpeg`) anchored its `ANALYSIS_DURATION`-second window at 0:00,
disproportionately analyzing a track's beatless/sparse intro instead of a representative
slice, while essentia's `MonoLoader` reads the whole file with no such cap. **Confirmed
landed on this branch**: `audio_processor.py` has `_analysis_window_start()`, which centers
the window in the track (falling back to 0.0 for tracks shorter than the window), and
`_load_audio_ffmpeg` calls it. No open work item here — noted only so nobody re-diagnoses
or re-fixes it.

### 7.2 — TypeScript Iron prototype review: what's ported, what isn't

A separate session reviewed a TypeScript prototype of Iron's tempo detection (built by
another tool, "Grok", as a browser-based lab — not part of this repo) and verified its
5-pass pipeline by running its own test suite and instrumenting it directly against its
synthetic fixtures (not just reading the code). Cross-checking against the current
`iron/tempo.py` on this branch:

- **Genre-band octave correction** and **breakdown-duration bar-fit** (the prototype's
  passes 2 and 3) are **already implemented** in `iron/tempo.py` (`_GENRE_BANDS`,
  `_in_genre_band`, the breakdown-duration bar-fit logic) — apparently arrived at
  independently, since neither `iron/tempo.py` nor its docstrings reference the prototype.
  Consistent with this file's own §1 note that genre-band correction is validated
  net-positive (10.5:1, §3).
- **The kick-onset-interval ("four-on-the-floor") gate** (the prototype's pass 5) is **not
  yet ported**. It isolates a kick envelope via a time-domain ~120 Hz lowpass + energy flux
  (deliberately not STFT bins — a clave hit's high-frequency content leaks into low STFT
  bins but doesn't survive a real lowpass), takes inter-onset-intervals between kick peaks,
  and only trusts the result when gated (CV < 0.12, ≥8 peaks, 0.8–1.2 "kicks per beat").
  It switches the tempo pick only when gated AND either a 2:3 rivalry with the broadband
  pick, or the two picks disagree by >4%. This targets the same disco/soul 2:3
  clave-vs-kick failure mode as this file's §2 "disco cluster" problem, from a different
  angle (isolating the kick in the time domain rather than changing the onset-envelope
  feature) — a candidate worth testing alongside, or instead of, the §2.4 energy_flux fix,
  not yet attempted here.
- **A documented gotcha for whoever ports the kick-IOI gate**: `kicksPerBeat` looks like a
  literal per-beat kick count from its name, but algebraically reduces to
  `(lastPeak − firstPeak) / windowLength`, a "peak coverage" ratio — it only works because
  the `bpm` in its denominator is the *post-octave-fold* value, so it's actually
  `peakCoverage / 2^k` where `2^k` is however many octave-doublings the fold applied. This
  was mis-diagnosed as a bug and then corrected in the same session after empirical
  verification against the prototype's own fixtures (disco/house cases give ~0.96–0.96,
  hip-hop/waltz correctly give ~0.44/~0.21, well outside the [0.8, 1.2] gate). If you port
  this gate, verify it the same way — instrument it against real fixtures — rather than
  trusting a paper derivation, per the lesson learned here.
- Two lower-priority implementation notes from that review, unaddressed either way: the
  prototype's kick lowpass is a gentle 1-pole IIR (6 dB/octave) rather than a steeper
  filter, and a bounds check in its breakdown pass only tests one side of the range. Neither
  was verified against real audio either way.

### 7.3 — Not independently validated against real ground truth

The TypeScript prototype review above verified internal consistency (its own tests pass,
its own fixtures behave as expected) — it does **not** constitute validation against the
12,687-track Rekordbox ground truth in §1, and neither does the analysis-window fix in
§7.1. Don't cite either as accuracy evidence; use the benchmark scripts in §6 for that.

### 7.4 — Open gap: key detection has no segment-wise handling

Unlike tempo, `iron/key.py` has had no dedicated research session. Two gaps flagged by the
prototype review, both still true as of this fold-in (checked against current
`iron/key.py`, which has no segment/window logic of its own — it consumes whatever chroma
`iron.dsp.chroma()` hands it):

- Key detection shares `audio_processor.py`'s decode path with the tempo fallback, so it
  inherits whatever windowing behavior that path has (see §7.1 — this specific inheritance
  is now less bad post-fix, since the window is centered rather than anchored at 0:00, but
  it's still one fixed window rather than anything key-detection-specific).
  `iron.dsp.chroma()` itself has no window-selection logic — it takes whatever audio it's
  given.
- Chroma is averaged over the entire analysis window with no segment-wise handling, so a
  track that modulates key mid-song gets a single blended, likely-wrong answer. Nobody has
  scoped how common this is in a real DJ library or what a fix would look like.

Neither gap has an owner or a next step beyond "worth investigating" — flagged here so it
isn't lost, not because a fix is in progress.

---

## 8. Multiband/cyclic-tempogram tempo scoring, CQT key chroma, BPM range widening (2026-08-27)

All work below happened in one session, on `~/FableGear`'s `anvil` branch, working tree
uncommitted as of this writing (check `git log`/`git status` for current state — don't
assume this landed in a commit just because it's written up here).

### 8.1 New ground-truth methodology: read tags directly, don't trust a Rekordbox DB's FolderPath

`/Volumes/Passport/PIONEER/Master/master.db`'s `DjmdContent.FolderPath` records point at a
`/Volumes/Passport/DJMT_Library/...` folder structure that **does not exist on this drive**.
The drive's real ~1.2TB of music lives at `/Volumes/Passport/DATABASE/...`, organized
differently (different folder layout, different filenames — no `Artist: Title.mp3` colon
convention, no per-letter subfolders). This is why a naive "does this FolderPath exist"
check found only 63 of 23,505 valid-BPM `master.db` rows (and only 1,066 of 63,114 total
rows) — not because the files are missing, but because the database's own path records are
stale relative to how the drive is laid out now. **If you hit a suspiciously low
existing-file count against this (or any) Rekordbox DB, check for this before concluding the
files are gone** — `find <drive> -iname "<a known artist name>"` a few levels deep is a
fast sanity check.

The fix used here: sidestep Rekordbox entirely. `scripts/benchmark_iron_genre_diverse.py`
(§6) reads ground-truth `bpm`/`initial_key` straight from each file's own embedded tags via
`anvil.read_fields()`, and genre via `mutagen`'s easy-tags API (`TCON`/genre; `anvil`'s own
`TrackFields` has no genre field, so this is the one place `mutagen` is used directly in this
session's work — read-only, benchmarking-only, not a runtime dependency change). This is
strictly more robust for any future large-scale validation: it works regardless of which DJ
software's database is current, or whether one even exists for a given folder.

**Caveat, not resolved**: embedded ID3 BPM tags are a different, less-audited ground-truth
source than Rekordbox's own analyzed BPM — some fraction may be stale, rounded, or wrong at
the source (pre-tagged by whoever assembled a given corner of the library, not necessarily
Rekordbox-verified). The 996-track exact-match number in §8.6 (14.9%) is notably lower than
every prior Rekordbox-sourced sample in this doc (18.5%-69.2% depending on sample) — some of
that gap is plausibly Iron performing worse on a broader, more genuinely diverse sample, but
some could be ground-truth noise from this new source. Not disentangled. Whoever investigates
further should spot-check a sample of disagreements by ear before trusting the exact-match
number as a precise measurement, though the *within-1%*/*MIREX*/wrong-rate numbers (looser
tolerances) are far more robust to a few degrees of ground-truth rounding noise and are the
more trustworthy part of §8.6's before/after comparison.

### 8.2 Multiband onset scoring (Klapuri 2003) — validated, kept

`iron.dsp.onset_envelope_multiband()`: independent log-magnitude spectral flux computed per
frequency band (kick/sub-bass, bass, mid, high — see the function's own docstring for exact
Hz ranges), rather than one summed broadband signal. `iron.tempo._combined_score()` folds
each band's own harmonic-sum score in alongside the existing broadband one
(`_MULTIBAND_WEIGHT = 0.5`), used throughout Pass 1 and Pass 2's candidate scoring. This is
the "genuinely independent second onset-detection feature" `_harmonic_score`'s own docstring
already flagged as the unsolved direction for the §2 disco-cluster problem — an
implementation of that direction, not a port of essentia's actual multifeature ensemble.

Confirmed genuinely active on real audio, not a no-op: flipped the raw Pass-1 winner on 3 of
63 tracks in a live check against Passport's `DATABASE/` sample. Net effect on final accuracy
is folded into the combined §8.6 before/after number, not measured in isolation.

### 8.3 Cyclic tempogram octave correction (Grosche & Müller) — validated, kept

`iron.dsp.cyclic_tempo_strength()`/`cyclic_tempo_class_lookup()`: pools a track's own
autocorrelation strength across every octave of a candidate's tempo class
(`log2(bpm) mod 1`) into one octave-invariant curve, independent of `_GENRE_BANDS`. Wired in
as a new Pass 2b in `detect_tempo`, gated the same conservative way as the rest of the
pipeline — a rival must pool decisively more tempo-class evidence (`_CYCLIC_MARGIN = 1.3`)
AND still carry a real share of the current pick's raw score (`_RIVAL_THRESHOLD`, same guard
Pass 2 uses) before it can override the current pick.

**Why this exists alongside `_GENRE_BANDS` rather than replacing it**: §3 already
established genre-band correction is net-positive load-bearing (10.5:1, don't remove per
§5). But §3 also documented its real gap — it only helps a track whose true tempo falls
inside one of the 7 hand-picked bands; everything else (slower soul/jazz/funk, mostly) "gets
no help and stays wrong." The cyclic tempogram is derived from the track's own signal, not
an external genre-tempo assumption, so it still has something to say about exactly those
tracks. Not independently ablated against §3's 150-track sample specifically — its
contribution, like §8.2's, is folded into §8.6's combined number.

### 8.4 DP transition-penalty-variance — tried and reverted, distinct new failure mode

Attempted as a further octave-disambiguation pass (a genuinely different comparison signal
than the raw/normalized DP *score* comparison §2.2 already documents as reverted — this used
the *variance* of per-step transition penalties along a phase-locked path, not its summed
score). Broke 9 of 12 synthetic regression cases in `tests/test_iron_tempo.py`, **all**
flipping to exactly half the true tempo. Root-caused, not a tuning-margin problem:
`dsp.track_beats`' search window scales with the *candidate* period
(`search = period * search_multiple`). At a wrong, doubled-period candidate, that window is
wide enough for the DP tracker to silently phase-lock onto the TRUE, faster rhythm's own
beat spacing while still being scored against the wrong (doubled) target period — producing
a uniformly-wrong-by-a-constant-ratio path, which is deceptively *self-consistent* (low
variance) precisely because it's consistent, not because it's correct. Measured on the 174
BPM synthetic fixture: variance 2.97 at the true tempo (genuine irregularity from competing
kick/hi-hat onsets) vs. 0.002 at the wrong half-time candidate.

Reverted — see the long comment in `iron/tempo.py` where this pass used to live, and §5.
`dsp.track_beats_with_penalty_variance` remains as a tested primitive for its original,
narrower purpose (self-consistency of a path at an already-known-correct period); it just
isn't valid for this specific cross-period comparison as attempted. A fix would need a
search window that doesn't scale with the (possibly wrong) candidate period — not attempted.

### 8.5 CQT-based chroma for key detection — validated, kept, first real accuracy number

`iron.dsp.chroma_cqt()`: a log-frequency-binned pseudo-CQT (large `n_fft=16384`, triangular
weight split across each FFT bin's 1-2 nearest semitone centers by log-frequency distance) —
not a literal per-bin variable-kernel direct CQT (Brown & Puckette 1992); see the function's
own docstring for why that distinction is deliberate (time resolution doesn't matter for a
whole-track-averaged chroma vector, so a single high-resolution STFT gets the same
frequency-resolution fix at a fraction of the cost a real per-bin kernel sweep would take).
Fixes a real, measurable defect in `dsp.chroma()`'s linear-Hz bins: at `n_fft=4096`/
`sr=22050`, FFT bin width (~5.4 Hz) is wider than a semitone's spacing at 55 Hz/A1 (~3.3 Hz),
so adjacent low bass semitones can share or straddle the same bin. `iron/key.py`'s
`detect_key()` now calls `chroma_cqt()` in place of `chroma()`; `dsp.chroma()` itself is
unchanged and still used by its own tests.

**First real accuracy number for `iron/key.py` against real ground truth** (previous numbers
in this doc, §2.1 and §7.4, were either unaddressed or from a different, smaller sample):
`scripts/benchmark_iron_key.py`, 300-track Rekordbox-sourced sample, live A/B in the same
run —

| | exact Camelot match |
|---|---|
| CQT chroma (current) | **31.3%** (n=300) |
| linear chroma (pre-CQT, live ablation) | 19.7% (n=300) |
| Iron pre-CQT, historical (§2.1, different 130-track sample) | 18.5% |
| librosa `chroma_cqt`, historical | 24.6% |

The live ablation's 19.7% lines up with the historical 18.5% closely enough to cross-validate
the A/B methodology itself. CQT chroma now beats even the old librosa reference number that
used to be the thing to catch up to. Still low in absolute terms — most tracks are still
wrong — and the §7.4 segment-wise/windowing gaps are completely unaddressed by this change;
it's a chroma-extraction fix, not a fix to `key.py`'s per-track handling.

### 8.6 BPM search range narrowed to 60-180 + new low-band genre coverage — validated

Two related, user-directed changes, both now live in `iron/tempo.py`/`iron/api.py` defaults
(check current source for exact values — this is a snapshot, not the source of truth):

- `detect_tempo`'s default `bpm_min`/`bpm_max` narrowed from (30.0, 300.0) to (60.0, 180.0)
  (mirrored in `iron/api.py`'s `_BPM_MIN`/`_BPM_MAX`).
- `_GENRE_BANDS` gained a new lowest band, `(60.0, 85.0)` (downtempo/slow hip-hop/R&B/soul
  ballads) — previously the lowest band started at 85.0, leaving genuinely no genre-band
  coverage at all below it.

**Validated with a real before/after on the same 996-track genre-diverse sample** (§8.1's
methodology, `scripts/benchmark_iron_genre_diverse.py --root /Volumes/Passport/DATABASE
--count 1000 --seed 42`, only `--bpm-min`/`--bpm-max` differing between runs):

| | exact | within-1% | MIREX | wrong (>4% off) |
|---|---|---|---|---|
| before (30-300, old bands) | 14.1% | 30.1% | 48.2% | 51.8% |
| after (60-180, new low band) | 14.9% | 33.7% | **60.7%** | **39.3%** |

A real, broad gain — MIREX +12.5 points, wrong-answer rate -12.5 points, held across nearly
every genre bucket with ≥10 tracks (e.g. Disco 45.0%→85.0% MIREX, R&B 48.0%→80.0%, Punk Rock
63.3%→83.3%, Alternative 52.4%→81.0% — full per-genre table in
`iron_1000_baseline.log`/`iron_1000_afterfix.log`, not committed to the repo, regenerate via
the command above if needed). Two small-n genre buckets (n≈11-18) dipped slightly — within
noise at that sample size, not a clear regression.

**Exact-match barely moved (14.1%→14.9%)** — this change mainly gets answers into the right
ballpark, not pinpoint-precise. Error-ratio breakdown of wrong answers shifted:

| | 2x | 0.667x | 1.5x | 0.5x | "other" (no clean ratio) |
|---|---|---|---|---|---|
| before | 22.9% | 14.0% | 7.8% | 5.6% | 44.2% |
| after | 16.1% | 9.0% | 10.2% | **11.5%** | 43.7% |

2x and 0.667x (compound-meter) errors both dropped as a share of remaining wrong answers.
**0.5x (half-time) errors rose, both as a share and in absolute count (29→45 of 996
tracks)** — a real, if modest, side effect, not chased further. Plausible mechanism, not
confirmed: narrowing to 60-180 (a 3x span, ~1.58 octaves) gives §8.3's cyclic tempogram less
than 2 full octaves of evidence to pool for tempo classes near the range's edges, weakening
its disambiguation there specifically. **"Other" (no clean octave/compound-meter ratio) is
unchanged at ~44% of wrong answers, still the single largest bucket** — these are the
genuinely hard, unexplained remaining cases; nothing in this session's work touched them.

### 8.7 Open question, unchanged from before this session: does `energy_flux` (§2.4) interact with §8.2/§8.3?

Nobody has tested this. §2.4's `energy_flux` onset feature (broadband RMS-energy novelty, no
log compression) and §8.2's multiband scoring (per-band log-magnitude flux) are structurally
different techniques both aimed at overlapping parts of the octave/compound-meter ambiguity
problem — they could compound, conflict, or be redundant. Whoever picks this up next should
run `scripts/experiment_energy_flux_onset.py` against this session's code (with §8's changes
already in place) before assuming either way, same as §1 already flagged before this session
started.

---

## How to add to this doc

Append a new dated section (`## N. <short title>`) rather than editing existing sections'
conclusions — if you disprove something above, add a note pointing at it rather than
silently rewriting history, the same discipline this file's own §2.2/§3 already follow. Keep
the "Current status" (§1) and "Things NOT to re-litigate" (§5) sections up to date as the
two sections most likely to be read and not the rest.
