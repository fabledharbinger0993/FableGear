# Iron — tempo/beat/key detection research

**Status:** Living document. This is not a spec and not a finished design —
it's the accumulated findings from real investigation, kept in one place so
work doesn't get re-derived from scratch every session. If you're a Claude
instance picking this up: read section 0, then add your own section at the
bottom rather than editing what's already here, unless you're correcting a
factual error (in which case say so explicitly — see the note in §3.2 about
a claim that was made and then retracted in the same session, which is
exactly the kind of thing worth being honest about in writing).

Multiple Claude sessions may be working on Iron/Anvil in parallel. If you
find work here that conflicts with what you're about to add, don't silently
overwrite it — reconcile in the text, or flag the conflict in a new section
and let a human resolve it.

---

## 0. Why Iron exists

FableGear's tempo/beat/key detection currently runs on **essentia**
(`RhythmExtractor2013`, `multifeature` method) with a **librosa** fallback
when essentia isn't installed. See `audio_processor.py` and
`requirements_optional.txt`.

The reason Iron needs to exist at all, and needs to be a *clean-room*
implementation rather than a port: **essentia is AGPL-3.0**
(`MARKETING.md` §4.3). For a product that intends to ship as a closed,
paid build, AGPL is a real liability — the same category of problem that
motivated Anvil (replacing GPL'd `mutagen`) and the Rekordbox-independence
roadmap (replacing `pyrekordbox`, a DMCA §1201 concern rather than a
licensing one, but the same underlying instinct: don't build the product
on a dependency you can't safely keep).

**This has a direct consequence for how Iron gets built.** It must come
from published, public technique — the MIREX/onset-detection literature
(Bello 2005, Dixon 2006, Ellis 2007, etc.) — not from reading essentia's
source and reimplementing what it does. Doing the latter would drag the
AGPL derivative-work question right back in through the side door, which
defeats the entire point. Anyone extending Iron should keep this in mind:
"how does essentia do X" is not the question to ask; "how does the
published literature do X" is.

## 1. Baseline accuracy — the number everything gets measured against

From `audio_processor.py`'s docstring for `_detect_bpm_essentia`, measured
against **12,687 real Rekordbox ground-truth beat grids** (a random
300-track sample of a real library):

| Metric                      | librosa fallback | essentia |
|------------------------------|------------------|----------|
| Exact (±0.6 BPM)              | 13.4%            | 91.4%    |
| Within 1%                     | 36.8%            | 94.8%    |
| MIREX (±4%)                   | 90.7%            | 98.3%    |

The **exact** column is the one that matters for FableGear specifically:
a OneLibrary export carries these grids straight to a CDJ, so "close" isn't
good enough — a 4%-tolerant tempo drifts a full beat inside ~25 bars.
**Iron's job is to close that 13.4% → 91.4% gap without AGPL, not to beat
essentia's number by some heroic margin.** Matching it is already the win.

## 2. Fixed this session: the analysis window was anchored at 0:00

**The bug.** `audio_processor.py`'s `_load_audio_ffmpeg` decoded a fixed
`ANALYSIS_DURATION = 90.0` seconds starting at the beginning of the file
(`ffmpeg -t 90`). Both the librosa BPM fallback *and* key detection share
this decode. essentia does not have this problem — `MonoLoader` reads the
whole file with no duration cap.

**Why it matters.** DJ-edited tracks routinely front-load a beatless or
sparse intro specifically so the track is easy to mix in. Anchoring the
*only* analysis window at 0:00 means the librosa/key path spends its
entire budget disproportionately on the least rhythmically confident part
of the track. This is a plausible, structural explanation for a meaningful
chunk of the 13.4%-vs-91.4% gap — essentia gets the whole track "for free";
librosa was structurally guaranteed the worst slice of it.

**The fix** (already written and unit-tested this session, living as an
uncommitted diff to `audio_processor.py` on this branch — see the actual
diff in the commit this file ships with): a new `_analysis_window_start()`
helper reads track duration via `sf.info()` (cheap header read, same call
already used elsewhere in the file) and centers the window:

```python
def _analysis_window_start(path: Path, window: float) -> float:
    try:
        total = sf.info(str(path)).duration
    except Exception:
        return 0.0
    if not total or total <= window:
        return 0.0
    return (total - window) / 2.0
```

`_load_audio_ffmpeg` now passes this as `-ss` before `-i`. Verified with
synthetic WAV fixtures: a 240s track with a 90s window centers at 75.0s
(window spans 75–165s); a 30s track (shorter than the window) correctly
falls back to 0.0. Existing test suite (`pytest tests/test_audio_processor.py`)
passes unchanged — 11 passed, 8 skipped (essentia-unavailable skips, not
failures).

**What this fix does *not* do:** it doesn't touch the 76–152 BPM
octave-fold range (`_fold_octave`, still hardcoded and still genre-biased
toward house/techno — see §4 below, the TS prototype's genre-band pass is
a much better answer to this), and it doesn't change essentia's path at
all (essentia was never affected by this bug).

**Open, not yet done:** the real Iron/Python implementation should
probably adopt something closer to what the TS prototype in §4 does for
its window — 1/3 to 90% of the track, capped at 240s, not a fixed
90-seconds-centered box. The centering fix here is a minimal, low-risk
patch to the *existing* librosa fallback; it is not itself "Iron."

## 3. Review of a TypeScript Iron prototype (shared this session)

A separate person/session had **Grok** build a TypeScript implementation
of Iron's tempo detection — not hypothetical, actual running code with a
real test suite. Files: `constants.ts`, `dsp.ts`, `tempo.ts`, `key.ts`,
`analyze.ts`, `decode.ts`, `synth.ts`, `index.ts` (a browser-based "Lab"
tool, `iron/tempo.py` / `iron/dsp.py` / `iron/api.py` are referenced as the
real Python engine these numbers are supposed to match — this session did
not have access to that Python code, only the TS port).

### 3.1 Architecture (all verified against the actual code, not the
formula writeup alone — see §3.3 for how)

A five-pass pipeline over FFT autocorrelation of a spectral-flux onset
envelope:

1. **Harmonic-sum scoring.** For each candidate lag (local ACF maxima
   only), score `Σ weight[k] · acf(lag·k)` for k=1..4, weights
   `[1.0, 0.6, 0.4, 0.3]`. Winner is the baseline pick.
2. **Genre-band octave correction.** If the winner isn't inside a padded
   genre band (hip-hop/trap 85–100, downtempo 95–115, house 118–130,
   techno/trance 125–145, dubstep/half-time DnB 140–155, DnB/jungle
   160–180, hardcore/gabber 180–220, ±5 BPM pad), scan *every* lag in
   range for the best-scoring in-band candidate that retains ≥50% of the
   winner's harmonic score. This is a direct, correct answer to a gap in
   FableGear's own `_fold_octave` (§2) — a single hardcoded [76,152) fold
   range can't be right for DnB, dubstep, or trap, and this fixes that
   properly instead of picking one more genre-biased range.
3. **Breakdown bar-fit.** Finds the longest sustained low-energy span
   (4s-smoothed flux, 3% edge margin excluded), computes how many bars
   that implies at the current pick vs. at rival ratios
   `[2, 0.5, 1.5, 2/3]`, and switches only on a **one-sided gate**: rival
   fit < 0.5 bars AND pick fit ≥ 0.5 bars. (A symmetric "whichever fits
   better" version was tried and rejected — it flipped an
   already-correct track. The one-sided gate is deliberate, not an
   oversight.)
4. **Sub-frame refinement.** Standard 3-point parabolic interpolation
   around the ACF peak. Textbook-correct, no notes.
5. **Four-on-the-floor kick IOI.** The remaining hard case: a genuine
   triplet/dotted-subdivision rhythm (disco/soul clave patterns) whose
   autocorrelation outscores the true tempo's harmonic ladder — broadband
   flux locks onto the clave, not the kick. Isolates a kick envelope via
   **time-domain** 120 Hz lowpass + energy flux (not STFT bins — a 6 kHz
   clave hit leaks into low STFT bins but does not survive a 120 Hz
   lowpass), finds inter-onset-intervals between kick peaks, and only
   trusts the result when gated: CV < 0.12, ≥8 peaks, 0.8–1.2 "kicks per
   beat." Switches the pick only when gated AND (a 2:3 rivalry with the
   broadband pick, or the two picks disagree by >4%).

