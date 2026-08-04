"""
FableGear / downloader.py

Downloads audio from web sources (Bandcamp, Beatport, Soundcloud, direct URLs)
via yt-dlp. Each download runs as a background daemon thread.

Job lifecycle:  queued → downloading → converting → importing → done / failed

Progress events are broadcast to all connected WebSocket clients via ws_bus so
the FableGo mobile web app gets live updates without polling.

After a successful download the file is auto-imported into the Rekordbox DB.
Import failure is non-fatal — the file is on disk and can be imported manually.
"""

import json
import logging
import shutil
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

log = logging.getLogger(__name__)

# ─── Job store ────────────────────────────────────────────────────────────────

_LOCK: threading.Lock = threading.Lock()
_JOBS: OrderedDict[str, dict] = OrderedDict()   # job_id → job dict, insertion order
_PROCS: dict[str, subprocess.Popen] = {}         # job_id → running yt-dlp Popen (cleared on finish)
_MAX_JOBS = 200

# ─── yt-dlp path cache ────────────────────────────────────────────────────────
# Resolved once on first use; safe to cache because yt-dlp doesn't move at runtime.
_YTDLP_PATH: str | None = None
_YTDLP_LOCK: threading.Lock = threading.Lock()

# ─── Supported formats ────────────────────────────────────────────────────────
#
# Output formats: All formats natively supported by Rekordbox.
# The downloader converts web streams or source files to these formats.
#
# LOSSLESS PREFERRED (for DJ library integrity):
#   - "aiff"   AIFF Interchange File Format — highest DJ standard, lossless
#   - "wav"    WAV — lossless uncompressed, universal compatibility
#   - "flac"   FLAC — lossless compression, smaller files than WAV
#
# COMPRESSED (smaller file size):
#   - "mp3"    MP3 — lossy, universal compatibility, DJ standard fallback
#   - "m4a"    M4A/AAC — lossy, Apple standard, smaller than MP3
#   - "ogg"    OGG Vorbis — open-source lossy, excellent quality/compression ratio
#   - "opus"   Opus — modern lossy, best quality-to-bitrate ratio
#
# DEFAULT: "aiff" (maximum fidelity for DJ use)
#

FORMATS = {
    # Lossless (preferred for master library)
    "aiff", "wav", "flac",
    # Compressed (space-efficient, full DJ compatibility)
    "mp3", "m4a", "ogg", "opus",
    # Rekordbox also supports ALAC (Apple Lossless) but it's typically in .m4a container
}
DEFAULT_FORMAT = "aiff"

# Legacy format conversion mapping
# Maps input formats (early 2000s, etc.) to recommended Rekordbox output format
LEGACY_CONVERSION_MAP = {
    ".wma": "mp3",       # Windows Media Audio → MP3
    ".ape": "flac",      # Monkey's Audio (lossless) → FLAC
    ".mpc": "flac",      # Musepack → FLAC (preserves lossless quality)
    ".mp+": "flac",      # Musepack alternate extension
    ".wv": "flac",       # WavPack → FLAC
    ".aac": "m4a",       # Raw AAC → M4A container
    ".ac3": "wav",       # Dolby Digital → WAV (safe fallback)
    ".dff": "flac",      # DSD → FLAC
    ".dsf": "flac",      # DSD alternate → FLAC
}

# ─── Public API ───────────────────────────────────────────────────────────────

def enqueue(url: str, destination: str, filename: str | None = None,
            fmt: str = DEFAULT_FORMAT) -> str:
    """
    Enqueue a download job and return its job_id immediately.
    The download runs in a background daemon thread.
    """
    if not url or not url.strip().startswith(("http://", "https://")):
        raise ValueError("URL must start with http:// or https://")
    url = url.strip()
    job_id = str(uuid.uuid4())
    fmt = fmt if fmt in FORMATS else DEFAULT_FORMAT

    job: dict = {
        "job_id":      job_id,
        "url":         url,
        "destination": destination,
        "format":      fmt,
        "filename":    filename,
        "status":      "queued",
        "progress":    0,
        "title":       None,
        "artist":      None,
        "file_path":   None,
        "error":       None,
    }

    with _LOCK:
        _JOBS[job_id] = job
        while len(_JOBS) > _MAX_JOBS:
            _JOBS.popitem(last=False)

    threading.Thread(target=_run, args=(job_id,), daemon=True).start()
    return job_id


