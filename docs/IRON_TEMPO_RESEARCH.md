# Iron tempo/beat-tracking research catalog

**Start at `docs/IRON_RESEARCH.md` first** — that's the primary, consolidated research
log (accuracy findings, root causes, what's been tried and reverted, current status).
This file is a detailed sub-reference specifically for third-party tool license/technique
due diligence, linked from that doc's §4.

**This is a living document.** Every third-party tempo, BPM, beat, downbeat, or
meter-tracking tool evaluated for relevance to Iron gets an entry here — whether or not
anything from it ended up used. The point is to stop the next person (human or Claude)
from re-researching a tool someone already checked, and to keep the license/technique due
diligence in one place instead of scattered across old conversations.

**If you're a Claude instance and you just evaluated a new tool for this purpose, add an
entry below in the same format** (see "Entry format" at the bottom). Don't skip the
license check — that's usually the fact that actually decides whether a tool is usable,
more often than its accuracy claims.

Context for why this matters: Iron exists specifically to get FableGear off essentia
(AGPL-3.0) and librosa (BSD-3, safe, but weak accuracy) for tempo/key detection, and
Anvil exists to get it off mutagen (GPL-2.0) for tag I/O — both because FableGear is
for-sale, proprietary software, and copyleft/NC licenses on a dependency are a real
commercial risk, not a formality. See `iron/README.md` and `anvil/README.md` for the
full reasoning. Every entry below was evaluated with that same constraint in mind.

---

## Summary table

| Tool | Language | License | Verdict |
|---|---|---|---|
| [realtime-bpm-analyzer](#realtime-bpm-analyzer) | JS/TS | Apache-2.0 | Different problem (live browser feedback), not a tempo-accuracy competitor |
| [bpm-detector](#bpm-detector-libraz) | Python | MIT | Wraps librosa's own tempo detection — inherits its weak 13.4% baseline |
| [Beat-and-Tempo-Tracking (BTT)](#beat-and-tempo-tracking-btt) | C | MIT | **Mined** — CBSS beat-tracking idea informs `iron/beats.py` |
| [loop-tempo-estimator](#loop-tempo-estimator) | C++ | GPL-3.0 | **Mined (technique only, not code)** — tatum-hypothesis idea informs `iron/beats.py`'s meter detection |
| [web-audio-beat-detector](#web-audio-beat-detector) | JS/TS | MIT | Same weak algorithm family as realtime-bpm-analyzer, nothing new |
| [SoundTouch (BPMDetect)](#soundtouch-bpmdetect) | C++ | LGPL-2.1 | Already-surpassed algorithm (no octave disambiguation at all) |
| [phip1611/beat-detector](#phip1611beat-detector) | Rust | MIT | Too shallow — no tempo output, author calls it underdeveloped |
| [BeatNet](#beatnet) | Python | CC-BY-4.0 | Usable license-wise but CC on code is a real headache; beat_this is strictly better |
| [beat_this](#beat_this) | Python | MIT (code + weights) | **In use** — offline ground-truth oracle only, never a runtime dependency |
| [madmom](#madmom) | Python | Code permissive; **models CC-BY-NC-SA** | Blocked — same class of problem as essentia's AGPL |

---

## Entries

### realtime-bpm-analyzer
- **Repo**: github.com/dlepaux/realtime-bpm-analyzer, npm `realtime-bpm-analyzer`
- **License**: Apache-2.0
- **Algorithm**: Classic Joe Sullivan / bpm-detective lineage. Lowpass biquad filter
  (~200Hz) to isolate bass → amplitude-threshold peak-picking in the time domain
  (descending threshold scan 0.95→0.2 until ≥15 peaks) → interval histogram (diff each
  peak against its next 10 neighbors, bucket, vote) → fold every result into a fixed
  90–180 BPM range by blind doubling/halving.
- **Why it's not a tempo-accuracy competitor**: built for live, in-browser "now playing"
  BPM display via AudioWorklet, not archival-grade detection. No key detection, no
  waveform-generation capability (checked specifically — it doesn't produce anything
  usable for CDJ/thumbdrive waveform export either).
- **License note**: permissive, no commercial concern — just not solving Iron's problem.

### bpm-detector (libraz)
- **Repo**: github.com/libraz/bpm-detector, `pip install bpm-detector` (planned)
- **License**: MIT
- **Algorithm**: Comprehensive musicological analysis wrapper (chords, structure, timbre,
  melody, dynamics) — but its core BPM detection literally calls **librosa's own tempo
  function**, confirmed via its `pyproject.toml` (`librosa>=0.11.0` is a hard dependency)
  and its docs ("uses librosa's tempo detection functionality").
- **Why it doesn't help**: adopting it for tempo would just re-import librosa's own
  measured 13.4% exact-BPM baseline (`requirements_optional.txt`) with extra packaging
  around it — the exact weak path Iron exists to replace. Also drags in a much heavier
  dependency footprint (scikit-learn, pandas, matplotlib, seaborn, resampy) than
  essentia or librosa alone, bad for a PyInstaller-packaged desktop app.
- **What's genuinely interesting**: its non-tempo features (chord progression, song
  structure/form, groove classification, timbre) are real, novel scope neither Iron nor
  the old essentia path attempts — worth a look if FableGear ever wants a "production
  reference sheet" feature, but as an inspiration source, not a dependency to adopt.

### Beat-and-Tempo-Tracking (BTT)
- **Repo**: github.com/michaelkrzyzaniak/Beat-and-Tempo-Tracking
- **License**: MIT (confirmed on repo)
- **Algorithm**: ANSI C, zero dependencies, causal/realtime (built for embedded/robotics
  use, reacting to live audio). Onset detection: spectral flux (same family as Iron's
  own `dsp.onset_envelope`). Tempo tracking: generalized autocorrelation (Percival &
  Tzanetakis 2014) — candidates scored by cross-correlating against an ideal pulse train,
  not just raw autocorrelation strength, combined with a **log-Gaussian tempo prior**
  (continuously weights toward a configurable moderate-BPM center) and a decaying
  histogram of estimates over time. Beat tracking: cumulative beat-strength signal (CBSS,
  Stark 2011 PhD thesis) with actual beat *prediction*, not just tempo.
- **Mined**: the CBSS beat-tracking concept is cited as inspiration in `iron/beats.py`'s
  module docstring (reimplemented from scratch using the pre-existing
  `iron.dsp.track_beats` DP phase-locker, not ported). **The log-Gaussian tempo prior
  has NOT been built yet** — flagged in `docs/IRON_HANDOVER_2026-08-24.md` as one of two
  candidate fixes for Iron's half-time bias problem, still open.
- **Why MIT mattered here**: unlike essentia's AGPL (which forced arm's-length
  clean-room reimplementation from published papers only), BTT's MIT status means its
  actual source could be read and ported directly with attribution — no clean-room
  constraint needed. Iron chose to reimplement independently anyway, for consistency
  with the rest of the codebase's style and to keep the numpy-only, no-C-extension design.

### loop-tempo-estimator
- **Repo**: github.com/saintmatthieu/loop-tempo-estimator (Audacity's tempo-detection
  feature, extracted as a standalone library)
- **License**: **GPL-3.0** (confirmed via GitHub API) — harder license problem than
  essentia's AGPL, no stated commercial dual-license path. Code must never be copied or
  adapted; technique only, same posture as essentia.
- **Algorithm**: onset detection function → **tatum-hypothesis estimation**. Hypothesizes
  different tatum counts (smallest regular pulse subdivision), scores each by how well
  onset peaks land on that hypothesized grid (weighted by onset strength), picks the
  best-fitting hypothesis, and derives **both BPM and time signature** from it. This is
  the only tool surveyed that reaches time signature at all alongside BPM.
- **Caveat**: built for classifying/tempo-estimating short *loops* (Audacity's own
  integration skips files over a minute) — applying the idea to full multi-minute tracks
  needed real adaptation, not a direct port.
- **Mined**: `iron/beats.py`'s `_detect_beats_per_bar` (3/4 vs 4/4 meter detection) is a
  from-scratch reimplementation of this idea's core spirit — hypothesize small-integer
  groupings, score by accent alignment, pick the best fit — applied one level up (at the
  bar level over already-tracked beats, since Iron already has reliable beat positions)
  rather than at the tatum level over raw onsets.

### web-audio-beat-detector
- **Repo**: github.com/chrisguttandin/web-audio-beat-detector, npm package
- **License**: MIT
- **Algorithm**: Same Joe Sullivan lineage as realtime-bpm-analyzer — lowpass/threshold
  peak-picking, folded to a 90–180 BPM default range. Its own docs cite the same
  inspiration. One small extra: a `guess()` function returns the time of the first
  detected beat (a crude beat-grid anchor), which the other tools in this family don't
  expose.
- **Verdict**: nothing algorithmically new versus realtime-bpm-analyzer. Not pursued.

### SoundTouch (BPMDetect)
- **Repo**: github.com/rspeyer/soundtouch (iOS-packaged fork of Olli Parviainen's
  well-known SoundTouch v1.8.0)
- **License**: LGPL-2.1
- **Algorithm**: read `BPMDetect.cpp` directly. Decimate to ~500Hz → rectify/smooth into
  an envelope → cut anything below ~RMS threshold → short-term autocorrelation → single
  peak-find, clamped to a fixed min/max BPM range. **No octave-disambiguation logic
  beyond the range clamp.**
- **Verdict**: less sophisticated than Iron already is (Iron's harmonic-sum scoring +
  genre-band correction + breakdown-duration fit all postdate and exceed this). Nothing
  to mine — Iron has already surpassed this specific algorithm on paper, though real
  head-to-head accuracy hasn't been measured.

### phip1611/beat-detector
- **Repo**: github.com/phip1611/beat-detector
- **License**: MIT
- **Algorithm**: Rust, `no_std`-compatible, causal single-pass spectrum analysis.
  Individual beat/onset detection only — **no BPM/tempo output at all**. The project's
  own docs call the approach "good enough for simple songs" and explicitly invite
  contributions for a better algorithm.
- **Verdict**: too shallow to be a source of technique. Not pursued.

### BeatNet
- **Repo**: github.com/mjhydri/BeatNet (ISMIR 2021 paper implementation)
- **License**: CC-BY-4.0 for the whole repo (code and weights together, not split)
- **Algorithm**: CRNN (convolutional recurrent neural net) + particle filtering. Joint
  beat, downbeat, tempo, and meter tracking, real-time and offline modes.
- **Why not pursued further**: CC-BY-4.0 technically permits commercial use, but Creative
  Commons licenses aren't built for software — no patent grant, no code-specific warranty
  terms, and Creative Commons' own guidance discourages using CC licenses for code. Would
  also need in-product attribution. **beat_this (below) is strictly better on every axis
  that matters here** — same general capability class, genuinely clean MIT license
  instead. Not evaluated further once beat_this was found.

### beat_this
- **Repo**: github.com/CPJKU/beat_this (Johannes Kepler University Linz, "Beat This!" —
  accurate and general beat tracker)
- **License**: **MIT for both code AND published model weights** — explicitly stated in
  the README ("The code and the published model weights are released under the MIT
  license"). Cleanest license of every tool surveyed.
- **Algorithm**: transformer-based, trained on GTZAN, Ballroom, Harmonix, RWC and others.
  Install: `pip install beat-this`. API: `File2Beats(checkpoint_path="final0",
  device=..., dbn=False)`, then `beats, downbeats = file2beats(path)` — both as arrays of
  times in seconds.
- **Status: in use, as an offline validation oracle only — never a runtime dependency.**
  This was an explicit user decision (see `docs/IRON_HANDOVER_2026-08-24.md`), not a
  license-driven exclusion — beat_this's license would actually permit shipping it. The
  reasoning was to keep Iron's "no ML dependency, pure numpy" design intact and avoid the
  real cost of bundling PyTorch + a transformer checkpoint into a PyInstaller build.
  `scripts/benchmark_iron_beats.py` implements the oracle comparison (needs
  `pip install beat-this`, dev-only, not in any requirements file).
- **If this decision gets revisited**: beat_this is the only tool surveyed whose license
  status would make "ship it as a real optional dependency for downbeat/meter specifically"
  a legitimate option, distinct from Iron's own tempo/key detection. Worth remembering
  if Iron's own downbeat/meter accuracy turns out not to be fixable to a usable bar.

### madmom
- **Repo**: github.com/CPJKU/madmom (same lab as beat_this), Python audio/music signal
  processing library, ~19 published papers (Böck, Krebs, Widmer, 2010–2019) implemented.
- **License**: **split, and the split matters.** Source code itself: a permissive
  BSD-2-Clause-style license (confirmed by reading the LICENSE file directly). **Model
  and data files: CC-BY-NC-SA 4.0** — stated plainly in the same LICENSE file
  ("All model and data files are distributed under the Creative Commons
  Attribution-NonCommercial-ShareAlike 4.0").
- **Why this is a hard blocker in practice, not just in theory**: madmom's actual
  downbeat tracker (`DBNDownBeatTrackingProcessor`) consumes an RNN activation function
  from those NC-licensed pretrained models — so even though you *could* legally use
  madmom's DBN inference code standalone, the shipped, working downbeat pipeline is
  NC-gated. Same practical outcome as essentia's AGPL problem, different mechanism.
- **What's still interesting, unclaimed**: madmom's `DBNDownBeatTrackingProcessor` uses
  a genuinely different, more rigorous technique than anything Iron currently has — a
  proper Dynamic Bayesian Network over tempo/meter/downbeat jointly (Whiteley/Krebs
  2015-era published methodology), not a heuristic scoring pass. **Not yet mined** — the
  DBN *inference algorithm* itself (as opposed to the specific pretrained activation
  function it's normally fed) is published, non-proprietary technique and could in
  principle be clean-room reimplemented the same way BTT's CBSS and
  loop-tempo-estimator's tatum-hypothesis were, fed by Iron's own onset features instead
  of an NC-licensed RNN. Flagged here as an unexplored option for whoever next works on
  `iron/beats.py`'s downbeat accuracy, not attempted this round.

---

## Entry format

When you add a new tool, use this shape:

```markdown
### <Tool name>
- **Repo**: <github URL or package registry>
- **License**: <SPDX id or plain description, and HOW you confirmed it — GitHub API,
  LICENSE file, or repo metadata; don't just trust a README badge, verify it>
- **Algorithm**: <what it actually does, in enough technical detail that a reader can
  judge whether it's a different technique or the same family as something already
  listed>
- **Verdict**: <mined / in use / blocked / not pursued, and why — one clear sentence>
```

Also add a row to the summary table at the top. If the license is anything other than a
short, unambiguous permissive license (MIT, BSD, Apache-2.0), spell out exactly what the
restriction is and why it does or doesn't matter for a for-sale app — don't just write
"restrictive."