DP beat tracking (Ellis 2007) is implemented in the real Python Iron per
the formula writeup but deliberately **not used** for tempo
disambiguation — comparing cumulative alignment score across candidate
periods is structurally biased toward faster candidates (more beat slots
= more chances at favorable alignment). Documented, not just omitted.

### 3.2 A mistake made and corrected in the same session — read this before
touching `kicksPerBeat`

First pass at reviewing `kickIoiTempo()`'s `kicksPerBeat` gate, I derived
algebraically that it reduces to `(lastPeak − firstPeak) / windowLength`
— a "peak coverage" ratio with **no dependency on the actual kick-to-beat
ratio** — and reported this as a real bug: the gate wouldn't actually
distinguish four-on-the-floor from hip-hop's kick-on-1-and-3 pattern.

This was wrong, and the error was a real one: I forgot that `bpm` in the
denominator is the **post-octave-folded** value, not the raw IOI-derived
BPM. With folding correctly included, the formula is actually
`kicksPerBeat ≈ (peakCoverage) / 2^k`, where `2^k` is however many
octave-doublings the fold needed. I verified this **empirically**, not
just by re-deriving it on paper: built a local FFT (standard radix-2
Cooley-Tukey, since the real `fft.ts` wasn't provided) faithful enough to
reproduce all 6 of the prototype's own tests passing, then instrumented
`kickIoiTempo` directly against the real synthetic fixtures:

```
disco125trap   trueBPM=125  kicksPerBeat=0.9613  isFourOnFloor=true
disco125clean  trueBPM=125  kicksPerBeat=0.9613  isFourOnFloor=true
house128       trueBPM=128  kicksPerBeat=0.9574  isFourOnFloor=true
hiphop90       trueBPM=90   kicksPerBeat=0.4448  isFourOnFloor=false
waltz90        trueBPM=90   kicksPerBeat=0.2084  isFourOnFloor=false
```

The gate works correctly on every fixture provided, including the
hip-hop and waltz negative cases it's specifically meant to stay silent
on. **The lesson, not just the correction:** the mechanism is more
indirect than the variable name suggests (it's coverage-scaled-by-fold-
octaves, not a literal per-beat count), which is worth knowing if you
ever hit a kick pattern whose period ratio to the target range *isn't*
close to a power of two — the waltz case (period ratio 3:1) already
shows the fold overshooting to a 1/4 answer instead of the "true" 1/3,
and it still worked out because 0.21 is nowhere near the [0.8, 1.2] gate
either way. A pattern where that overshoot lands *inside* the gate by
coincidence is the untested edge case worth building a fixture for.

### 3.3 How this was actually verified (not just read)

All 6 of the prototype's own `node:test` tests were run and passed,
using a hand-written FFT as a stand-in for the missing `fft.ts` (any
correct power-of-2 FFT is numerically interchangeable here — the code
only ever consumes power spectra via an FFT→IFFT round trip, or raw
magnitude spectra, neither of which depends on which correct
implementation computed it). The `kicksPerBeat` numbers above were
captured by direct instrumentation against the real `synth.ts` fixtures,
not asserted from memory.

### 3.4 Minor, lower-priority notes from the review

- **Kick lowpass is gentle** — a 1-pole IIR at 120 Hz (6 dB/octave). Likely
  fine in practice, since the CV gate would probably catch irregular
  snare/clap bleed as a side effect, but a steeper (e.g. 2nd/4th-order)
  filter would isolate the kick band more cleanly. Not verified either way
  with real audio.
- **Pass 3's `newLag` bounds check** only tests `>= lo`, not `<= hi`.
  Harmless in practice since the candidate BPM is already range-filtered
  upstream, but worth tightening for defensiveness.

## 4. What "Iron" should probably borrow from the TS prototype

If/when the real Python Iron gets built out:

- **The body-window approach (1/3–90% of track, capped at 240s)** is
  better-reasoned than the centered-90s fix in §2 — it skips both the
  intro *and* the outro/fade, and gives long tracks more autocorrelation
  data instead of wasting the extra length. The §2 fix is a minimal patch
  to the *existing* librosa fallback; a fresh Iron build should probably
  start from the TS prototype's window logic instead.
- **Genre-band octave correction** (§3.1 pass 2) is a direct, tested fix
  for the exact gap in FableGear's current `_fold_octave` (hardcoded
  [76,152) range, wrong for DnB/dubstep/trap).
