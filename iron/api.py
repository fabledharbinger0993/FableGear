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

from iron import key as key_detect
from iron import tempo as tempo_detect
from iron.errors import DecodeFailed
from iron.schema import IronResult

_ANALYSIS_SR = 22050
_ANALYSIS_DURATION = 90.0
_BPM_MIN = 30.0
_BPM_MAX = 300.0

_KNOWN_FIELDS = frozenset({"bpm", "initial_key"})


def _find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(candidate).exists():
            return candidate
    return "ffmpeg"  # last resort -- surfaces a clear FileNotFoundError if truly absent


_FFMPEG = _find_ffmpeg()


def _decode(path: Path, duration: float = _ANALYSIS_DURATION) -> tuple[np.ndarray, int]:
    """Decode `path` to mono float64 PCM at _ANALYSIS_SR via an ffmpeg subprocess."""
    cmd = [
        _FFMPEG, "-hide_banner", "-y",
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


def analyze(
    path: Path,
    *,
    want: Iterable[str] = ("bpm", "initial_key"),
    bpm_min: float = _BPM_MIN,
    bpm_max: float = _BPM_MAX,
) -> IronResult:
    """
    Analyze one file and return whatever candidates were found for the fields in `want`.

    Never raises for an ordinary analysis failure (bad decode, no reliable tempo/key found)
    -- those land in `result.errors` so a batch caller doesn't need a try/except per file.
    A programmer error (an unknown field name in `want`) still raises, the same way a typo
    in an argument name should surface immediately rather than silently doing nothing.
    """
    path = Path(path)
    wanted = set(want)
    unknown = wanted - _KNOWN_FIELDS
    if unknown:
        raise ValueError(f"iron.analyze: unknown field(s) {sorted(unknown)!r}")

    result = IronResult()

    try:
        y, sr = _decode(path)
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
