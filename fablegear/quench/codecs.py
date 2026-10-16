"""
fablegear / quench / codecs.py

The ffmpeg output recipes Quench uses to rewrite a file *without changing what it is*:
same container, same bit depth, same endianness, tags and cover art carried across.

Re-Factor (format conversion in audio_processor.py) shares these recipes deliberately: it
was the second caller to discover that an unqualified `-codec:a pcm_s16le` inside .aiff
produces little-endian AIFF-C that not every player reads, and that AIFF/WAV need
-write_id3v2 or ffmpeg keeps only a handful of text chunks. One place, one fix.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

# Formats Quench can rewrite without losing anything it can't put back. AAC/M4A is left
# alone: re-encoding is lossy, and ffmpeg's MP4 writer drops freeform atoms (cue and beat-grid
# markers, MusicBrainz ids), so a "levelled" m4a would quietly lose DJ metadata.
NORMALISABLE_SUFFIXES = frozenset({".mp3", ".aiff", ".aif", ".wav", ".flac"})

# Rough output/source size ratios by target format, used to size the disk preflight check
# realistically instead of always assuming worst-case (source size again). mp3 in particular
# is drastically smaller than any lossless source, so sizing its margin off the source file
# wrongly skips conversions that would easily fit. flac compresses moderately; wav/aiff are
# ~1:1 with a lossless source (or larger, if going from a lower bit depth up).
CONVERT_SIZE_RATIO = {"mp3": 0.35, "flac": 0.75, "wav": 1.3, "aiff": 1.3}


def source_subtype(path: Path) -> str:
    """soundfile's sample format of *path* ('PCM_16', 'PCM_24', ...), or '' if unreadable."""
    try:
        import soundfile as sf
        return sf.info(str(path)).subtype or ""
    except Exception as e:
        log.warning("sf.info failed for %s, assuming 16-bit. Err: %s", path.name, e)
        return ""


def lossless_codec_args(path: Path, target_format: str) -> list[str]:
    """ffmpeg args that write *target_format* at the bit depth of the source *path*.

    AIFF is written big-endian: pcm_*le inside .aiff makes ffmpeg emit AIFF-C 'sowt', which
    not every player reads. AIFF and WAV also need -write_id3v2, or ffmpeg keeps only a
    handful of text chunks and drops artist, album, key, BPM and the rest.
    """
    sub = source_subtype(path)
    if target_format == "flac":
        # The flac encoder widens 16-bit input to 24-bit unless told otherwise (+75% size).
        args = ["-codec:a", "flac", "-compression_level", "8"]
        return [*args, "-sample_fmt", "s16"] if sub in ("", "PCM_16", "PCM_U8", "PCM_S8") else args
    if "DOUBLE" in sub:
        width = "f64"  # keep floating point: integer PCM would drop precision and clip > 0 dBFS
    elif "FLOAT" in sub:
        width = "f32"
    elif "24" in sub:
        width = "s24"
    elif "32" in sub:
        width = "s32"
    else:
        width = "s16"
    endian = "be" if target_format == "aiff" else "le"
    return ["-codec:a", f"pcm_{width}{endian}", "-write_id3v2", "1"]


def codec_args(path: Path) -> list[str] | None:
    """Return ffmpeg codec args matching the file format and source bit depth.

    None means Quench has no safe way to rewrite this format (see NORMALISABLE_SUFFIXES).
    """
    ext = path.suffix.lower()
    if ext == ".mp3":
        return ["-codec:a", "libmp3lame", "-b:a", "320k"]
    if ext in (".aiff", ".aif"):
        return lossless_codec_args(path, "aiff")
    if ext == ".wav":
        return lossless_codec_args(path, "wav")
    if ext == ".flac":
        return lossless_codec_args(path, "flac")
    return None


def has_room_for(path: Path, *, multiplier: float = 1.5) -> tuple[bool, int, int]:
    """
    Check whether path.parent's filesystem has enough free space to hold a working copy of
    *path* before we start writing one.

    Every in-place transform (level, convert) keeps overhead to a single file at a time: it
    writes one temp file, then swaps it in with shutil.move() -- a same-filesystem rename,
    not a copy, so the original's space is reclaimed the instant .bak is deleted. But a batch
    job still needs *at least one file's worth* of real headroom to start that swap, and on
    a full disk retrying that per file just produces a wall of ffmpeg ENOSPC errors.

    multiplier=1.5 is a safety margin: a re-encode isn't guaranteed to be smaller than the
    source. Returns (has_room, free_bytes, needed_bytes).
    """
    try:
        free_bytes = shutil.disk_usage(path.parent).free
    except OSError:
        return True, 0, 0  # can't check -- don't block on a stat failure
    needed_bytes = int(path.stat().st_size * multiplier)
    return free_bytes >= needed_bytes, free_bytes, needed_bytes