- **The kick-IOI four-on-the-floor gate** (§3.1 pass 5) targets a real,
  named failure class (disco/soul 2:3 clave errors) with a narrowly-scoped,
  well-tested trigger condition that's deliberately silent on patterns it
  shouldn't touch (hip-hop, waltz).

None of this has been ported to Python or wired into `audio_processor.py`.
The only code change actually landed this session is the §2 window-centering
fix.

## 5. Honest gaps — things this research does *not* establish

- **No measurement against the 12,687-track Rekordbox ground-truth set**
  exists for either the §2 fix or the TS prototype's approach. Everything
  in §3 is verified against synthetic fixtures the prototype's own author
  built — internally consistent, not independently validated against real
  music. "Passes its own tests" and "beats essentia on real tracks" are
  different claims; only the second one is comparable to the 91.4% number
  in §1.
- **Key detection** has its own, separate bug (unaddressed): it uses the
  same shared decode as the librosa BPM path, so it inherits whatever
  windowing problem that path has, *and* it averages chroma over the
  entire window with no segment-wise handling — a track that modulates
  mid-song gets one blended, likely-wrong answer. Key detection also never
  gets an essentia alternative at all; it's unconditionally librosa today.
- **No real-audio kick-lowpass validation** (§3.4).

## 6. For the next Claude session

