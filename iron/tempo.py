"""
fablegear / iron / tempo.py

Tempo (BPM) detection: onset envelope -> autocorrelation -> DJ-genre-aware tempo pick.

Standard MIR technique -- spectral-flux onset detection feeding an autocorrelation
periodicity analysis, the approach underlying published beat trackers going back to Scheirer
1998 and refined through e.g. Ellis 2007 ("Beat Tracking by Dynamic Programming"). This is an
independent implementation of a published, non-proprietary method, not a port of any
dependency's internals.

Octave disambiguation is the hard part. Picking the single strongest autocorrelation peak
with no other information is what produces the ~13% exact-BPM accuracy documented for the
librosa fallback path in requirements_optional.txt -- a plain peak can't reliably tell a
tempo from its double or half, and worse: a short, sharp click can leave a *stronger* raw
autocorrelation peak at 2x its true period than at the true period itself, an artifact of
onset energy landing unevenly across the analysis-frame grid. Two corrections handle it:

  Harmonic-sum scoring. A true fundamental period T has strong autocorrelation not just at
  T but reliably at 2T, 3T, ... A spurious detection at 2T doesn't get that same
  reinforcement from T itself (T is shorter than 2T, not a multiple of it), so scoring each
  candidate by its own lag plus a few of its multiples -- not the raw lag value alone --
  systematically favours the true, faster fundamental over its subharmonic aliases.

  Genre-band bonus. A working DJ library clusters tightly by genre (house ~118-130,
  techno ~125-145, ...) -- real prior information a generic MIR tool doesn't get to assume.
  Applied on top of the harmonic-sum score to break what ties remain. Bands carry a small
  pad at their edges (_BAND_PAD_BPM) so a true tempo whose autocorrelation energy straddles
  two adjacent integer lags right at a boundary -- a real quantization effect, not a design
  choice -- doesn't get excluded on a technicality. The correction itself scans every lag in
  range for the best in-band rival rather than only checking clean multiples/submultiples of
  the raw winner, because on real (non-percussion-uniform) music the true tempo is not
  always a tidy ratio of the wrong one.

Three further corrections, added after real-world benchmarking (docs/IRON_RESEARCH.md,
docs/IRON_HANDOVER_2026-08-24.md) found the genre-band prior alone leaves two gaps: it can't
help a track whose true tempo falls outside every hand-picked band, and it has no
independent way to catch a raw Pass-1 pick landing at half or double the true tempo. All
three are independent numpy-only implementations of published, non-proprietary techniques,
not ports of any dependency's code, and all are deliberately conservative -- gated to switch
only on decisive evidence, never to override an already-confident pick:

  Multiband scoring (Klapuri 2003). The harmonic-sum score above runs on one broadband
  onset envelope, which forces genuinely distinct rhythmic voices -- a kick's sub-bass thump,
  a syncopated hi-hat's high tick -- into a single combined signal. When those voices carry
  conflicting periodicities (a 2-against-3 compound meter, common in disco/soul/nu-disco),
  the combined flux can blur exactly the ambiguity that needs resolving. `dsp.
  onset_envelope_multiband` computes flux independently per frequency band; Pass 1/2's
  scoring folds each band's own harmonic-sum score in alongside the broadband one, so a
  candidate has to be supported across multiple independent rhythmic voices, not just one.

  Cyclic-tempogram octave correction (Grosche & Müller). `dsp.cyclic_tempo_strength` pools a
  track's own autocorrelation evidence across every octave of a candidate's tempo class
  (log2(bpm) mod 1) into one octave-invariant curve -- unlike _GENRE_BANDS, this is derived
  from the signal itself, so it still has something to say about a track whose true tempo
  isn't near any hand-picked genre center.

  DP transition-penalty-variance (Ellis 2007 phase-locker) was also tried as a fourth
  correction and reverted -- see the long comment where Pass 3 used to be, below. Root cause
  was genuinely different from the raw-score comparison already documented as reverted on
  dsp.track_beats: variance is deceptively LOW for a candidate at the wrong half-time period,
  not high, because track_beats' search window scales with the candidate's own period and
  ends up wide enough to silently phase-lock onto the true tempo's real spacing anyway.
  dsp.track_beats_with_penalty_variance remains a valid primitive for its original, narrower
  purpose (self-consistency of a path at an already-known-correct period) -- just not for
  this cross-period comparison, at least not in the form tried here.
"""

