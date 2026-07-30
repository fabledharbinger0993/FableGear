"""
fablegear / audio_processor.py

Analyses and normalises audio files in-place. No database interaction.

Operations per file (each independently skippable):
1. BPM detection via essentia RhythmExtractor2013 when available (much more
   accurate; falls back to librosa beat tracking), written to TBPM tag
2. Key detection via librosa chroma + Krumhansl-Schmuckler, written to TKEY (Camelot)
3. Loudness + true-peak check via ffmpeg's loudnorm filter (EBU R128 measurement)
4. Normalisation via ffmpeg volume filter if outside tolerance, gain capped so
   true peak can't cross TRUE_PEAK_CEILING_DBTP, in-place replacement

Design rules:
- Existing tags are NEVER overwritten unless force=True (both) or the per-effect
  force_bpm/force_key are passed
- Original files are never deleted until the replacement is verified
- All failures are logged and returned in ProcessResult; nothing crashes the batch
- MP3s are re-encoded at 320kbps CBR if normalisation is applied
- AIFFs are re-encoded losslessly (pcm_s16le or pcm_s24le, matching source bit depth)

Target loudness: -8.0 LUFS (DJ standard)
Tolerance: 0.5 LUFS (skip normalisation if within this window)
"""


import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import librosa
import numpy as np
import soundfile as sf
from mutagen import File as MutagenFile
from mutagen.id3 import TBPM, TKEY

from config import (
    AUDIO_EXTENSIONS, BPM_MAX, BPM_MIN, LUFS_TOLERANCE, TARGET_LUFS,
    TRUE_PEAK_CEILING_DBTP,
)
from health_acoustid import collect_health

log = logging.getLogger(__name__)


# Resolve ffmpeg once at import time — on macOS with Homebrew the server process
# may not inherit the shell PATH, so we fall back to common install locations.
def _find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(candidate).exists():
            return candidate
    return "ffmpeg"  # last resort — will surface a clear FileNotFoundError if absent

_FFMPEG = _find_ffmpeg()

ANALYSIS_DURATION: float = 90.0
LIBROSA_TO_CAMELOT: dict[str, str] = {
    "Amin": "8A", "Emin": "9A", "Bmin": "10A", "F#min": "11A", "C#min": "12A",
    "G#min": "1A", "D#min": "2A", "A#min": "3A", "Fmin": "4A", "Cmin": "5A",
    "Gmin": "6A", "Dmin": "7A",
    "Cmaj": "8B", "Gmaj": "9B", "Dmaj": "10B", "Amaj": "11B", "Emaj": "12B",
    "Bmaj": "1B", "F#maj": "2B", "C#maj": "3B", "G#maj": "4B", "D#maj": "5B",
    "A#maj": "6B", "Fmaj": "7B",
}

KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# ─── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class ProcessResult:
    path: Path
    bpm_detected: float | None = None
    bpm_written: bool = False
    # Beat-tracker agreement, 0-~5 (essentia only; None on the librosa path).
    # Low values flag grids worth eyeballing before a gig — it catches some but
    # not all errors, notably not half-time reads on genuinely fast tracks.
    bpm_confidence: float | None = None
    bpm_source: str = ""             # "essentia" | "librosa" | "" (not run)
    key_detected: str | None = None
    key_written: bool = False
    loudness_before: float | None = None
    loudness_after: float | None = None
    normalised: bool = False
    skipped_bpm: bool = False
    skipped_key: bool = False
    skipped_loudness: bool = False
    enrich_written: bool = False
    mb_recording_id: str | None = None
    errors: list[str] = field(default_factory=list)
    quarantined: bool = False        # True if file was moved to the quarantine folder
    quarantine_dest: Path | None = None  # Where it was moved to

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


# Error substrings that indicate a file is unreadable/corrupt at the binary level.
# These are distinct from soft failures (tag write failed, no tags found, etc.)
# that don't mean the audio data is broken.
_CORRUPT_ERRORS: tuple[str, ...] = (
    "mutagen could not open file",
    "mutagen open failed",
    "Header size < 8",
    "No 'fmt' chunk found",
    "can't sync to MPEG frame",
    "unrecognized format",
    "could not read tags",
)


def is_corrupt(result: ProcessResult) -> bool:
    """Return True if this result represents a file that cannot be read at all."""
    return any(
        any(sig in err for sig in _CORRUPT_ERRORS)
        for err in result.errors
    )


def quarantine_file(result: ProcessResult, quarantine_dir: Path) -> bool:
    """
    Move result.path into quarantine_dir, preserving the filename.
    If a file with the same name already exists there, append a counter suffix.

    Returns True if the move succeeded; updates result.quarantined and
    result.quarantine_dest in place.
    """
    src = result.path
    if not src.exists():
        return False

    quarantine_dir.mkdir(parents=True, exist_ok=True)

    dest = quarantine_dir / src.name
    # Avoid silently overwriting a different file with the same name
    if dest.exists():
        stem, suffix = src.stem, src.suffix
        for n in range(1, 10_000):
            candidate = quarantine_dir / f"{stem}_{n}{suffix}"
            if not candidate.exists():
                dest = candidate
                break

    try:
        src.rename(dest)
        result.quarantined = True
        result.quarantine_dest = dest
        log.info("Quarantined %s → %s", src.name, dest)
        return True
    except OSError as exc:
        log.warning("Could not quarantine %s: %s", src.name, exc)
        return False


# ─── BPM detection ────────────────────────────────────────────────────────────

_ANALYSIS_SR: int = 22050  # sample rate used for BPM/key analysis

# Tri-state cache for the optional essentia import: None = not yet probed.
_ESSENTIA_OK: "bool | None" = None