- Read this whole file before doing anything, not just this section.
- If you're extending Iron's actual implementation: check whether `main`
  has moved past this branch's `_analysis_window_start` fix before
  reimplementing it.
- If you find a factual error above, correct it in place and say so — see
  §3.2 for the model of how to do that honestly rather than just deleting
  the wrong claim.
- Add a dated section below this line for your own findings rather than
  editing existing sections (except corrections, per above). Multiple
  sessions may be working in parallel — see the note about coordinating
  through `docs/iron/RESEARCH.md` conflicts rather than silent overwrites.

---

<!-- Add new sections below this line, oldest first. -->

## 7. 2026-09-02 — Test-harness finding only (no algorithm change)

Scope note up front, so nobody re-reads this expecting accuracy work:
**nothing in Iron's detection code was changed.** This section exists only so
the next session doesn't spend time re-diagnosing a test failure that isn't a
detection bug.

**`tests/test_iron_dryrun.py` hard-failed on any machine without ffmpeg.**
Iron decodes through an ffmpeg subprocess by deliberate design (`iron/api.py`:
going through ffmpeg rather than a container-specific reader is what lets Iron
analyze anything ffmpeg can decode — that design is unchanged and was not
questioned here). With no ffmpeg on PATH, `analyze()` returns
`ok=False, errors=["<name>: ffmpeg invocation failed (...)"]`, so every file in
the survey reports `status="error"` and the test's
`assert all(f.status == "ok" ...)` fails.

CI installs ffmpeg via Homebrew, so this was never red on `main` — it only bites
local runs and any environment without it. `test_audio_processor.py` and
`test_tagger_effects.py` already had a `_require_ffmpeg()` skip guard for exactly
this; `test_iron_dryrun.py` did not. Added the same guard.

Two of the four tests in that file are decoder-independent (they only count
files) and still run everywhere. The guard went on the two that are not:

- `test_survey_reports_files_and_never_writes` — failed outright without ffmpeg.
- `test_survey_reports_unreadable_file_without_raising` — the more interesting
  one: it *passed* without ffmpeg, but **vacuously**. It asserts a corrupt mp3
  yields `status="error"` with a `detail`; with no decoder installed every file
  yields exactly that, so the assertion could not distinguish "corrupt input"
  from "no decoder present". It was not testing what it claimed on such a
  machine. It now skips instead.

Also in `tests/test_iron_key.py`: `_chord()` built its sum with the builtin
`sum()`, whose accumulator seeds at int `0`, giving an inferred type of
`NDArray | float` that pyright rejected against the declared `np.ndarray`
return. That was the repo's single pyright error, and CI gates on pyright.
Replaced with `np.stack([...]).sum(axis=0)`. Verified bit-identical output
(`np.array_equal` True, max abs diff 0.0) — this is a typing fix, not a change
to any fixture the key tests measure against.

**Not investigated here:** none of the §5 accuracy gaps were touched, and no
measurement against the 12,687-track ground-truth set was attempted. The
baseline numbers in §1 are unaffected by anything in this section.
