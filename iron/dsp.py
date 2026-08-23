"""
fablegear / iron / dsp.py

Low-level signal-processing primitives -- numpy only. No third-party MIR or audio-analysis
library sits underneath this: framing, windowing, the FFT-based STFT, spectral flux, chroma
folding, and autocorrelation are all built directly on numpy so nothing above this layer
needs librosa or essentia.

This is Iron's equivalent of Anvil's id3.py: the actual sample-level work, with a thin,
well-named API above it (tempo.py, key.py) that never touches an FFT directly.
"""

from __future__ import annotations

import numpy as np


def hann_window(n: int) -> np.ndarray:
    """Periodic Hann window (the convention STFT analysis expects -- a symmetric window
    introduces a small spectral bias at frame boundaries that the periodic form avoids)."""
    if n <= 1:
        return np.ones(n, dtype=np.float64)
    k = np.arange(n)
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * k / n)


def frame_signal(y: np.ndarray, frame_length: int, hop_length: int) -> np.ndarray:
    """
    Split `y` into overlapping frames, shape (n_frames, frame_length).

    Trailing samples that don't fill a complete frame are dropped -- for tempo/key analysis
    on a multi-second clip that's a few milliseconds of the very end, never worth the
    complexity of a padded partial frame.
    """
    y = np.ascontiguousarray(y, dtype=np.float64)
    n = y.shape[-1]
    if n < frame_length:
        return np.empty((0, frame_length), dtype=np.float64)
    n_frames = 1 + (n - frame_length) // hop_length
    stride = y.strides[-1]
    shape = (n_frames, frame_length)
    strides = (hop_length * stride, stride)
    return np.lib.stride_tricks.as_strided(y, shape=shape, strides=strides, writeable=False)


