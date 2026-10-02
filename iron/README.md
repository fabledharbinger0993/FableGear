# Iron

In-house audio analysis for FableGear. Iron listens to audio so Anvil doesn't have to.

Iron detects tempo and musical key from raw PCM and hands the results to Anvil as ordinary
candidate values. Anvil's own README states the contract: "Anvil cannot tell whether a value
came from Iron or from a caller typing it in by hand, and treats both the same way."

```python
from pathlib import Path
import iron

result = iron.analyze(Path("track.mp3"))
result.bpm, result.bpm_confidence        # 128.34, 1.0
result.initial_key, result.key_confidence  # "8A", 0.69

import anvil
anvil.write_fields(result.path, result.to_track_fields())
```

Iron never writes a file and never touches a tag. It decodes audio via an ffmpeg subprocess
(the same approach `audio_processor.py::_load_audio_ffmpeg` already uses, which sidesteps
`librosa.load()`'s audioread/AudioToolbox segfault risk on some MP3s) and runs detection
against the decoded samples.

## Why this exists, and what it isn't

FableGear's tempo/key detection previously lived in `audio_processor.py`, wrapping essentia
(`RhythmExtractor2013`) with a librosa (`chroma_cqt` + `beat_track`) fallback. For a for-sale
app, three things pushed toward an in-house replacement instead of continuing to wrap those
libraries:

- **essentia is AGPL-3.0 / commercial dual-licensed.** Copying or deriving from its source
  would obligate the same license on anything built from it -- untenable for proprietary,
  for-sale software. (librosa is BSD-3-Clause and was safe to read/adapt from; essentia's
  *published methodology* -- `RhythmExtractor2013`'s multifeature approach is documented MIR
  research, not Essentia-proprietary -- is fair game to build an independent implementation
  from. Iron's tempo detector is a clean-room implementation of published, non-proprietary
  technique, not a port of either library's code.)
- **Purpose-built beats general-purpose.** librosa and essentia are general music-analysis
  libraries; Iron is scoped to exactly what a DJ needs -- tempo and key precise enough to
  trust on a CDJ, not transcription-grade analysis.
- **No runtime dependency on either**, at ship time.

No third-party MIR or beat-tracking library sits underneath Iron: everything is built
directly on numpy (`iron/dsp.py` — STFT, spectral flux, chroma folding, autocorrelation).
numpy (and scipy, if ever needed) are the math substrate, not the thing being replaced --
both BSD-licensed and already depended on elsewhere in FableGear.

## How detection works

**Key** (`iron/key.py`): `iron.dsp.chroma()` folds FFT bin energy into a 12-bin pitch-class
profile (linear-frequency, not a full constant-Q transform -- adequate for a whole-track key
estimate, where transcription-grade low-frequency resolution isn't the goal). The profile is
correlated against the Krumhansl-Schmuckler major/minor key profiles (Krumhansl & Kessler
1982, published psychoacoustic data) and the best match is mapped to Camelot notation. This
correlation step was already original code before this package existed -- the only thing that
used to come from librosa was the chroma extraction itself.

**Tempo** (`iron/tempo.py`): five onset-strength functions (full-band, low, mid and high
band log-mel spectral flux, plus high-frequency content) each feed a windowed autocorrelation
(8 s windows averaged, so a periodicity must hold throughout the track). Each is reduced to a
tempo curve by harmonic-sum scoring and the curves are summed -- the multi-feature idea of
Zapata et al. 2014, independently implemented from the published method. Octave and 3:2
ambiguity -- telling a tempo from its double, half or compound -- is settled by a broad
log-normal tempo prior (centre 125 BPM, one sigma = 0.6 octave): harmonic-sum scoring alone is
biased toward slow candidates, and without the prior the detector picks half-tempo on most
tracks. The prior is a preference, not a band: a clear periodicity still wins from well
outside it. `bpm_confidence` is the winner's margin over the best unrelated rival tempo.

**Beat grid and meter** (`iron/beats.py`, opt-in via `analyze(want=(..., "downbeat_offset"))`):
built on top of an already-decided `bpm`, not a separate detector. `iron.dsp.track_beats` --
a dynamic-programming phase-locker (Ellis 2007) that already existed in Iron for a different,
reverted experiment (see its own docstring) -- turns the single tempo estimate into actual
beat positions. From there, a beat-level accent signal picks which of those positions is the
downbeat (loudest average onset strength) and whether the bar groups beats in 3s or 4s
(autocorrelating that same accent signal one level up, defaulting to 4/4 per
`TrackFields.time_signature`'s own "unless proven otherwise" stance). The approach was shaped
by comparing Iron against other tempo/beat tools: BTT's (Krzyzaniak, MIT) cumulative
beat-strength tracking, built on Stark's 2011 PhD thesis, for turning a known tempo into beat
positions; and loop-tempo-estimator's (Audacity, GPL -- read for its published method only,
the same posture Iron already takes toward essentia, never its code) tatum-hypothesis idea of
scoring small integer groupings by onset-alignment. Both reimplemented from scratch in numpy,
not ported.

## Known limitations (v1)

- **Which beat is the downbeat is validated on synthetic fixtures only, not real music
  yet.** Picking WHICH of the locked beat positions is beat 1 relies on it carrying a
  stronger accent than its neighbors -- true of real DJ productions, but getting a
  same-instrument amplitude accent to survive measurement took two fixes (see
  `iron/beats.py`'s module docstring): a dedicated, non-log-compressed kick-band energy
  feature (`iron.dsp.band_energy`, an idea prompted by a Perplexity-sourced review of
  FableGear's approach that specifically flagged low-frequency kick emphasis), and
  searching backward from the phase-locked frame for that feature's own peak rather than
  sampling it where onset_env's flux happens to land. With both, `tests/test_iron_beats.py`
  validates the correct beat wins across a BPM sweep on synthetic fixtures -- real-music
  validation is the same "not yet run through the ground-truth benchmark" gap bpm itself
  has. `beat_grid_confidence` surfaces the residual uncertainty either way -- a low value
  means "don't trust which beat is 1 here," the same spirit as `bpm_confidence`. Time
  signature (3/4 vs the 4/4 default) has the same caveat, and doesn't attempt compound
  meters (6/8 and similar) at all -- see `iron/beats.py`'s module docstring for why.
- **Accuracy, measured.** Against Rekordbox's own BPMs for a random 300-track sample of the
  maintainer's library (5,844 tracks matched by filename), Iron scores 91.7% exact (+-0.6
  BPM), 95.0% within 1%, 96.3% MIREX (+-4%) -- versus essentia's historical 91.4% / 94.8% /
  98.3% and librosa's 13.4% / 36.8% / 90.7% (`requirements_optional.txt`). Both sit on the same
  kind of ground truth (a Rekordbox grid), not on a human-annotated corpus, and the library is
  club music, so the tempo prior is tuned to it; a library of very slow or very fast music
  would score lower. Remaining errors are mostly half-tempo and 4:3. **essentia and librosa
  remain in `requirements.txt` / `requirements_optional.txt` and Iron is not yet the primary
  detection path anywhere in FableGear** -- that cutover is a separate decision.
- **A near-perfectly-periodic signal is a genuinely hard case.** Autocorrelation-based
  disambiguation relies on real asymmetry in the onset pattern (dynamics, kick/snare
  contrast); an idealized, exactly-regular pulse train is mathematically ambiguous between
  its true period and every one of its own integer divisors. `tests/test_iron_tempo.py`
  documents one such case (`xfail`, not silently skipped).
- **Fingerprinting is out of scope.** Chromaprint (used for internal dedup/novelty matching
  and for AcoustID metadata lookups) is a separate algorithm family and a separate,
  not-yet-started project. AcoustID enrichment specifically keeps using real Chromaprint
  regardless of that project's outcome, since matching AcoustID's public database inherently
  requires a spec-compliant fingerprint.

## Tests

```
pytest tests/test_iron_key.py tests/test_iron_tempo.py tests/test_iron_beats.py tests/test_iron_dryrun.py
```

Fully synthetic fixtures (numpy-generated tones/chords and kick+hi-hat patterns) -- no real
music, no copyright question. `test_iron_tempo.py`'s docstring explains why the fixtures
include timing humanization and a bar-level accent rather than a bare metronome click.
