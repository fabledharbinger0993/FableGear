"""
fablegear / quench / api.py

The public surface: measure(), plan_gain(), apply_gain(), level().

Quench drives ffmpeg and nothing else. Measurement is ffmpeg's loudnorm filter (EBU R128)
in one subprocess pass, which yields integrated loudness and true peak together. Levelling
is ffmpeg's volume filter with the recipes in quench/codecs.py, written to a temp file,
verified with ffprobe, then swapped into place with a .bak safety net.

Quench never touches a tag directly (-map_metadata 0 carries them across the rewrite) and
never listens for tempo or key: those are Anvil's and Iron's jobs.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from quench.codecs import NORMALISABLE_SUFFIXES, codec_args, has_room_for
from quench.errors import MeasureFailed, NotEnoughSpace, RewriteFailed, UnsupportedFormat
from quench.schema import GainPlan, Loudness, QuenchResult

log = logging.getLogger(__name__)

# The default true-peak ceiling. config.TRUE_PEAK_CEILING_DBTP is the app's setting; this is
# the package default so Quench stands on its own in a script or a test.
DEFAULT_TRUE_PEAK_CEILING_DBTP: float = -1.0

_MEASURE_TIMEOUT_S = 120
_REWRITE_TIMEOUT_S = 600


# Resolve ffmpeg once at import time -- on macOS with Homebrew the server process may not
# inherit the shell PATH, so fall back to the common install locations (same as iron/api.py).
def _find_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for candidate in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if Path(candidate).exists():
            return candidate
    return name  # last resort -- surfaces a clear FileNotFoundError if truly absent


_FFMPEG = _find_tool("ffmpeg")
_FFPROBE = _find_tool("ffprobe")


# ─── Measurement ──────────────────────────────────────────────────────────────

def measure(path: Path, *, start: float | None = None, duration: float | None = None) -> Loudness:
    """
    Integrated loudness (LUFS) and true peak (dBTP) of *path*, or of the window
    [start, start+duration) when given.

    One ffmpeg subprocess pass, so memory use is bounded regardless of file size. Raises
    MeasureFailed when loudnorm reports nothing usable (corrupt stream, digital silence).
    """
    cmd = [_FFMPEG, "-hide_banner"]
    if start is not None:
        cmd += ["-ss", str(max(0.0, float(start)))]
    if duration is not None:
        cmd += ["-t", str(float(duration))]
    cmd += ["-i", str(path), "-af", "loudnorm=print_format=json", "-f", "null", "-"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=_MEASURE_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        raise MeasureFailed(f"ffmpeg loudnorm could not run for {path.name}: {e}") from e

    # loudnorm prints its JSON summary to stderr after the last '{'
    idx = result.stderr.rfind("{")
    end = result.stderr.rfind("}")
    if idx == -1 or end == -1 or end < idx:
        raise MeasureFailed(f"no loudnorm JSON in ffmpeg output for {path.name}")
    try:
        data = json.loads(result.stderr[idx: end + 1])
        lufs = float(data["input_i"])
    except (ValueError, KeyError, TypeError) as e:
        raise MeasureFailed(f"unreadable loudnorm JSON for {path.name}: {e}") from e
    if not math.isfinite(lufs):
        raise MeasureFailed(f"non-finite LUFS for {path.name} (silent file?)")

    true_peak: float | None = None
    try:
        tp = float(data["input_tp"])
        if math.isfinite(tp):
            true_peak = round(tp, 2)
    except (KeyError, TypeError, ValueError):
        pass
    return Loudness(integrated_lufs=round(lufs, 2), true_peak_dbtp=true_peak)


# ─── Gain planning ────────────────────────────────────────────────────────────

def capped_gain_db(
    lufs: float,
    true_peak: float | None,
    target: float,
    ceiling: float = DEFAULT_TRUE_PEAK_CEILING_DBTP,
) -> float:
    """
    dB of gain to reach *target* LUFS, capped so the true peak can never cross *ceiling*.
    Falls back to the uncapped gain when true_peak is unknown, rather than blocking
    levelling outright.

    A requested boost is clamped to [0, want]: it may be reduced to whatever headroom
    exists, but it must never invert into an attenuation. Most commercial masters already
    peak above a -1.0 dBTP ceiling, so an unclamped `min(want, ceiling - true_peak)` would
    go negative and quietly turn "make this louder" into "make this quieter, and re-encode
    it" -- lossy generation loss for no benefit. When there is no headroom the honest answer
    is 0.0 (nothing safe to do), and the caller skips the rewrite entirely.

    An attenuation (want < 0) passes through unchanged -- lowering level can't push a peak up.
    """
    want = target - lufs
    if true_peak is None or want <= 0:
        return want
    return max(0.0, min(want, ceiling - true_peak))


def plan_gain(before: Loudness, target: float, ceiling: float = DEFAULT_TRUE_PEAK_CEILING_DBTP) -> GainPlan:
    requested = target - before.integrated_lufs
    applied = capped_gain_db(before.integrated_lufs, before.true_peak_dbtp, target, ceiling)
    return GainPlan(requested_db=requested, applied_db=applied, capped=(applied != requested))


# ─── Rewrite ──────────────────────────────────────────────────────────────────

def _verify_output(tmp_path: Path) -> None:
    """ffprobe must see a positive duration, or the rewrite is treated as failed."""
    try:
        r = subprocess.run(
            [_FFPROBE, "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(tmp_path)],
            capture_output=True, text=True, timeout=30,
        )
        seconds = float(r.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        raise RewriteFailed(f"could not verify ffmpeg output for {tmp_path.name}: {e}") from e
    if not seconds > 0:
        raise RewriteFailed(f"ffmpeg output for {tmp_path.name} has no audio (duration {seconds})")


def apply_gain(path: Path, gain_db: float) -> None:
    """
    Apply *gain_db* to *path* in place, keeping container, bit depth, tags and cover art.

    Write -> verify -> move original to .bak -> move temp to path -> delete .bak. Restores
    from .bak if the final move fails, and logs CRITICAL if even that restore fails (the
    original is then still on disk as the .bak). Raises UnsupportedFormat, NotEnoughSpace or
    RewriteFailed; never returns a half-replaced file.
    """
    args = codec_args(path)
    if args is None:
        raise UnsupportedFormat(f"no safe loudness rewrite for {path.suffix} files: {path.name}")

    has_room, free_bytes, needed_bytes = has_room_for(path)
    if not has_room:
        raise NotEnoughSpace(
            f"not enough free space on {path.parent} for {path.name} "
            f"({free_bytes / 1_048_576:.1f} MB free, need ~{needed_bytes / 1_048_576:.1f} MB)"
        )

    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix=path.suffix, dir=path.parent)
    tmp_path = Path(tmp_path_str)
    os.close(tmp_fd)
    bak = path.with_suffix(path.suffix + ".bak")
    original_moved = False

    try:
        cmd = [
            _FFMPEG, "-y", "-i", str(path),
            "-af", f"volume={gain_db:.4f}dB",
            *args,
            "-c:v", "copy",  # keep cover art as-is; ffmpeg would re-encode it (h264 for MP4)
            "-map_metadata", "0",
            "-id3v2_version", "3",
            str(tmp_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=_REWRITE_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError) as e:
            raise RewriteFailed(f"ffmpeg could not run for {path.name}: {e}") from e
        if result.returncode != 0:
            raise RewriteFailed(f"ffmpeg failed for {path.name}:\n{result.stderr[-500:]}")
        _verify_output(tmp_path)

        shutil.move(str(path), str(bak))
        original_moved = True
        shutil.move(str(tmp_path), str(path))
        bak.unlink()
    except Exception as e:
        if original_moved and not path.exists() and bak.exists():
            try:
                shutil.move(str(bak), str(path))
                log.warning("Restored original from .bak: %s", path.name)
            except Exception as restore_err:
                log.critical("RESTORE FAILED for %s -- original is at %s: %s", path.name, bak, restore_err)
        if isinstance(e, (UnsupportedFormat, NotEnoughSpace, RewriteFailed)):
            raise
        raise RewriteFailed(f"levelling failed for {path.name}: {e}") from e
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# ─── The whole job ────────────────────────────────────────────────────────────

def level(
    path: Path,
    *,
    target: float,
    tolerance: float,
    ceiling: float = DEFAULT_TRUE_PEAK_CEILING_DBTP,
    force: bool = False,
) -> QuenchResult:
    """
    Measure *path*, decide, and (only when worthwhile and safe) rewrite it at *target* LUFS.

    Skips, with a reason rather than an error, when the container is one Quench will not
    rewrite, when the file is already within *tolerance* of the target (unless *force*),
    or when the true-peak *ceiling* leaves no headroom for the boost. A measurement or
    rewrite failure lands in `result.error`; this function does not raise for one file,
    so a batch keeps going.
    """
    result = QuenchResult(path=path)
    if path.suffix.lower() not in NORMALISABLE_SUFFIXES:
        result.skipped, result.skip_reason = True, "unsupported_format"
        return result

    try:
        result.before = measure(path)
    except MeasureFailed as e:
        result.error = str(e)
        return result

    if (not force) and abs(result.before.integrated_lufs - target) <= tolerance:
        result.skipped, result.skip_reason = True, "in_tolerance"
        return result

    result.plan = plan_gain(result.before, target, ceiling)
    if not result.plan.worth_applying:
        result.skipped, result.skip_reason = True, "no_headroom"
        return result

    try:
        apply_gain(path, result.plan.applied_db)
    except (UnsupportedFormat, NotEnoughSpace, RewriteFailed) as e:
        result.error = str(e)
        return result
    result.levelled = True
    try:
        result.after = measure(path)
    except MeasureFailed:
        result.after = None  # the rewrite succeeded; a failed re-measure is informational
    return result