def stft(y: np.ndarray, *, n_fft: int = 2048, hop_length: int = 512) -> np.ndarray:
    """
    Short-time Fourier transform.

    Returns a (n_frames, n_fft // 2 + 1) complex array -- frame-major, the opposite of some
    libraries' freq-major convention, because every caller here iterates frame by frame.
    """
    window = hann_window(n_fft)
    frames = frame_signal(y, n_fft, hop_length)
    if frames.shape[0] == 0:
        return np.empty((0, n_fft // 2 + 1), dtype=np.complex128)
    return np.fft.rfft(frames * window, axis=-1)


def magnitude_spectrogram(y: np.ndarray, *, n_fft: int = 2048, hop_length: int = 512) -> np.ndarray:
    return np.abs(stft(y, n_fft=n_fft, hop_length=hop_length))


def onset_envelope(y: np.ndarray, sr: int, *, n_fft: int = 2048, hop_length: int = 512) -> np.ndarray:
    """
    Spectral-flux onset strength: the half-wave-rectified frame-to-frame increase in
    log-magnitude spectral energy, summed across frequency. A standard onset-detection
    technique (Bello et al. 2005; Dixon 2006) -- independent of any MIR library's
    implementation, not a port of one.

    Log-magnitude (rather than raw magnitude) is used so a single loud transient doesn't
    dwarf every other onset in the track; flux on raw magnitude is dominated by whichever
    frame is loudest, which is a poor proxy for rhythmic pulse.

    Returns one value per frame, at sr / hop_length frames per second. `sr` is accepted for
    API symmetry with the frame-rate math callers do downstream, even though this function
    itself only needs frame counts.
    """
    del sr  # not needed here; kept for a consistent (y, sr, ...) call shape across iron.dsp
    mag = magnitude_spectrogram(y, n_fft=n_fft, hop_length=hop_length)
    if mag.shape[0] < 2:
        return np.zeros(mag.shape[0], dtype=np.float64)
    log_mag = np.log1p(mag)
    flux = np.sum(np.maximum(np.diff(log_mag, axis=0), 0.0), axis=1)
    return np.concatenate([[0.0], flux])


def autocorrelate(x: np.ndarray) -> np.ndarray:
    """
    Full autocorrelation of `x`, computed via FFT (fast for the frame counts an onset
    envelope produces -- a few hundred to a few thousand samples).

    autocorrelate(x)[0] is the zero-lag term (signal energy); index k is the correlation at
    a lag of k frames.
    """
    n = x.shape[-1]
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    # Zero-pad to at least 2n so the FFT-based correlation doesn't wrap circularly.
    size = 1
    while size < 2 * n:
        size *= 2
    spectrum = np.fft.rfft(x, n=size)
    return np.fft.irfft(spectrum * np.conj(spectrum), n=size)[:n]


def band_energy(
    y: np.ndarray, sr: int, *, fmin: float, fmax: float, n_fft: int = 2048, hop_length: int = 512
) -> np.ndarray:
    """
    Per-frame magnitude-squared energy summed over [fmin, fmax] Hz -- raw energy, NOT
    log-compressed flux like onset_envelope. Appropriate when comparing a small number of
    already-located candidate positions against each other (their relative energy IS the
    signal, e.g. iron.beats scoring which of 4 known beat positions is the downbeat),
    rather than hunting for transients across a whole track from scratch, where
    onset_envelope's log-compression job of taming dynamic range matters more (see its own
    docstring). A percussive instrument's fundamental+first-harmonic band (a kick drum,
    roughly 40-120Hz) carries less contamination from unrelated broadband content
    (hi-hats, cymbals, vocals) than a full-spectrum feature would.
    """
    mag = magnitude_spectrogram(y, n_fft=n_fft, hop_length=hop_length)
    if mag.shape[0] == 0:
        return np.zeros(0, dtype=np.float64)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    band = (freqs >= fmin) & (freqs <= fmax)
    if not np.any(band):
        return np.zeros(mag.shape[0], dtype=np.float64)
    return np.sum(mag[:, band] ** 2, axis=1)


def track_beats(
    onset_env: np.ndarray, period: float, *, alpha: float = 100.0, search_multiple: float = 2.0
) -> tuple[list[int], float]:
    """
    Dynamic-programming beat tracker (Ellis 2007, "Beat Tracking by Dynamic Programming") --
    an independent implementation of a published, non-proprietary method, not a port of any
    library's internals.

    Unlike a static autocorrelation peak (which scores a single aggregate periodicity
    statistic for the whole clip), this finds an actual SEQUENCE of beat times maximizing
    onset strength at each beat plus a log-Gaussian penalty for deviating from `period`
    between consecutive beats -- useful on its own for turning a single already-decided
    tempo into actual beat positions (phase-locking), which is what this function is
    validated for.

    Comparing its returned `total_score` ACROSS candidate periods to pick which period is
    correct was tried in iron/tempo.py and reverted: neither raw nor baseline-subtracted
    (mean-onset-corrected) normalization removes a systematic bias toward faster candidates
    -- a faster period fits more beats into the same clip, giving the optimizer more chances
    at favorable alignment independent of whether that period is musically real. Confirmed
    by breaking several already-correct synthetic cases in a full sweep, not just a
    tuning-margin problem. A fair cross-period comparison likely needs to control for path
    length directly (e.g. resampling every candidate to the same beat count) rather than
    normalizing the raw score by frames or by beats -- unsolved, flagged for whoever
    revisits this rather than re-attempted blind.
    """
    n = onset_env.shape[0]
    if n == 0:
        return [], 0.0

    period_i = max(1, round(period))
    search = max(1, round(period * search_multiple))
    cumulative = np.full(n, -np.inf)
    backptr = np.full(n, -1, dtype=np.int64)

    for t in range(n):
        best_score = float(onset_env[t])
        best_prev = -1
        lo, hi = max(0, t - search), t - 1
        for tp in range(lo, hi + 1):
            dt = t - tp
            penalty = -alpha * (np.log(dt / period_i)) ** 2
            prev = cumulative[tp] if cumulative[tp] > -np.inf else 0.0
            score = prev + penalty + onset_env[t]
            if score > best_score:
                best_score, best_prev = score, tp
        cumulative[t] = best_score
        backptr[t] = best_prev

    end = int(np.argmax(cumulative))
    beats = [end]
    cur = end
    while backptr[cur] >= 0:
        cur = backptr[cur]
        beats.append(cur)
    beats.reverse()
    return beats, float(cumulative[end])


def find_breakdown_duration(
    onset_env: np.ndarray, frame_rate: float, *, exclude_frac: float = 0.15, smooth_seconds: float = 4.0
) -> float | None:
    """
    Duration in seconds of the longest sustained low-energy span in the track -- the
    breakdown/bridge/plateau a DJ mix nearly always has, excluding the first/last
    `exclude_frac` of the clip (intro and outro are ALSO low-energy, but they aren't the
    structural section this is looking for).

    Onset strength is smoothed over `smooth_seconds` first: a bare per-frame onset value
    dips between every individual hit even in the busiest section, so the raw envelope has
    no sustained low span to find at all without smoothing over several beats first.

    "Low energy" is anything more than half a standard deviation below the track's own mean
    -- relative to itself, not an absolute threshold, since a quiet ambient track and a loud
    club track have no comparable absolute energy scale.

    Returns None if the clip is too short to have a meaningful body/edge split, or has no
    span that dips notably below its own average (a track with no real breakdown).
    """
    n = onset_env.shape[0]
    lo_bound, hi_bound = int(n * exclude_frac), int(n * (1 - exclude_frac))
    if hi_bound - lo_bound < int(smooth_seconds * frame_rate):
        return None

    window = max(1, int(smooth_seconds * frame_rate))
    kernel = np.ones(window) / window
    smoothed = np.convolve(onset_env, kernel, mode="same")

    threshold = smoothed.mean() - 0.5 * smoothed.std()
    low = smoothed < threshold

    best_len = 0
    run_start: int | None = None
    for i in range(lo_bound, hi_bound):
        if low[i] and run_start is None:
            run_start = i
        elif not low[i] and run_start is not None:
            best_len = max(best_len, i - run_start)
            run_start = None
    if run_start is not None:
        best_len = max(best_len, hi_bound - run_start)

    return (best_len / frame_rate) if best_len > 0 else None


def chroma(
    y: np.ndarray,
    sr: int,
    *,
    n_fft: int = 4096,
    hop_length: int = 2048,
    fmin: float = 55.0,
    fmax: float = 5000.0,
) -> np.ndarray:
    """
    Whole-clip chroma: fold FFT bin power into 12 pitch classes by nearest MIDI note.

    This is linear-frequency chroma, not a full constant-Q transform -- CQT gives sharper
    low-frequency resolution, which matters for transcription but not for the coarse,
    whole-track pitch-class profile a key estimate needs.

    Returns a single 12-vector (index 0 = C, matching key.py's NOTES ordering), summed
    across every frame in the clip -- not a frame-by-frame chromagram.
    """
    mag = magnitude_spectrogram(y, n_fft=n_fft, hop_length=hop_length)
    if mag.shape[0] == 0:
        return np.zeros(12, dtype=np.float64)

    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    band = (freqs >= fmin) & (freqs <= fmax)
    if not np.any(band):
        return np.zeros(12, dtype=np.float64)
    band_freqs = freqs[band]
    energy = np.sum(mag[:, band] ** 2, axis=0)  # power per bin, summed over all frames

    # MIDI note number from frequency (A4 = MIDI 69 = 440 Hz). MIDI 60 (C4) mod 12 == 0,
    # which is exactly NOTES[0] == "C" in key.py -- no offset needed.
    midi = 69.0 + 12.0 * np.log2(band_freqs / 440.0)
    pitch_class = np.mod(np.round(midi).astype(np.int64), 12)

    vec = np.zeros(12, dtype=np.float64)
    np.add.at(vec, pitch_class, energy)
    return vec