def _load_audio_ffmpeg(path: Path, duration: float = ANALYSIS_DURATION) -> "tuple[np.ndarray, int] | None":
    """
    Decode audio to mono float32 PCM via ffmpeg subprocess.

    Bypasses audioread / macOS Core Audio entirely — librosa.load() falls back
    to audioread for MP3s which can segfault via AudioToolbox on certain files.
    ffmpeg runs isolated; any crash or format error surfaces as a return of None.
    """
    cmd = [
        _FFMPEG, "-hide_banner", "-y",
        "-t", str(duration), "-i", str(path),
        "-ac", "1", "-ar", str(_ANALYSIS_SR), "-f", "f32le", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            log.debug("ffmpeg decode failed for %s: %s", path.name, result.stderr[-200:])
            return None
        y = np.frombuffer(result.stdout, dtype=np.float32).copy()
        return (y, _ANALYSIS_SR) if y.size > 0 else None
    except Exception as exc:
        log.debug("ffmpeg audio decode error for %s: %s", path.name, exc)
        return None


def _fold_octave(bpm: float, lo: float, hi: float) -> float:
    """Fold a tempo into [lo, hi) by doubling/halving. Corrects librosa's common
    half/double-time octave errors. Only octave (2x) errors are fixable this way;
    non-octave errors (e.g. 4:3 detections) are left as-is.

    Only reachable on the librosa fallback below: when essentia is available it
    resolves the octave from the signal itself and this heuristic is bypassed.
    A fixed fold range also can't represent a genuinely slow or fast track,
    which is part of why essentia is preferred."""
    if bpm <= 0:
        return bpm
    b = bpm
    for _ in range(6):
        if b < lo:
            b *= 2
        elif b >= hi:
            b /= 2
        else:
            break
    return b if lo <= b < hi else bpm  # give up rather than force a bad value


def _essentia_available() -> bool:
    """Whether essentia can be imported. Cached; essentia is an OPTIONAL
    dependency and every call site falls back to the librosa path without it,
    so a missing essentia degrades accuracy but never breaks processing."""
    global _ESSENTIA_OK
    if _ESSENTIA_OK is None:
        try:
            import essentia.standard  # noqa: F401,PLC0415
            _ESSENTIA_OK = True
        except Exception as exc:
            log.info(
                "essentia not available (%s) — falling back to librosa beat "
                "tracking. Install essentia for materially better beat grids.",
                type(exc).__name__,
            )
            _ESSENTIA_OK = False
    return _ESSENTIA_OK


def _detect_bpm_essentia(path: Path) -> "tuple[float, float] | None":
    """(bpm, confidence) from essentia's RhythmExtractor2013 multifeature,
    over the whole track at 44.1 kHz.

    Measured against 12,687 Rekordbox ground-truth beat grids (random 300-track
    sample of a real library), against the librosa path below:

        exact (within 0.6 BPM)   13.4%  ->  91.4%
        within 1%                36.8%  ->  94.8%
        MIREX (within 4%)        90.7%  ->  98.3%

    The exact column is the one that matters. A 4%-tolerant tempo drifts a full
    beat inside ~25 bars, so MIREX-style accuracy is far too loose to decide
    whether a grid is safe to put on a CDJ — which is exactly what FableGear
    now does, since a OneLibrary export carries our grids to the player.

    Returns None on decode or extraction failure so the caller falls back.
    """
    try:
        import essentia.standard as es  # noqa: PLC0415
        audio = es.MonoLoader(filename=str(path), sampleRate=44100)()
        bpm, _beats, conf, _, _ = es.RhythmExtractor2013(method="multifeature")(audio)
        bpm = float(bpm)
        if not (BPM_MIN <= bpm <= BPM_MAX):
            log.warning("essentia BPM %s out of range (%s–%s) for %s",
                        bpm, BPM_MIN, BPM_MAX, path.name)
            return None
        return round(bpm, 2), round(float(conf), 2)
    except Exception as e:
        log.warning("essentia beat tracking failed for %s (%s) — falling back to librosa",
                    path.name, e)
        return None


def _detect_bpm(y: np.ndarray, sr: int, name: str,
                fix_octaves: bool = False,
                fold_min: float = 76.0, fold_max: float = 152.0) -> float | None:
    try:
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(np.squeeze(tempo))
        if fix_octaves:
            folded = _fold_octave(bpm, fold_min, fold_max)
            if folded != bpm:
                log.info("BPM octave-corrected %.2f → %.2f for %s", bpm, folded, name)
                bpm = round(folded, 2)
        if BPM_MIN <= bpm <= BPM_MAX:
            return round(bpm, 2)
        log.warning("BPM %s out of range (%s–%s) for %s", bpm, BPM_MIN, BPM_MAX, name)
        return None
    except Exception as e:
        log.error("BPM detection failed for %s: %s", name, e)
        return None


# ─── Key detection ────────────────────────────────────────────────────────────

def _detect_key(y: np.ndarray, sr: int, name: str) -> str | None:
    try:
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
        scores: dict[str, float] = {}
        for i, note in enumerate(NOTES):
            rolled = np.roll(chroma, -i)
            scores[note + "maj"] = float(np.corrcoef(rolled, KS_MAJOR)[0, 1])
            scores[note + "min"] = float(np.corrcoef(rolled, KS_MINOR)[0, 1])
        best = max(scores, key=scores.get)  # type: ignore[arg-type]
        camelot = LIBROSA_TO_CAMELOT.get(best)
        if camelot is None:
            log.warning("No Camelot mapping for detected key %r", best)
            return None
        log.debug("Key detected: %s → %s  (score %.3f)", best, camelot, scores[best])
        return camelot
    except Exception as e:
        log.error("Key detection failed for %s: %s", name, e)
        return None


# ─── Loudness measurement ─────────────────────────────────────────────────────

def _measure_lufs(path: Path) -> tuple[float, float | None] | None:
    """
    Measure integrated loudness and true peak via ffmpeg's loudnorm filter
    (EBU R128), in one subprocess pass. loudnorm's analysis already computes
    true peak (`input_tp`) alongside integrated loudness at no extra cost —
    reading it here is what lets the caller cap normalisation gain instead of
    silently clipping. Uses a subprocess so memory use is bounded regardless
    of file size, and avoids the scipy circular-import problem on Python 3.12+.

    Returns (lufs, true_peak_dbtp), where true_peak_dbtp is None if loudnorm's
    JSON didn't include a usable value (older ffmpeg builds, edge-case input).
    """
    try:
        cmd = [
            _FFMPEG, "-hide_banner",
            "-i", str(path),
            "-af", "loudnorm=print_format=json",
            "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        # loudnorm prints its JSON summary to stderr after the last '{'
        idx = result.stderr.rfind("{")
        if idx == -1:
            log.warning("No loudnorm JSON in ffmpeg output for %s", path.name)
            return None
        end = result.stderr.rfind("}")
        if end == -1 or end < idx:
            return None
        data = json.loads(result.stderr[idx : end + 1])
        lufs = float(data["input_i"])
        if not np.isfinite(lufs):
            log.warning("Non-finite LUFS for %s (silent file?)", path.name)
            return None
        true_peak = None
        try:
            tp = float(data["input_tp"])
            if np.isfinite(tp):
                true_peak = round(tp, 2)
        except (KeyError, TypeError, ValueError):
            pass
        return round(lufs, 2), true_peak
    except Exception as e:
        log.error("Loudness measurement failed for %s: %s", path.name, e)
        return None


# Below this, applying gain isn't worth a full re-encode: the change is
# inaudible, and on a lossy format the rewrite costs a generation of quality.
_MIN_GAIN_DB: float = 0.1


def _capped_gain_db(
    lufs: float,
    true_peak: float | None,
    target: float = TARGET_LUFS,
    ceiling: float = TRUE_PEAK_CEILING_DBTP,
) -> float:
    """
    dB of gain to reach *target* LUFS, capped so the true peak can never cross
    *ceiling*. Falls back to the uncapped gain when true_peak is unknown,
    rather than blocking normalisation outright.

    A requested boost is clamped to [0, want]: it may be reduced to whatever
    headroom exists, but it must never invert into an attenuation. Most
    commercial masters already peak above a -1.0 dBTP ceiling, so an
    unclamped `min(want, ceiling - true_peak)` would go negative and quietly
    turn "make this louder" into "make this quieter, and re-encode it" —
    lossy generation loss for no benefit. When there is no headroom the
    honest answer is 0.0 (nothing safe to do), and the caller skips the
    rewrite entirely.

    An attenuation (want < 0) passes through unchanged — lowering level can't
    push a peak up.
    """
    want = target - lufs
    if true_peak is None or want <= 0:
        return want
    return max(0.0, min(want, ceiling - true_peak))


# ─── Normalisation ────────────────────────────────────────────────────────────

def _get_ffmpeg_codec_args(path: Path) -> list[str]:
    """Return ffmpeg codec args matching the file format and source bit depth."""
    ext = path.suffix.lower()
    if ext == ".mp3":
        return ["-codec:a", "libmp3lame", "-b:a", "320k"]
    elif ext in (".aiff", ".aif"):
        try:
            info = sf.info(str(path))
            codec = "pcm_s24le" if "24" in info.subtype else "pcm_s16le"
        except Exception as e:
            log.warning("sf.info failed for %s, falling back to pcm_s16le. Err: %s", path.name, e)
            codec = "pcm_s16le"
        return ["-codec:a", codec]
    elif ext == ".wav":
        return ["-codec:a", "pcm_s16le"]
    elif ext == ".flac":
        return ["-codec:a", "flac", "-compression_level", "8"]
    else:
        return ["-codec:a", "copy"]


# Rough output/source size ratios by target format, used to size the disk
# preflight check realistically instead of always assuming worst-case (source
# size again). mp3 in particular is drastically smaller than any lossless
# source, so sizing its margin off the source file wrongly skips conversions
# that would easily fit. flac compresses moderately; wav/aiff are ~1:1 with
# a lossless source (or larger, if going from a lower bit depth up).
_CONVERT_SIZE_RATIO = {"mp3": 0.35, "flac": 0.75, "wav": 1.3, "aiff": 1.3}


def _has_room_for(path: Path, *, multiplier: float = 1.5) -> tuple[bool, int, int]:
    """
    Check whether path.parent's filesystem has enough free space to hold a
    working copy of *path* before we start writing one.

    Every in-place transform here (normalise, convert) already keeps overhead
    to a single file at a time: it writes one temp file, then swaps it in with
    shutil.move() — a same-filesystem rename, not a copy, so the original's
    space is reclaimed the instant .bak is deleted. It never duplicates the
    whole library. But a batch job still needs *at least one file's worth* of
    real headroom to start that swap, and on a full disk retrying that per
    file just produces a wall of ffmpeg ENOSPC errors. Check up front instead.

    multiplier=1.5 is a safety margin: a re-encode (e.g. AIFF→WAV, or a
    normalise pass) isn't guaranteed to be smaller than the source.

    Returns (has_room, free_bytes, needed_bytes).
    """
    try:
        free_bytes = shutil.disk_usage(path.parent).free
    except OSError:
        return True, 0, 0  # can't check — don't block on a stat failure
    needed_bytes = int(path.stat().st_size * multiplier)
    return free_bytes >= needed_bytes, free_bytes, needed_bytes


def _normalise_file(path: Path, gain_db: float) -> bool:
    """
    Apply gain_db to path using ffmpeg volume filter.
    Write → verify → move original to .bak → move temp to path → delete .bak.
    Restores from .bak if the final move fails. Logs CRITICAL if restore fails.
    """
    has_room, free_bytes, needed_bytes = _has_room_for(path)
    if not has_room:
        log.error(
            "Skipping %s — not enough free space on %s (%.1f MB free, need ~%.1f MB)",
            path.name, path.parent, free_bytes / 1_048_576, needed_bytes / 1_048_576,
        )
        return False

    suffix = path.suffix
    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix=suffix, dir=path.parent)
    tmp_path = Path(tmp_path_str)
    os.close(tmp_fd)

    bak = path.with_suffix(path.suffix + ".bak")
    original_moved = False

    try:
        codec_args = _get_ffmpeg_codec_args(path)
        cmd = [
            _FFMPEG, "-y", "-i", str(path),
            "-af", f"volume={gain_db:.4f}dB",
            *codec_args,
            "-map_metadata", "0",
            "-id3v2_version", "3",
            str(tmp_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            log.error("ffmpeg failed for %s:\n%s", path.name, result.stderr[-500:])
            return False

        # Verify output. soundfile can't open MP3s, so use mutagen for those.
        try:
            if tmp_path.suffix.lower() == ".mp3":
                mf = MutagenFile(str(tmp_path))
                if mf is None or mf.info.length == 0:
                    raise ValueError("empty or unreadable MP3")
            else:
                verify_info = sf.info(str(tmp_path))
                if verify_info.frames == 0:
                    raise ValueError("zero frames in output")
        except Exception as verify_err:
            log.error("Could not verify ffmpeg output for %s: %s", path.name, verify_err)
            return False

        shutil.move(str(path), str(bak))
        original_moved = True
        shutil.move(str(tmp_path), str(path))
        bak.unlink()
        return True

    except Exception as e:
        log.error("Normalisation failed for %s: %s", path.name, e)
        if original_moved and not path.exists() and bak.exists():
            try:
                shutil.move(str(bak), str(path))
                log.warning("Restored original from .bak: %s", path.name)
            except Exception as restore_err:
                log.critical(
                    "RESTORE FAILED for %s — original is at %s: %s",
                    path.name, bak, restore_err,
                )
        return False
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# ─── Format Conversion ────────────────────────────────────────────────────────────

def _convert_file(path: Path, target_format: str) -> tuple[bool, str]:
    """
    Convert path to target format (mp3, wav, aif, flac).
    Write → verify → move original to .bak → move new to path with target ext → delete .bak.
    Returns (success: bool, message: str).
    """
    target_format = target_format.lower().lstrip(".")
    if target_format not in ("mp3", "wav", "aif", "aiff", "flac"):
        return False, f"Unsupported format: {target_format}"

    # Normalize aif → aiff for consistency
    if target_format == "aif":
        target_format = "aiff"

    # Compute target extension. When converting *to* AIFF, normalise .aif → .aiff
    # so we always land on the canonical extension. For every other target format,
    # just use the format name as-is regardless of the input extension.
    if target_format == "aiff":
        target_ext = ".aiff"
    else:
        target_ext = f".{target_format}"

    # Normalise source extension for the skip check so .aif and .aiff both match
    src_ext = path.suffix.lower()
    if src_ext == ".aif":
        src_ext = ".aiff"

    # If already target format, skip
    if src_ext == target_ext.lower():
        return True, f"Already {target_format}"

    new_path = path.with_suffix(target_ext)
    if new_path.exists():
        return False, f"{new_path.name} already exists"

    has_room, free_bytes, needed_bytes = _has_room_for(
        path, multiplier=_CONVERT_SIZE_RATIO.get(target_format, 1.5)
    )
    if not has_room:
        return False, (
            f"Skipped — not enough free space on {path.parent} "
            f"({free_bytes / 1_048_576:.1f} MB free, need ~{needed_bytes / 1_048_576:.1f} MB)"
        )

    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix=target_ext, dir=path.parent)
    tmp_path = Path(tmp_path_str)
    os.close(tmp_fd)

    bak = path.with_suffix(path.suffix + ".bak")
    original_moved = False

    try:
        # Determine codec args for target format
        if target_format == "mp3":
            codec_args = ["-codec:a", "libmp3lame", "-b:a", "320k"]
        elif target_format == "aiff":
            try:
                info = sf.info(str(path))
                codec = "pcm_s24le" if "24" in info.subtype else "pcm_s16le"
            except Exception as e:
                log.warning("sf.info failed for %s, falling back to pcm_s16le. Err: %s", path.name, e)
                codec = "pcm_s16le"
            codec_args = ["-codec:a", codec]
        elif target_format == "wav":
            codec_args = ["-codec:a", "pcm_s16le"]
        elif target_format == "flac":
            codec_args = ["-codec:a", "flac", "-compression_level", "8"]

        cmd = [
            _FFMPEG, "-y", "-i", str(path),
            *codec_args,
            "-map_metadata", "0",
            "-id3v2_version", "3",
            str(tmp_path),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode != 0:
            stderr_str = result.stderr.decode("utf-8", errors="replace")
            log.error("ffmpeg failed for %s (exit %d):\n%s", path.name, result.returncode, stderr_str[-2000:])
            # ffmpeg always prints a size/bitrate/muxing-overhead summary line
            # last, whether the run succeeded or failed — truncating to a raw
            # tail surfaces that boilerplate instead of the actual error, which
            # appears earlier in stderr. Pull the last non-progress line instead.
            diag_lines = [
                ln.strip() for ln in stderr_str.splitlines()
                if ln.strip() and "time=" not in ln
                and not ln.lstrip().startswith(("size=", "frame="))
            ]
            reason = diag_lines[-1] if diag_lines else stderr_str[-200:].strip()
            return False, f"ffmpeg failed: {reason}"

        # Verify output without loading into RAM
        try:
            verify_info = sf.info(str(tmp_path))
        except Exception as verify_err:
            return False, f"Could not verify ffmpeg output: {verify_err}"
        if verify_info.frames == 0:
            return False, "ffmpeg output is empty (zero frames)"

        # Move original to .bak, new to path
        shutil.move(str(path), str(bak))
        original_moved = True
        shutil.move(str(tmp_path), str(new_path))
        # ⚠ PERMANENT OPERATION — once .bak is deleted the pre-conversion file
        # is unrecoverable.  This is an intentional, documented tradeoff: the
        # purpose of conversion is to change the format, and re-encoding from
        # the output would degrade quality further.  The .bak is kept just long
        # enough to guarantee the output is non-empty; if the caller ever needs
        # "true undo" for conversions, it must archive .bak before this line
        # rather than deleting it (see routes_undo.py comment on 'convert').
        bak.unlink()
        return True, f"Converted to {target_format}"

    except Exception as e:
        if original_moved and not path.exists() and bak.exists():
            try:
                shutil.move(str(bak), str(path))
                log.warning("Restored original from .bak: %s", path.name)
                return False, f"Conversion failed (restored original): {e}"
            except Exception as restore_err:
                log.critical("RESTORE FAILED for %s — original is at %s: %s", path.name, bak, restore_err)
                return False, f"Conversion failed AND restore failed: {restore_err}"
        return False, f"Conversion failed: {e}"

    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# ─── AcoustID enrichment ──────────────────────────────────────────────────────

def _enrich_from_acoustid(path: Path, *, force: bool = False) -> dict | None:
    """
    Fingerprint path with fpcalc (via pyacoustid) and query the AcoustID
    web service. Returns a dict with available metadata fields on success,
    or None if the API key is not configured, lookup fails, or score is low.

    Returned dict keys (all optional — only present when non-empty):
      recording_id, title, artist, album, year, genre

    Note: when enrich_tags=True is passed to process_directory(), expect
    ~1s additional time per file due to AcoustID rate limits (3 req/s).
    """
    health = collect_health()
    fpcalc_path = str(health["fpcalc_path"])
    if not bool(health["ok"]) or not fpcalc_path:
        log.debug("AcoustID enrichment skipped (preflight failed): %s", health)
        return None
    previous_fpcalc = os.environ.get("FPCALC")
    try:
        os.environ["FPCALC"] = fpcalc_path
        import acoustid  # noqa: PLC0415
        from config import ACOUSTID_API_KEY  # noqa: PLC0415

        duration, fingerprint = acoustid.fingerprint_file(str(path))
        if not fingerprint:
            return None
        if isinstance(fingerprint, bytes):
            fingerprint = fingerprint.decode("utf-8", errors="replace")
        response = acoustid.lookup(
            ACOUSTID_API_KEY, fingerprint, duration,
            meta=["recordings", "releasegroups", "compress"],
        )
        # Not using acoustid.parse_lookup_result() here: it only yields
        # (score, recording_id, title, artist) and silently drops the
        # releasegroups data we asked for above, so album/release never made
        # it into best_meta even though the docstring promised it. Walk the
        # raw response ourselves to keep the release-group title.
        if response.get("status") != "ok" or "results" not in response:
            log.debug("AcoustID: bad response for %s: %s", path.name, response.get("status"))
            return None
        best_score = 0.0
        best_meta: dict = {}
        for result in response["results"]:
            score = result.get("score", 0.0)
            if score <= best_score or "recordings" not in result:
                continue
            for recording in result["recordings"]:
                artists = recording.get("artists")
                if artists:
                    artist_name = "".join(
                        a["name"] + a.get("joinphrase", "") for a in artists
                    )
                else:
                    artist_name = None
                releasegroups = recording.get("releasegroups") or []
                album_name = releasegroups[0].get("title") if releasegroups else None
                best_score = score
                best_meta = {
                    "recording_id": recording.get("id") or "",
                    "title":        recording.get("title") or "",
                    "artist":       artist_name or "",
                    "album":        album_name or "",
                }
        if best_score < 0.60 or not best_meta:
            log.debug("AcoustID: no confident match for %s (best=%.2f)", path.name, best_score)
            return None
        log.info("AcoustID match: %s → %s - %s (score=%.2f)",
                 path.name, best_meta.get("artist", "?"), best_meta.get("title", "?"), best_score)
        return best_meta
    except Exception as e:
        log.warning("AcoustID enrichment failed for %s: %s", path.name, e)
        return None
    finally:
        if previous_fpcalc is None:
            os.environ.pop("FPCALC", None)
        else:
            os.environ["FPCALC"] = previous_fpcalc


def _write_enriched_tags(path: Path, meta: dict, *, force: bool = False) -> list[str]:
    """
    Write MusicBrainz metadata into file tags.
    Only writes fields that are:
      a) present and non-empty in meta, AND
      b) currently empty in the file (or force=True).
    Returns list of field names written.
    """
    from mutagen import File as MutagenFile  # noqa: PLC0415
    from mutagen.id3 import TIT2, TPE1, TALB  # noqa: PLC0415

    audio = MutagenFile(str(path), easy=False)
    if audio is None:
        return []
    if audio.tags is None:
        try:
            audio.add_tags()
        except Exception as e:
            log.warning("Failed to add tags to %s. Err: %s", path.name, e)
            return []

    tag_type = type(audio.tags).__name__
    is_vorbis = "VCFLACDict" in tag_type or "VComment" in tag_type
    is_mp4    = "MP4Tags" in tag_type or "MP4" in tag_type

    written = []

    def _field_empty(id3_key, vorbis_key, mp4_key=None) -> bool:
        try:
            if is_vorbis:
                v = audio.tags.get(vorbis_key.lower())
                return not (v and str(v[0] if isinstance(v, list) else v).strip())
            elif is_mp4 and mp4_key:
                v = audio.tags.get(mp4_key)
                return not (v and str(v[0] if isinstance(v, list) else v).strip())
            else:
                f = audio.tags.get(id3_key)
                return f is None or not str(f).strip()
        except Exception as e:
            log.warning("Failed to check if field is empty for %s. Err: %s", path.name, e)
            return True

    def _write_field(label, value, id3_cls, id3_key, vorbis_key, mp4_key=None):
        if not value:
            return
        if not force and not _field_empty(id3_key, vorbis_key, mp4_key):
            return
        try:
            if is_vorbis:
                audio.tags[vorbis_key.lower()] = [value]
            elif is_mp4 and mp4_key:
                audio.tags[mp4_key] = [value]
            else:
                audio.tags.delall(id3_key)
                audio.tags[id3_key] = id3_cls(encoding=3, text=[value])
            written.append(label)
        except Exception as e:
            log.debug("Could not write %s tag to %s: %s", label, path.name, e)

    _write_field("title",  meta.get("title"),  TIT2, "TIT2", "title", "©nam")
    _write_field("artist", meta.get("artist"), TPE1, "TPE1", "artist", "©ART")
    _write_field("album",  meta.get("album"),  TALB, "TALB", "album", "©alb")

    if written:
        try:
            audio.save()
        except Exception as e:
            log.warning("Could not save enriched tags for %s: %s", path.name, e)
            return []

    return written


# ─── Tag writing ──────────────────────────────────────────────────────────────

def _write_tags(path: Path, bpm: float | None, key: str | None) -> None:
    """Write BPM and/or key to file tags via mutagen. Raises on failure."""
    audio = MutagenFile(str(path), easy=False)
    if audio is None:
        raise RuntimeError(f"mutagen could not open {path.name}")
    if audio.tags is None:
        try:
            audio.add_tags()
        except Exception as e:
            raise RuntimeError(f"Cannot create tag block for {path.name}: {e}")

    tag_type = type(audio.tags).__name__
    is_vorbis = "VCFLACDict" in tag_type or "VComment" in tag_type
    is_mp4 = "MP4Tags" in tag_type or "MP4" in tag_type

    if is_vorbis:
        if bpm is not None:
            audio.tags["bpm"] = [str(int(round(bpm)))]
        if key is not None:
            audio.tags["initialkey"] = [key]
    elif is_mp4:
        # MP4/M4A uses atom keys — tmpo for BPM (integer list), freeform atom for key
        if bpm is not None:
            audio.tags["tmpo"] = [int(round(bpm))]
        if key is not None:
            from mutagen.mp4 import MP4FreeForm
            audio.tags["----:com.apple.iTunes:initialkey"] = [
                MP4FreeForm(key.encode("utf-8"))
            ]
    else:
        # delall() before setting ensures a clean overwrite regardless of the
        # existing frame's encoding or format (handles WAV + force-overwrite).
        if bpm is not None:
            audio.tags.delall("TBPM")
            audio.tags["TBPM"] = TBPM(encoding=3, text=[str(int(round(bpm)))])
        if key is not None:
            audio.tags.delall("TKEY")
            audio.tags["TKEY"] = TKEY(encoding=3, text=[key])

    audio.save()


# ─── Main entry point ─────────────────────────────────────────────────────────

def process_file(
    path: Path,
    *,
    detect_bpm: bool = True,
    detect_key: bool = True,
    normalise: bool = True,
    force: bool = False,
    force_bpm: bool = False,
    force_key: bool = False,
    force_normalize: bool = False,
    force_enrich: bool = False,
    enrich_tags: bool = False,
    fix_octaves: bool = False,
) -> ProcessResult:
    """Run the full analysis + normalisation pipeline on a single file."""
    result = ProcessResult(path=path)

    if not path.exists():
        result.errors.append("file not found")
        return result
    if path.suffix.lower() not in AUDIO_EXTENSIONS:
        result.errors.append(f"unsupported extension: {path.suffix}")
        return result

    try:
        audio = MutagenFile(str(path), easy=False)
        if audio is None:
            result.errors.append("mutagen could not open file (unsupported format)")
            return result
        # If the file has no tag block yet, create one now so we can write to it.
        if audio.tags is None:
            try:
                audio.add_tags()
                log.info("Created new tag block for tagless file: %s", path.name)
            except Exception as e:
                # Some formats (e.g. WAV) may need special handling — log and continue
                log.warning("Could not add tags to %s (%s: %s) — will attempt write anyway", path.name, type(e).__name__, e)
        tags = audio.tags
    except Exception as e:
        result.errors.append(f"could not read tags: {e}")
        tags = None

    tag_type = type(tags).__name__ if tags else ""
    is_vorbis = "VCFLACDict" in tag_type or "VComment" in tag_type

    def _existing(id3_key: str, vorbis_key: str) -> bool:
        if tags is None:
            return False
        if is_vorbis:
            val = tags.get(vorbis_key.lower())
            # Treat empty string or "0" as absent so force=False still writes
            return bool(val) and str(val[0] if isinstance(val, list) else val).strip() not in ("", "0")
        frame = tags.get(id3_key)
        return frame is not None and str(frame).strip() not in ("", "0")

    # ── Load audio once for BPM + key (shared decode) ──
    # Per-effect force: the global `force` still forces both (back-compat).
    _force_bpm = force or force_bpm
    _force_key = force or force_key
    needs_bpm = detect_bpm and not (_existing("TBPM", "bpm") and not _force_bpm)
    needs_key = detect_key and not (_existing("TKEY", "initialkey") and not _force_key)
    # essentia reads the file itself at full rate, so try it before deciding
    # whether the shared 90 s decode is needed at all. Its result (or failure)
    # is what determines whether the librosa fallback still has to run.
    _es_bpm: float | None = None
    if needs_bpm and _essentia_available():
        got = _detect_bpm_essentia(path)
        if got is not None:
            _es_bpm, result.bpm_confidence = got
            result.bpm_source = "essentia"

    # Key always needs the decode; BPM needs it only as the librosa fallback,
    # i.e. when essentia is absent or came back empty. A BPM-only run where
    # essentia succeeded skips the decode entirely.
    _audio: "tuple[np.ndarray, int] | None" = None
    if needs_key or (needs_bpm and _es_bpm is None):
        _audio = _load_audio_ffmpeg(path)
        if _audio is None:
            result.errors.append("audio decode failed — BPM/key analysis skipped")

    # ── BPM ──
    if detect_bpm:
        if not needs_bpm:
            result.skipped_bpm = True
        else:
            # essentia already ran above (it decides whether _audio was even
            # loaded); librosa stands in when essentia is absent or came back
            # empty, so a per-file essentia failure still yields a BPM.
            # --fix-octaves only reaches the librosa path: essentia resolves
            # the octave from the signal, so folding its answer would be a
            # heuristic overriding a better measurement.
            bpm = _es_bpm
            if bpm is None and _audio is not None:
                bpm = _detect_bpm(*_audio, path.name, fix_octaves=fix_octaves)
                if bpm is not None:
                    result.bpm_source = "librosa"

            result.bpm_detected = bpm
            if bpm is not None:
                try:
                    _write_tags(path, bpm=bpm, key=None)
                    result.bpm_written = True
                    log.info("BPM written: %.1f → %s  (%s%s)", bpm, path.name,
                             result.bpm_source,
                             f", confidence {result.bpm_confidence}"
                             if result.bpm_confidence is not None else "")
                except Exception as e:
                    result.errors.append(f"BPM tag write failed: {e}")

    # ── Key ──
    if detect_key:
        if not needs_key:
            result.skipped_key = True
        elif _audio is not None:
            key = _detect_key(*_audio, path.name)
            result.key_detected = key
            if key is not None:
                try:
                    _write_tags(path, bpm=None, key=key)
                    result.key_written = True
                    log.info("KEY written: %s → %s", key, path.name)
                except Exception as e:
                    result.errors.append(f"KEY tag write failed: {e}")

    # ── Loudness ──
    if normalise:
        measured = _measure_lufs(path)
        lufs, true_peak = measured if measured is not None else (None, None)
        result.loudness_before = lufs
        if lufs is None:
            result.errors.append("loudness measurement failed")
        elif (not force_normalize) and abs(lufs - TARGET_LUFS) <= LUFS_TOLERANCE:
            result.skipped_loudness = True
        else:
            gain_db = _capped_gain_db(lufs, true_peak)
            requested_db = TARGET_LUFS - lufs
            if gain_db != requested_db:
                log.info(
                    "Gain capped %+.1f → %+.1f dB for %s to keep true peak "
                    "under %.1f dBTP (measured %.1f dBTP)",
                    requested_db, gain_db, path.name, TRUE_PEAK_CEILING_DBTP, true_peak,
                )
            if abs(gain_db) < _MIN_GAIN_DB:
                # No headroom to move into. Rewriting the file to apply ~0 dB
                # would re-encode it (lossy generation loss on MP3) for no
                # audible change, so leave it alone and say why.
                result.skipped_loudness = True
                log.info(
                    "Loudness unchanged for %s: at %.1f LUFS it needs %+.1f dB to "
                    "reach %.1f, but true peak is already %.1f dBTP — no headroom "
                    "under the %.1f dBTP ceiling. Needs a limiter to go louder.",
                    path.name, lufs, requested_db, TARGET_LUFS,
                    true_peak if true_peak is not None else float("nan"),
                    TRUE_PEAK_CEILING_DBTP,
                )
            else:
                log.info("Normalising %s: %.1f LUFS → %.1f (gain: %+.1f dB)",
                         path.name, lufs, TARGET_LUFS, gain_db)
                if _normalise_file(path, gain_db):
                    after = _measure_lufs(path)
                    result.loudness_after = after[0] if after is not None else None
                    result.normalised = True
                else:
                    result.errors.append("normalisation failed")

    # ── MusicBrainz enrichment ──
    if enrich_tags:
        enrich_force = force or force_enrich
        meta = _enrich_from_acoustid(path, force=enrich_force)
        if meta:
            written_fields = _write_enriched_tags(path, meta, force=enrich_force)
            if written_fields:
                result.enrich_written = True
                result.mb_recording_id = meta.get("recording_id")
                log.info("Enriched %s: wrote %s", path.name, ", ".join(written_fields))

    return result


# ─── Batch runner ─────────────────────────────────────────────────────────────

def process_directory(
    root: Path,
    *,
    detect_bpm: bool = True,
    detect_key: bool = True,
    normalise: bool = True,
    force: bool = False,
    force_bpm: bool = False,
    force_key: bool = False,
    force_normalize: bool = False,
    force_enrich: bool = False,
    enrich_tags: bool = False,
    fix_octaves: bool = False,
    max_workers: int = 1,
    pause_seconds: float = 0.0,
    quarantine_dir: Path | None = None,
    skip_paths: "set[str] | None" = None,
    on_result: "Callable[[ProcessResult], None] | None" = None,
) -> list[ProcessResult]:
    """
    Process all audio files under root. Returns all ProcessResults.

    Parameters
    ----------
    root : Path
        Directory to scan recursively.
    detect_bpm, detect_key, normalise, force, force_bpm, force_key : bool
        Passed through to process_file().
    max_workers : int
        Number of files to process in parallel. Default 1 (sequential).
        Values > 1 use a ThreadPoolExecutor. Keep at 1 on systems where
        the BPM/key libraries are not thread-safe, or when normalisation
        is enabled (ffmpeg is subprocess-safe but concurrent re-encoding
        is very disk-intensive).
    pause_seconds : float
        Seconds to sleep between files (sequential mode only). Use this to
        keep CPU load below 100% on slower machines or when DJing on the
        same computer. Default 0.0 (no pause).
    quarantine_dir : Path | None
        If provided, any file whose result is corrupt (cannot be opened
        at the binary level) is moved here after processing. Pass the
        FableGear Archive Quarantine path from config or a custom location.
    skip_paths : set[str] | None
        Absolute paths (as str) to exclude from this run entirely — no
        process_file() call, no result. Used by the caller for checkpoint
        resume: files already completed in an interrupted prior run.
    on_result : Callable[[ProcessResult], None] | None
        Invoked once per file immediately after its result is tallied, from
        whichever thread produced it. Used by the caller to persist a
        checkpoint incrementally rather than only after the whole root
        finishes — process_directory() itself has no notion of checkpoints.
    """
    import concurrent.futures
    from scanner import scan_directory

    tracks = list(scan_directory(root))
    if skip_paths:
        tracks = [t for t in tracks if str(t.path) not in skip_paths]
    total = len(tracks)
    results: list[ProcessResult] = []

    if total == 0:
        log.info("No audio files found under %s", root)
        return results

    log.info(
        "Processing %d files — workers=%d pause=%.1fs",
        total, max_workers, pause_seconds,
    )

    # Running counters for live progress ticker
    done = 0
    clean = 0
    errors = 0
    edited = 0
    tags_written = 0
    bpm_key_written = 0
    quarantined = 0
    enriched = 0
    inspected = 0

    def _emit_progress() -> None:
        print(
            "FABLEGEAR_PROGRESS: " + json.dumps({
                "done":          done,
                "total":         total,
                "remaining":     total - done,
                "clean":         clean,
                "errors":        errors,
                "edited":        edited,
                "tags_written":  tags_written,
                "bpm_key_written": bpm_key_written,
                "quarantined":   quarantined,
                "enriched":      enriched,
                "inspected":     inspected,
            }),
            flush=True,
        )

    def _tally(r: ProcessResult) -> None:
        nonlocal done, clean, errors, edited, tags_written, bpm_key_written, quarantined, enriched
        done += 1
        if r.errors:
            errors += 1
        if r.quarantined:
            quarantined += 1
        if r.enrich_written:
            enriched += 1
        any_edit = r.bpm_written or r.key_written or r.normalised
        if any_edit:
            edited += 1
            if r.bpm_written or r.key_written:
                bpm_key_written += 1
            tags_written += 1  # all writes: bpm, key, or normalisation
        elif r.ok:
            clean += 1
        # Quarantined files are gone from their original path — don't index them
        if r.quarantined:
            return
        # Build scan index entry — duration via soundfile header (fast, no decode)
        try:
            duration_sec = round(sf.info(str(r.path)).duration, 1)
        except Exception as e:
            log.warning("Could not read duration for %s. Err: %s", r.path.name, e)
            duration_sec = None
        try:
            file_size = r.path.stat().st_size
        except OSError:
            file_size = 0
        # Read current BPM/key from tags (may have just been written)
        bpm_val = None
        key_val = None
        try:
            audio = MutagenFile(str(r.path), easy=False)
            if audio and audio.tags:
                tbpm = audio.tags.get("TBPM")
                if tbpm:
                    bpm_val = str(tbpm).strip()
                tkey = audio.tags.get("TKEY")
                if tkey:
                    key_val = str(tkey).strip()
        except Exception as exc:
            log.warning("Tag read failed for %s: %s", r.path, exc)
        scan_index.append({
            "path":         str(r.path),
            "bpm":          bpm_val,
            "key":          key_val,
            "duration_sec": duration_sec,
            "file_size":    file_size,
        })

    def _process_one(track, index: int) -> ProcessResult:
        nonlocal inspected
        inspected += 1
        _emit_progress()
        r = process_file(
            track.path,
            detect_bpm=detect_bpm,
            detect_key=detect_key,
            normalise=normalise,
            force=force,
            force_bpm=force_bpm,
            force_key=force_key,
            force_normalize=force_normalize,
            force_enrich=force_enrich,
            enrich_tags=enrich_tags,
            fix_octaves=fix_octaves,
        )
        if r.errors:
            log.info("[%d/%d] %s  ✗ errors: %s",
                     index, total, track.path.name, ", ".join(r.errors))
        else:
            log.info("[%d/%d] %s", index, total, track.path.name)
        # Quarantine corrupt files immediately after processing
        if quarantine_dir and is_corrupt(r):
            quarantine_file(r, quarantine_dir)
            log.warning("QUARANTINED: %s → %s", track.path.name, quarantine_dir)
        return r

    scan_index: list[dict] = []   # accumulates entries for scan_index.json

    if max_workers > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(_process_one, track, i + 1): i
                for i, track in enumerate(tracks)
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    r = future.result()
                    results.append(r)
                    _tally(r)
                    _emit_progress()
                    if on_result is not None:
                        on_result(r)
                except Exception as exc:
                    idx = futures[future]
                    log.error("Unexpected error processing file %d: %s", idx + 1, exc)
                    done += 1
                    errors += 1
                    _emit_progress()
    else:
        for i, track in enumerate(tracks):
            r = _process_one(track, i + 1)
            results.append(r)
            _tally(r)
            _emit_progress()
            if on_result is not None:
                on_result(r)
            if pause_seconds > 0 and i < total - 1:
                time.sleep(pause_seconds)

    # Write scan index for duplicate pre-filter
    # MED-03 FIX: Use atomic write pattern (write-to-temp + rename) to prevent
    # corruption if process crashes mid-write or concurrent access occurs.
    if scan_index:
        index_path = Path.home() / "rekordbox-toolkit" / "scan_index.json"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing: dict[str, dict] = {}
            if index_path.exists():
                with open(index_path, encoding="utf-8") as f:
                    for entry in json.load(f):
                        existing[entry["path"]] = entry
            for entry in scan_index:
                existing[entry["path"]] = entry
            
            # Atomic write: write to temp file then rename
            import tempfile
            temp_fd, temp_path = tempfile.mkstemp(
                dir=index_path.parent,
                prefix=".scan_index_",
                suffix=".json.tmp"
            )
            try:
                with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                    json.dump(list(existing.values()), f, indent=2)
                # Atomic rename (POSIX guarantees atomicity)
                Path(temp_path).replace(index_path)
                log.info("Scan index written: %s (%d entries)", index_path, len(existing))
            except Exception as io_err:
                # Clean up temp file on failure
                Path(temp_path).unlink(missing_ok=True)
                raise io_err
        except Exception as exc:
            msg = f"Could not write scan index: {exc}"
            log.warning(msg)
            # Bubble up the failure so the UI reports the scan index error instead of swallowing it locally
            results.append(ProcessResult(path=index_path, errors=[msg]))

    # Emit structured error summary so the UI can build actionable next steps.
    # Emitted as FABLEGEAR_ERROR_SUMMARY: {json} — parsed by the JS SSE handler.
    errored_results = [r for r in results if r.errors]
    if errored_results:
        def _short_err(r: ProcessResult) -> str:
            return r.errors[0] if r.errors else "unknown error"

        corrupt_list:  list[dict] = []
        decode_list:   list[dict] = []
        tag_list:      list[dict] = []
        other_list:    list[dict] = []

        for r in errored_results:
            entry = {"name": r.path.name, "path": str(r.path), "error": _short_err(r)}
            if r.quarantined:
                corrupt_list.append(entry)
            elif any("audio decode failed" in e for e in r.errors):
                decode_list.append(entry)
            elif any("tag write failed" in e or "normalisation failed" in e for e in r.errors):
                tag_list.append(entry)
            else:
                other_list.append(entry)

        print(
            "FABLEGEAR_ERROR_SUMMARY: " + json.dumps({
                "corrupt":       corrupt_list,
                "decode_failed": decode_list,
                "tag_failed":    tag_list,
                "other":         other_list,
                "quarantine_dir": str(quarantine_dir) if quarantine_dir else None,
            }),
            flush=True,
        )

    return results


# ─── Smoke test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    test_files = [Path(arg) for arg in sys.argv[1:]]
    if not test_files:
        from config import MUSIC_ROOT  # noqa: PLC0415
        test_files = []
        for candidate in MUSIC_ROOT.rglob("*"):
            if candidate.suffix.lower() in AUDIO_EXTENSIONS:
                test_files.append(candidate)
            if len(test_files) >= 2:
                break

    if not test_files:
        print("SKIP (no audio files found; pass one or more paths to test)")
        sys.exit(0)

    for f in test_files:
        if not f.exists():
            print(f"SKIP (not found): {f.name}")
            continue
        print(f"\n{'─'*60}")
        print(f"FILE: {f.name}")
        r = process_file(f, detect_bpm=True, detect_key=True, normalise=False, force=False)
        print(f"  BPM detected : {r.bpm_detected}  written={r.bpm_written}  skipped={r.skipped_bpm}")
        print(f"  KEY detected : {r.key_detected}  written={r.key_written}  skipped={r.skipped_key}")
        print(f"  Errors       : {r.errors or 'none'}")
        print(f"  Status       : {'OK' if r.ok else 'ERRORS'}")