def get_all_jobs() -> list:
    """Return all jobs newest-first (snapshot, safe to serialise)."""
    with _LOCK:
        return list(reversed(list(_JOBS.values())))


def get_job(job_id: str) -> dict | None:
    """Return a single job dict or None if not found."""
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def cancel_job(job_id: str) -> bool:
    """
    Cancel a queued or in-progress download job.

    - If the job is queued (thread not yet started processing yt-dlp), it is
      marked cancelled immediately.
    - If yt-dlp is already running, the subprocess is terminated and the job
      is marked cancelled.
    - Returns True if the job existed and was not already in a terminal state.
    """
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return False
        if job["status"] in ("done", "failed", "cancelled"):
            return False
        _JOBS[job_id]["status"] = "cancelled"
        proc = _PROCS.pop(job_id, None)

    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception as exc:
                # Both terminate() and kill() failed — the yt-dlp subprocess may
                # be left running as an orphan. Nothing more we can do, but log
                # it so a lingering process isn't a total mystery later.
                log.warning("cancel_job: could not kill yt-dlp process for job %s: %s", job_id, exc)

    try:
        from ws_bus import broadcast
        with _LOCK:
            snapshot = dict(_JOBS.get(job_id, {}))
        if snapshot:
            broadcast(json.dumps({"type": "download_update", "job": snapshot}))
    except Exception as exc:
        log.debug("cancel_job broadcast failed: %s", exc)

    return True


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _update(job_id: str, **kwargs) -> None:
    """Mutate job fields and broadcast the update to all WebSocket clients."""
    try:
        from ws_bus import broadcast
        with _LOCK:
            if job_id not in _JOBS:
                return
            _JOBS[job_id].update(kwargs)
            snapshot = dict(_JOBS[job_id])
        broadcast(json.dumps({"type": "download_update", "job": snapshot}))
    except Exception as exc:
        log.debug("_update broadcast failed: %s", exc)


def _find_ytdlp() -> str:
    global _YTDLP_PATH
    with _YTDLP_LOCK:
        if _YTDLP_PATH is not None:
            return _YTDLP_PATH
        found = shutil.which("yt-dlp")
        if found:
            _YTDLP_PATH = found
            return _YTDLP_PATH
        for candidate in ("/opt/homebrew/bin/yt-dlp", "/usr/local/bin/yt-dlp"):
            if Path(candidate).exists():
                _YTDLP_PATH = candidate
                return _YTDLP_PATH
        _YTDLP_PATH = "yt-dlp"
        return _YTDLP_PATH