from __future__ import annotations

import numpy as np

from iron import dsp

# Typical genre tempo centers a working DJ library clusters around. Not exhaustive and not
# genre-labelled -- just density priors for octave disambiguation.
#
# The (60.0, 85.0) band was added from a real 1000-track genre-diverse benchmark (real
# embedded-tag ground truth, not synthetic): tracks whose true tempo fell in ANY band scored
# 50.3% MIREX accuracy; tracks outside every band scored just 8.2% -- and every single
# out-of-band track in that 1000-track sample had a true tempo between 62 and 78 BPM (median
# 72), exactly the gap this band closes. Real content living there: downtempo, slow hip-hop,
# R&B, soul ballads -- genres a DJ-genre-tempo prior previously had nothing to say about.
_GENRE_BANDS: tuple[tuple[float, float], ...] = (
    (60.0, 85.0),     # downtempo / slow hip-hop / R&B / soul ballads
    (85.0, 100.0),    # hip-hop / trap
    (95.0, 115.0),    # downtempo / halftime
    (118.0, 130.0),   # house
    (125.0, 145.0),   # techno / trance
    (140.0, 155.0),   # dubstep / half-time DnB
    (160.0, 180.0),   # drum & bass / jungle
    (180.0, 220.0),   # hardcore / gabber
)


# Real music quantizes its true period across a discrete lag grid, and the
# true tempo's harmonic energy can straddle two adjacent integer lags right at
# a band's edge -- found on a real 117.45 BPM house track whose autocorrelation
# peak split across lag 21 (123.05 BPM, in-band but under-scoring) and lag 22
# (117.45 BPM, well-scored but 0.55 BPM outside the band as originally drawn).
# A fixed pad absorbs that without having to hand-tune every band boundary.
_BAND_PAD_BPM = 5.0


def _in_genre_band(bpm: float) -> bool:
    return any(lo - _BAND_PAD_BPM <= bpm <= hi + _BAND_PAD_BPM for lo, hi in _GENRE_BANDS)


# The (60, 85) band specifically -- unlike every other band, its range is also almost exactly
# where a half-time misread of nearly any OTHER band lands (half of 125-180 is 62.5-90). A
# raw Pass-1 pick landing here can just as easily be a genuine slow track as a half-time
# alias of a faster one, so this band alone doesn't get to shortcut Pass 2's rival search the
# way the others do -- see the real-benchmark regression this fixed (a 133 BPM synthetic
# fixture starting to misdetect as 66.44 once (60, 85) was added: 66.44 "looked" already
# validated, and Pass 2 stopped searching for a rival it would otherwise have found).
_LOW_BAND = _GENRE_BANDS[0]
assert _LOW_BAND == (60.0, 85.0), "the low-band special-case below assumes this is _GENRE_BANDS[0]"


def _in_low_band(bpm: float) -> bool:
    lo, hi = _LOW_BAND
    return lo - _BAND_PAD_BPM <= bpm <= hi + _BAND_PAD_BPM


# How much of the raw winner's score a rival candidate must retain to be considered real
# rather than noise. 0.5 is deliberately permissive: a click train with even, unaccented
# beats (or a hi-hat/kick pair of similar onset magnitude) can leave near-tied strength at
# T and 2T -- see iron/tempo.py's module docstring -- and that near-tie is exactly the case
# genre banding exists to break.
_RIVAL_THRESHOLD = 0.5

# Conventional breakdown/bridge lengths, in bars (4/4 assumed). Not exhaustive -- arrangement
# convention, same informal-prior spirit as _GENRE_BANDS, not a hard rule.
_CONVENTIONAL_BAR_COUNTS = (8, 12, 16, 24, 32)

# How close (in bars) a breakdown's implied length must land to a conventional count to be
# treated as a genuinely tight structural fit -- see the Pass 4 comment in detect_tempo for
# why this must be a strict, one-sided gate rather than a plain "closer of two" comparison.
_BAR_FIT_TIGHT_BARS = 0.5


