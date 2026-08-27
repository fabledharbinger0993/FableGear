"""
fablegear / iron / api.py

The public surface: analyze().

Read-only. Iron never writes a file and never touches a tag -- it decodes audio to PCM via
ffmpeg (the same subprocess approach `audio_processor.py::_load_audio_ffmpeg` already uses,
which sidesteps `librosa.load()`'s audioread/AudioToolbox segfault risk on some MP3s) and
runs tempo/key detection against the decoded samples.

Because decoding goes through ffmpeg rather than a container-specific tag reader, Iron isn't
limited to the containers Anvil's tag layer supports -- anything ffmpeg can decode, Iron can
analyze. The two packages compose at the field level (`IronResult.to_track_fields()`), not
at the container-support level.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

import numpy as np

from iron import beats as beat_grid_detect
from iron import dsp
from iron import key as key_detect
from iron import tempo as tempo_detect
from iron.errors import DecodeFailed
from iron.schema import IronResult, TempoCheckpoint

_ANALYSIS_SR = 22050
_ANALYSIS_DURATION = 90.0
_BPM_MIN = 60.0  # matches iron.tempo.detect_tempo's own default -- see its docstring for
_BPM_MAX = 180.0  # the real-benchmark justification and the real, deliberate 180+ BPM cost
_HOP_LENGTH = 512  # matches iron.tempo.detect_tempo's own default
_KICK_BAND_FMIN = 40.0  # Hz -- iron.beats.detect_beat_grid's accent_env, a kick drum's
_KICK_BAND_FMAX = 120.0  # fundamental + first harmonic; see iron/dsp.py::band_energy

# Primary analysis targets the track's BODY, not the first 90 seconds from 0:00: roughly a
# third of the way in, through to the last 10% (where a DJ starts prepping the mix-out).
# This avoids an often-sparse/beatless intro, and -- the actual reason it matters for
# tempo accuracy, not just "more representative audio" -- it's long enough to actually
# contain a mid-track breakdown/bridge, which iron.tempo's structural bar-fit pass needs
# and a fixed 0-90s window from the very start essentially never has. Capped at
# _MAX_BODY_SECONDS so a multi-hour DJ mix or live recording doesn't attempt to decode and
# analyze an hour of audio by default -- the same cost concern _STABILITY_WINDOW_SECONDS's
# per-checkpoint window and scripts/ real-library testing already had to account for.
#
# Reverted here from a same-day whole-track-decode experiment (docs/IRON_RESEARCH.md SS9/
# SS10): on a real 500-track benchmark, whole-track decoding measured no clear accuracy
# gain over this windowed approach (58.6% vs 60.7% MIREX on different-but-comparable
# samples -- within noise, not a real difference) while costing roughly 3-5x the per-track
# decode+analysis time. Not a good trade -- see SS10 for the real numbers.
_BODY_START_FRACTION = 1.0 / 3.0
_BODY_END_FRACTION = 0.9
_MAX_BODY_SECONDS = 240.0

_KNOWN_FIELDS = frozenset({"bpm", "initial_key", "downbeat_offset", "time_signature"})

# Beat counts a long-baseline stability check projects forward to, by default. Chosen as
# powers of two (bars, in 4/4, at 32/64/128/256/512 beats = 8/16/32/64/128 bars) rather than
# fixed time offsets deliberately: a beat-count anchor scales with the tempo guess itself, so
# a clean octave/ratio error in that guess shows up as the projected anchor landing at the
# wrong actual time -- a fixed-percentage-of-file window has no such self-correcting property.
_DEFAULT_STABILITY_ANCHORS = (32, 64, 128, 256, 512)
_STABILITY_WINDOW_SECONDS = 20.0
_STABILITY_TOLERANCE = 0.04  # MIREX-style 4%, matching scripts/benchmark_iron_tempo.py


def _find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(candidate).exists():
            return candidate
    return "ffmpeg"  # last resort -- surfaces a clear FileNotFoundError if truly absent


_FFMPEG = _find_ffmpeg()


def _decode(
    path: Path, duration: float = _ANALYSIS_DURATION, *, start: float = 0.0
) -> tuple[np.ndarray, int]:
    """
    Decode `path` to mono float64 PCM at _ANALYSIS_SR via an ffmpeg subprocess.

    `start` seeks before decoding (ffmpeg's input-side -ss, which is fast -- it seeks to
    the nearest keyframe rather than decoding and discarding everything before it), so a
    long-baseline check can read a short window far into a file without paying to decode
    everything in between.
    """
    cmd = [_FFMPEG, "-hide_banner", "-y"]
    if start > 0:
        cmd += ["-ss", str(start)]
    cmd += [
        "-t", str(duration), "-i", str(path),
        "-ac", "1", "-ar", str(_ANALYSIS_SR), "-f", "f32le", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DecodeFailed(f"{path.name}: ffmpeg invocation failed ({exc})") from exc

    if result.returncode != 0:
        detail = result.stderr[-200:].decode("utf-8", "replace")
        raise DecodeFailed(f"{path.name}: ffmpeg decode failed ({detail})")

    y = np.frombuffer(result.stdout, dtype=np.float32).astype(np.float64)
    if y.size == 0:
        raise DecodeFailed(f"{path.name}: ffmpeg produced no audio samples")
    return y, _ANALYSIS_SR


def _pick_body_window(duration: float | None) -> tuple[float, float]:
    """
    Return (start, duration) targeting the track's body -- see _BODY_START_FRACTION's
    comment for why. Falls back to (0, _ANALYSIS_DURATION), the previous fixed
    from-the-start window, when duration can't be determined at all (e.g. ffprobe missing)
    or the file is too short for a body/edge split to mean anything.
    """
    if duration is None or duration <= 0:
        return 0.0, _ANALYSIS_DURATION
    start = duration * _BODY_START_FRACTION
    end = duration * _BODY_END_FRACTION
    span = end - start
    if span <= 1.0:
        return 0.0, min(duration, _ANALYSIS_DURATION)
    return start, min(span, _MAX_BODY_SECONDS)


def _probe_duration(path: Path) -> float | None:
    """Total duration in seconds via ffprobe, or None if it can't be determined."""
    ffprobe = _FFMPEG.replace("ffmpeg", "ffprobe") if "ffmpeg" in _FFMPEG else "ffprobe"
    cmd = [ffprobe, "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30, text=True)
        return float(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def _check_stability(
    path: Path,
    bpm0: float,
    *,
    anchors: Iterable[int],
    window: float,
    bpm_min: float,
    bpm_max: float,
) -> tuple[bool | None, list[TempoCheckpoint]]:
    """
    Confirm `bpm0` holds at long-baseline projected checkpoints, not just in the initial
    analysis window.

    For each beat count in `anchors`, project where that beat should fall if `bpm0` is
    right (`beat * 60 / bpm0`), decode a short independent window there, and re-run tempo
    detection from scratch. Anchors past the track's actual duration are skipped (recorded
    with measured_bpm=None, agrees=None) rather than silently omitted, so a caller can tell
    "not reachable" apart from "reachable and disagreed".
    """
    duration = _probe_duration(path)
    checkpoints: list[TempoCheckpoint] = []
    any_checked = False
    all_agree = True

    for beat in anchors:
        anchor_time = beat * 60.0 / bpm0
        if duration is None or anchor_time + window > duration:
            checkpoints.append(TempoCheckpoint(beat, anchor_time, None, None))
            continue

        try:
            y, sr = _decode(path, window, start=anchor_time)
            outcome = tempo_detect.detect_tempo(y, sr, bpm_min=bpm_min, bpm_max=bpm_max)
        except Exception:  # a bad checkpoint must not take down the whole stability check
            checkpoints.append(TempoCheckpoint(beat, anchor_time, None, None))
            continue

        if outcome is None:
            checkpoints.append(TempoCheckpoint(beat, anchor_time, None, None))
            continue

        measured_bpm, _conf = outcome
        agrees = bool(abs(measured_bpm - bpm0) <= _STABILITY_TOLERANCE * bpm0)
        checkpoints.append(TempoCheckpoint(beat, anchor_time, measured_bpm, agrees))
        any_checked = True
        if not agrees:
            all_agree = False

    return (all_agree if any_checked else None), checkpoints


def analyze(
    path: Path,
    *,
    want: Iterable[str] = ("bpm", "initial_key"),
    bpm_min: float = _BPM_MIN,
    bpm_max: float = _BPM_MAX,
    verify_stability: bool = False,
    stability_anchors: Iterable[int] = _DEFAULT_STABILITY_ANCHORS,
    stability_window: float = _STABILITY_WINDOW_SECONDS,
) -> IronResult:
    """
    Analyze one file and return whatever candidates were found for the fields in `want`.

    Decodes the track's BODY, not a fixed window from 0:00 -- see _BODY_START_FRACTION.
    Falls back to the first _ANALYSIS_DURATION seconds when the file's duration can't be
    determined or is too short for a body/edge split to mean anything. (A same-day
    whole-track-decode experiment was tried and reverted -- see docs/IRON_RESEARCH.md SS10:
    no measurable accuracy gain over this windowed approach on a real 500-track benchmark,
    at roughly 3-5x the per-track decode+analysis cost.)

    Never raises for an ordinary analysis failure (bad decode, no reliable tempo/key found)
    -- those land in `result.errors` so a batch caller doesn't need a try/except per file.
    A programmer error (an unknown field name in `want`) still raises, the same way a typo
    in an argument name should surface immediately rather than silently doing nothing.

    `verify_stability=True` additionally projects the found BPM forward to where beats
    32/64/128/256/512 should fall (skipping anchors past the file's own duration) and
    re-derives the tempo fresh at each one, populating `result.bpm_stable` and
    `result.checkpoints`. Off by default: it costs one extra decode+analyze per reachable
    anchor, worth paying only when a caller actually wants to distinguish "one confident
    number" from "this file may have more than one tempo section" -- a DJ mix, a live
    recording, or a track with a real tempo change partway through, none of which a single
    90-second window can tell apart from an ordinary track on its own. `stability_window`
    is how much audio each checkpoint decodes (default 20s); shrink it for short test
    fixtures where an anchor near the end of the file would otherwise be treated as
    unreachable simply because the window wouldn't fit.

    `want=(..., "downbeat_offset")` and/or `want=(..., "time_signature")` additionally run
    a beat-grid pass (`iron.beats.detect_beat_grid`) once `bpm` is found, populating
    `result.downbeat_offset`, `result.time_signature`, and `result.beat_grid_confidence`
    together -- both fields come from the same pass, so requesting either one populates
    both. Off by default: it's one extra dynamic-programming pass over the analyzed
    window, a real (if bounded) added cost worth paying only when a caller actually wants
    a beat-grid anchor for CDJ export, not just a BPM number.
    """
    path = Path(path)
    wanted = set(want)
    unknown = wanted - _KNOWN_FIELDS
    if unknown:
        raise ValueError(f"iron.analyze: unknown field(s) {sorted(unknown)!r}")

    result = IronResult()

    duration = _probe_duration(path)
    start, window_duration = _pick_body_window(duration)
    try:
        y, sr = _decode(path, window_duration, start=start)
    except DecodeFailed as exc:
        result.errors.append(str(exc))
        return result

    if "bpm" in wanted:
        try:
            outcome = tempo_detect.detect_tempo(y, sr, bpm_min=bpm_min, bpm_max=bpm_max)
        except Exception as exc:  # a detector bug must not take down a batch run
            result.errors.append(f"tempo detection failed: {exc}")
        else:
            if outcome is not None:
                result.bpm, result.bpm_confidence = outcome
                if verify_stability:
                    result.bpm_stable, result.checkpoints = _check_stability(
                        path, result.bpm,
                        anchors=stability_anchors, window=stability_window,
                        bpm_min=bpm_min, bpm_max=bpm_max,
                    )

    wants_beat_grid = "downbeat_offset" in wanted or "time_signature" in wanted
    if wants_beat_grid and result.bpm is not None:
        try:
            onset_env = dsp.onset_envelope(y, sr, hop_length=_HOP_LENGTH)
            accent_env = dsp.band_energy(
                y, sr, fmin=_KICK_BAND_FMIN, fmax=_KICK_BAND_FMAX, hop_length=_HOP_LENGTH
            )
            outcome = beat_grid_detect.detect_beat_grid(
                onset_env, sr / _HOP_LENGTH, result.bpm,
                window_start_s=start, accent_env=accent_env,
            )
        except Exception as exc:  # a detector bug must not take down a batch run
            result.errors.append(f"beat-grid detection failed: {exc}")
        else:
            if outcome is not None:
                result.downbeat_offset, result.time_signature, result.beat_grid_confidence = outcome

    if "initial_key" in wanted:
        try:
            outcome = key_detect.detect_key(y, sr)
        except Exception as exc:
            result.errors.append(f"key detection failed: {exc}")
        else:
            if outcome is not None:
                result.initial_key, result.key_confidence = outcome

    return result


__all__ = ["analyze"]