def _run(job_id: str) -> None:
    with _LOCK:
        job = dict(_JOBS.get(job_id, {}))
    if not job:
        return

    # Bail immediately if cancelled while still queued
    if job.get("status") == "cancelled":
        return

    url         = job["url"]
    destination = job["destination"]
    fmt         = job["format"]
    filename    = job["filename"]

    dest_path = Path(destination)
    try:
        dest_path.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        _update(job_id, status="failed", error=f"cannot create destination folder: {exc}")
        return

    _update(job_id, status="downloading", progress=10)

    yt_dlp = _find_ytdlp()

    # Output template: use provided filename or let yt-dlp derive it from metadata
    if filename:
        # Strip any extension the caller may have included
        stem = Path(filename).stem
        output_tmpl = str(dest_path / f"{stem}.%(ext)s")
    else:
        output_tmpl = str(dest_path / "%(artist)s - %(title)s.%(ext)s")

    # Format-specific postprocessor args for quality/codec settings
    # Quality levels aligned with DJ standards and Rekordbox compatibility
    audio_quality = {
        "mp3":   ["--audio-format", "mp3", "--audio-quality", "320K"],
        "m4a":   ["--audio-format", "m4a", "--audio-quality", "192"],
        "ogg":   ["--audio-format", "vorbis", "--audio-quality", "192"],
        "opus":  ["--audio-format", "opus", "--audio-quality", "192"],
        "flac":  ["--audio-format", "flac"],         # lossless, no quality needed
        "wav":   ["--audio-format", "wav"],          # lossless, no quality needed
        "aiff":  ["--audio-format", "aiff"],         # lossless, no quality needed
    }
    pp_args = audio_quality.get(fmt, ["--audio-format", fmt])

    cmd = [
        yt_dlp,
        "--extract-audio",
        *pp_args,
        "--embed-thumbnail",
        "--embed-metadata",
        "--output", output_tmpl,
        "--no-playlist",
        "--print", "after_move:filepath",   # prints final path to stdout
        "--",  # end of flags — prevents URLs starting with '--' being parsed as options
        url,
    ]

    # Record start time for the fallback file-detection window
    job_start = time.monotonic()

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        _update(job_id, status="failed",
                error="yt-dlp not installed — run: pip install yt-dlp")
        return
    except Exception as exc:
        _update(job_id, status="failed", error=str(exc))
        return

    # Register the process so cancel_job() can terminate it
    with _LOCK:
        if _JOBS.get(job_id, {}).get("status") == "cancelled":
            # Cancelled between Popen and registration — kill immediately
            proc.kill()
            proc.wait()
            return
        _PROCS[job_id] = proc

    try:
        stdout, stderr = proc.communicate(timeout=600)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        _update(job_id, status="failed", error="download timed out after 10 minutes")
        return
    finally:
        with _LOCK:
            _PROCS.pop(job_id, None)

    # If cancelled mid-run, don't overwrite the "cancelled" status
    with _LOCK:
        if _JOBS.get(job_id, {}).get("status") == "cancelled":
            return

    if proc.returncode != 0:
        err = stderr.strip()[-500:] or "yt-dlp exited with error"
        _update(job_id, status="failed", error=err)
        return

    # yt-dlp prints the final file path via --print after_move:filepath
    downloaded_path: Path | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if line and Path(line).exists():
            downloaded_path = Path(line)
            break

    # Fallback: most-recently-modified audio file written after this job started.
    # The time window prevents picking up pre-existing files in the destination.
    if downloaded_path is None:
        audio_exts = {".aiff", ".aif", ".aifc", ".flac", ".wav", ".mp3",
                      ".m4a", ".m4p", ".ogg", ".opus"}
        cutoff = time.time() - (time.monotonic() - job_start) - 5  # 5 s grace window
        candidates = sorted(
            [
                f for f in dest_path.iterdir()
                if f.suffix.lower() in audio_exts and f.stat().st_mtime >= cutoff
            ],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            downloaded_path = candidates[0]

    if downloaded_path is None or not downloaded_path.exists():
        _update(job_id, status="failed",
                error="download completed but could not locate the output file")
        return

    _update(job_id, status="converting", progress=70,
            file_path=str(downloaded_path))

    # Try to read title/artist from tags to surface in the job summary
    try:
        from mutagen import File as MutagenFile
        mf = MutagenFile(str(downloaded_path), easy=True)
        if mf:
            title  = (mf.get("title")  or [None])[0]
            artist = (mf.get("artist") or [None])[0]
            _update(job_id, title=title, artist=artist)
    except Exception:
        pass

    # ── Auto-import into Rekordbox DB ─────────────────────────────────────────
    _update(job_id, status="importing", progress=85)
    try:
        from config import DEVICE_DB as _DB
        from db_connection import write_db
        from importer import _import_track
        from scanner import extract_metadata

        track_info = extract_metadata(downloaded_path)
        if track_info.is_valid:
            with write_db(_DB) as db:
                _import_track(track_info, db)
                db.commit()
            log.info("Auto-imported into Rekordbox DB: %s", downloaded_path.name)
        else:
            log.warning("Skipping DB import — file not valid: %s — %s",
                        downloaded_path.name, track_info.errors)
    except Exception as exc:
        # Import failure is non-fatal: file is on disk, user can import manually
        log.warning("Post-download DB import failed for %s: %s",
                    downloaded_path.name, exc)

    _update(job_id, status="done", progress=100, file_path=str(downloaded_path))