def _bar_fit_distance(duration_s: float, bpm: float) -> float:
    """Distance, in bars, from `duration_s` at `bpm` to the nearest conventional bar count."""
    bars = duration_s * bpm / 60.0 / 4.0
    nearest = min(_CONVENTIONAL_BAR_COUNTS, key=lambda r: abs(bars - r))
    return abs(bars - nearest)


# How much weight the multiband harmonic-sum score (Klapuri 2003) carries relative to the
# broadband one when combined -- kept as a genuine second vote, not the dominant term, since
# broadband harmonic-sum scoring is the validated primary signal.
_MULTIBAND_WEIGHT = 0.5

# Cyclic-tempogram octave correction (Grosche & Müller). Octave ratios checked are the same
# set Pass 4's breakdown-fit rival search already uses.
_CYCLIC_OCTAVE_BINS = 60
_CYCLIC_RATIOS = (2.0, 0.5, 1.5, 2.0 / 3.0)
# A rival must pool at least this much MORE cyclic-tempo-class evidence than the current pick
# to be considered decisive, not noise.
_CYCLIC_MARGIN = 1.3


_HARMONICS = (1, 2, 3, 4)
_HARMONIC_WEIGHT = (1.0, 0.6, 0.4, 0.3)


def _harmonic_score(acf: np.ndarray, lag: int) -> float:
    """
    Sum positive autocorrelation at `lag` and its first few integer multiples, weighted
    down for higher multiples. Reinforces true fundamentals (whose multiples are real
    periodicities of the same pulse train) over subharmonic aliases (which aren't
    multiples of the true, faster period and so don't collect this same credit).

    A compound-meter (1.5x / 3-against-2) credit term was tried here and reverted: on a
    real 75-track library test it failed to flip the one case it targeted (both the true
    fundamental and its wrong, higher-scoring rival share enough overlapping harmonic
    content that a modest asymmetric weight boosted both roughly proportionally), and at a
    strong enough weight to matter, it broke an existing synthetic regression test
    (test_detect_tempo_within_tolerance[165], a clean 2x error). This is flagged as a real,
    unsolved gap -- see docs/ANVIL_IRON_STATUS.md -- not something to patch again without a
    fundamentally different approach (e.g. a genuinely independent second onset-detection
    feature, closer to what essentia's multifeature ensemble does, rather than a second term
    on the same single autocorrelation signal).
    """
    total = 0.0
    for k, weight in zip(_HARMONICS, _HARMONIC_WEIGHT, strict=True):
        idx = lag * k
        if idx >= acf.shape[0]:
            break
        value = acf[idx]
        if value > 0:
            total += weight * value
    return total


def _multiband_harmonic_score(band_acfs: np.ndarray, lag: int) -> float:
    """
    Sum `_harmonic_score` independently across each band's own autocorrelation, then combine.
    This is the "genuinely independent second onset-detection feature" flagged as the
    unsolved gap in `_harmonic_score`'s own docstring: a candidate now has to be supported by
    multiple distinct rhythmic voices (kick, hi-hat, ...), not just whichever periodicity
    dominates a single combined broadband signal.
    """
    return float(sum(_harmonic_score(band_acfs[i], lag) for i in range(band_acfs.shape[0])))


def _combined_score(acf: np.ndarray, band_acfs: np.ndarray, lag: int) -> float:
    """Broadband harmonic-sum score plus a weighted multiband vote -- the scoring function
    Pass 1 and Pass 2 use throughout, in place of `_harmonic_score` alone."""
    return _harmonic_score(acf, lag) + _MULTIBAND_WEIGHT * _multiband_harmonic_score(band_acfs, lag)


def detect_tempo(
    y: np.ndarray,
    sr: int,
    *,
    bpm_min: float = 60.0,
    bpm_max: float = 180.0,
    hop_length: int = 512,
) -> tuple[float, float] | None:
    """
    Return (bpm, confidence) for a decoded clip, or None if no reliable periodicity is
    found (silence, or a clip too short to establish one).

    Default search range narrowed from the original (30, 300) to (60, 180), backed by the
    same 1000-track real-tag benchmark that motivated the new (60, 85) genre band: 0 of 1000
    real tracks had a true tempo below 60 BPM, and only 16 (1.6%) were above 180 -- mostly
    hardcore/gabber. That 1.6% is a real, deliberate cost: a track genuinely faster than 180
    BPM can no longer be found at all with these defaults (searching a range that excludes
    the true answer can't return it, by construction) -- pass wider bounds explicitly
    (bpm_max=300.0 restores the old ceiling) for a library known to contain that content.

    `confidence` is the winning lag's autocorrelation strength relative to the strongest
    lag anywhere in the signal (0..1) -- not on the same scale as essentia's beat-tracker
    confidence, but usable the same way: a low value means "eyeball this grid before a gig."
    """
    env = dsp.onset_envelope(y, sr, hop_length=hop_length)
    if env.shape[0] < 8 or not np.any(env):
        return None

    acf = dsp.autocorrelate(env - env.mean())
    frame_rate = sr / hop_length

    # Multiband onset envelopes (Klapuri 2003) feed _combined_score below -- see the module
    # docstring's "Multiband scoring" section. A band with no meaningful energy just
    # contributes zero score, so this is always safe to compute even on sparse/quiet tracks.
    band_envs = dsp.onset_envelope_multiband(y, sr, hop_length=hop_length)
    band_acfs = np.array(
        [dsp.autocorrelate(row - row.mean()) for row in band_envs]
    ) if band_envs.shape[0] else np.zeros((0, acf.shape[0]))

    lag_lo = max(1, int(frame_rate * 60.0 / bpm_max))
    lag_hi = min(acf.shape[0] - 1, int(frame_rate * 60.0 / bpm_min))
    if lag_hi <= lag_lo:
        return None

    peak_strength = float(np.max(np.abs(acf[1:]))) if acf.shape[0] > 1 else 0.0
    if peak_strength <= 0:
        return None

    # Pass 1: find the strongest candidate by combined (broadband + multiband) harmonic-sum
    # score, no genre bias. This is the honest "what does the signal say" answer before any
    # DJ-domain prior gets applied to it.
    best_lag: int | None = None
    best_score = -np.inf
    for lag in range(lag_lo, lag_hi + 1):
        strength = acf[lag]
        if strength <= 0:
            continue
        score = _combined_score(acf, band_acfs, lag)
        if score > best_score:
            best_score = score
            best_lag = lag

    if best_lag is None:
        return None

    # Pass 2: octave correction. If some OTHER lag in the valid range both (a) lands inside
    # a genre-typical band the raw winner itself misses, and (b) still carries a meaningful
    # fraction of the raw winner's score -- a real rival, not noise -- prefer whichever such
    # candidate scores highest. This is where genre-band knowledge earns its keep: only to
    # resolve a signal that's already ambiguous, never to override a candidate that's
    # clearly winning on its own.
    #
    # Scanning every lag directly, rather than only checking clean integer/simple-fraction
    # multiples of the raw winner, is deliberate: real music's competing periodicity is not
    # always a tidy ratio of the wrong one. Found on a real track where the raw winner and
    # the true tempo were related by a ratio of ~2.52 -- not 2, 3, 4, 0.5, 1/3, or 1/4, so a
    # ratio-restricted search would never have found the true candidate at all, no matter how
    # generous its score.
    best_bpm = frame_rate * 60.0 / best_lag
    chosen_lag = best_lag
    if not _in_genre_band(best_bpm) or _in_low_band(best_bpm):
        rival_lag: int | None = None
        rival_score = -np.inf
        for lag in range(lag_lo, lag_hi + 1):
            if lag == best_lag:
                continue
            bpm = frame_rate * 60.0 / lag
            if not _in_genre_band(bpm):
                continue
            score = _combined_score(acf, band_acfs, lag)
            if score >= _RIVAL_THRESHOLD * best_score and score > rival_score:
                rival_score = score
                rival_lag = lag
        if rival_lag is not None:
            chosen_lag = rival_lag
            best_bpm = frame_rate * 60.0 / rival_lag

    # Pass 2b: cyclic-tempogram octave correction (Grosche & Müller) -- see the module
    # docstring. Independent of _GENRE_BANDS: pools this track's OWN autocorrelation evidence
    # across every octave of a candidate's tempo class, so it still has something to say
    # about a track whose true tempo isn't near any hand-picked genre center (the documented
    # gap in Pass 2 -- docs/IRON_HANDOVER_2026-08-24.md: "tracks outside all of them ... get
    # no help and stay wrong"). Gated on two conditions so this can't override an
    # already-confident pick on cyclic strength alone: the rival must pool decisively more
    # tempo-class evidence (_CYCLIC_MARGIN), AND still carry a real share of the current
    # pick's raw combined score (_RIVAL_THRESHOLD, the same guard Pass 2 uses).
    cyclic_curve = dsp.cyclic_tempo_strength(
        acf, frame_rate, bpm_min=bpm_min, bpm_max=bpm_max, octave_bins=_CYCLIC_OCTAVE_BINS
    )
    current_bpm = frame_rate * 60.0 / chosen_lag
    current_cyclic = dsp.cyclic_tempo_class_lookup(cyclic_curve, current_bpm)
    current_score = _combined_score(acf, band_acfs, chosen_lag)
    best_cyclic_lag: int | None = None
    best_cyclic_strength = current_cyclic
    for ratio in _CYCLIC_RATIOS:
        lag = round(chosen_lag * ratio)
        if lag < lag_lo or lag > lag_hi or lag == chosen_lag:
            continue
        rival_bpm = frame_rate * 60.0 / lag
        rival_cyclic = dsp.cyclic_tempo_class_lookup(cyclic_curve, rival_bpm)
        rival_score = _combined_score(acf, band_acfs, lag)
        if (
            rival_cyclic > best_cyclic_strength * _CYCLIC_MARGIN
            and rival_score >= _RIVAL_THRESHOLD * current_score
        ):
            best_cyclic_strength = rival_cyclic
            best_cyclic_lag = lag
    if best_cyclic_lag is not None:
        chosen_lag = best_cyclic_lag
        best_bpm = frame_rate * 60.0 / best_cyclic_lag

    # A Pass 3 using dsp.track_beats_with_penalty_variance() (DP transition-penalty variance,
    # a comparison signal genuinely different from the raw/magnitude-normalized DP score
    # already tried and reverted -- see dsp.track_beats' docstring) was tried here and
    # reverted too, for a new and different reason than that prior attempt: it broke 9 of 12
    # synthetic regression cases (test_detect_tempo_within_tolerance), ALL flipping to exactly
    # half the true tempo. Root-caused, not a tuning-margin problem -- track_beats' search
    # window scales with the CANDIDATE period (search = period * search_multiple), so at a
    # candidate double the true period, the search window is wide enough that the DP tracker
    # silently phase-locks onto the TRUE, faster rhythm's own beat spacing while still being
    # scored against the wrong (doubled) target period. Every transition then incurs the same
    # constant mismatch penalty (log(true_period / candidate_period))^2 -- a uniformly-wrong-
    # by-a-fixed-ratio path, which produces a deceptively LOW variance (measured 0.002 at the
    # wrong half-time candidate vs. 2.97 at the true tempo, on the 174 BPM fixture) precisely
    # because it's consistent, not because it's correct. The true tempo's path, by contrast,
    # has genuine irregularity from this fixture's kick/hi-hat onsets legitimately competing
    # for the same phase-locked slots. Fixing this would need a search window that doesn't
    # scale with the candidate's own (possibly wrong) period -- not attempted here; flagged
    # for whoever revisits this rather than re-gated blind. dsp.track_beats_with_penalty_
    # variance itself is unaffected and still valid for its tested, narrower purpose --
    # measuring self-consistency of a phase-locked path at a period already known correct,
    # not for comparing across candidate periods.

    # Pass 4: breakdown-duration structural fit. A DJ mix or club edit's bridge/breakdown is
    # almost always a "round" length in bars (8/16/32, arrangement convention, not a signal
    # property) -- so the duration of that section, divided by each candidate BPM's beat
    # length, should land close to a round bar count for the TRUE tempo and not necessarily
    # for a compound-meter alias. This needs the caller to have decoded enough of the track
    # to contain a real mid-song breakdown, not just the first 90 seconds -- see iron/api.py.
    #
    # exclude_frac is small here, NOT find_breakdown_duration's own 0.15 default: the caller
    # (iron/api.py) already decodes only the track's body, having excluded the real intro
    # and outro at the file level. Applying another 15% exclusion on top of that double-trims
    # an already-trimmed window -- confirmed on a real track where it clipped the true
    # breakdown from 31.4s down to 24.7s, throwing away exactly the portion that made the
    # bar-count fit tight. A small edge margin still guards against pure window-boundary
    # artifacts without re-excluding real structure.
    #
    # Deliberately conservative otherwise: only overrides the current pick when the fit is
    # GENUINELY tight for exactly one candidate. A naive "whichever is closer" comparison was
    # tested and rejected -- on an already-correct track, the wrong half-tempo candidate had
    # a *closer* (but still loose, ~0.8 bars off) fit than the correct one (~1.6 bars off),
    # which would have flipped a working answer. Requiring a tight fit on one side and a
    # loose fit on the other keeps the two ambiguous-but-not-clean real cases this was tested
    # against silent (correctly -- neither had a fit worth trusting) while still catching the
    # one real case where the true tempo's breakdown length landed within a third of a bar of
    # 16 bars exactly, against the wrong candidate's 1.5+ bar miss.
    breakdown_s = dsp.find_breakdown_duration(env, frame_rate, exclude_frac=0.03)
    if breakdown_s is not None:
        chosen_bpm = frame_rate * 60.0 / chosen_lag
        chosen_dist = _bar_fit_distance(breakdown_s, chosen_bpm)
        best_rival_lag: int | None = None
        best_rival_dist = np.inf
        for ratio in (2.0, 0.5, 1.5, 2.0 / 3.0):
            lag = round(chosen_lag * ratio)
            if lag < lag_lo or lag > lag_hi or lag == chosen_lag:
                continue
            rival_bpm = frame_rate * 60.0 / lag
            dist = _bar_fit_distance(breakdown_s, rival_bpm)
            if dist < best_rival_dist:
                best_rival_dist = dist
                best_rival_lag = lag
        if (
            best_rival_lag is not None
            and best_rival_dist < _BAR_FIT_TIGHT_BARS
            and chosen_dist >= _BAR_FIT_TIGHT_BARS
        ):
            chosen_lag = best_rival_lag
            best_bpm = frame_rate * 60.0 / best_rival_lag

    strength = float(acf[chosen_lag])
    refined_lag = _parabolic_peak(acf, chosen_lag)
    if refined_lag > 0:
        best_bpm = frame_rate * 60.0 / refined_lag

    confidence = float(np.clip(strength / peak_strength, 0.0, 1.0))
    return round(best_bpm, 2), round(confidence, 2)


def _parabolic_peak(acf: np.ndarray, lag: int) -> float:
    """
    Sub-frame refinement of an integer-lag peak by fitting a parabola through it and its
    two neighbours. Autocorrelation only has one value per frame (here, ~23ms at 22050 Hz /
    512 hop), so the raw integer-lag estimate alone carries a quantization error large
    enough to matter -- at 128 BPM it's the difference between 128.0 and 129.2, a gap a DJ
    tool can't shrug off. Standard technique for periodicity/pitch estimation refinement.
    """
    if lag <= 0 or lag >= acf.shape[0] - 1:
        return float(lag)
    left, center, right = acf[lag - 1], acf[lag], acf[lag + 1]
    denom = left - 2.0 * center + right
    if denom == 0:
        return float(lag)
    shift = 0.5 * (left - right) / denom
    # A well-formed peak shifts by less than half a frame; a larger shift means the
    # neighbours aren't really describing this peak (e.g. it's at the edge of the search
    # window) and the unrefined integer lag is more trustworthy than the fit.
    if abs(shift) >= 1.0:
        return float(lag)
    return lag + shift
