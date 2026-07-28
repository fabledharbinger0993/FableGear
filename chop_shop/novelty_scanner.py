"""
fablegear / novelty_scanner.py

Identifies tracks that exist ONLY on a source drive / library and have no
confirmed match in the destination library — then copies them across using
integrate mode (source is never touched).

Philosophy
----------
This is the OPPOSITE risk profile of the duplicate detector.

    Duplicate detector:  only act on certainty.
                         False positive = permanent deletion.  Unacceptable.

    Novelty scanner:     copy unless proven present.
                         False positive = one extra copy on disk.  Fine.

A track is considered NOVEL (copy it) unless we find a Chromaprint
fingerprint match with high confidence in the destination.  When in doubt
we always copy.  We are capturing potentially irreplaceable recordings from
across 500 000+ tracks — erring on the side of caution means erring on the
side of copying.

Matching strategy
-----------------
Two-phase to keep Chromaprint calls to a minimum:

  Phase 1 — Pre-filter (fast)
    Build a key+BPM+duration index of the DESTINATION from scan_index.json
    and/or the rekordbox DB, plus a normalized-filename index. A source
    track becomes a fingerprint candidate if it matches on key+BPM+duration
    OR shares a normalized filename with a destination track. Only a track
    with NEITHER kind of match is flagged novel without fingerprinting.
    The filename fallback matters when scan_index.json hasn't been built
    yet (fresh install, or files never run through Tag Tracks) — without
    it, BPM/key/duration are unknown on both sides for every comparison,
    Phase 1 finds nothing, and Phase 2 never runs at all: a literal
    drive-to-drive copy of the destination would be re-copied as "novel"
    every time, without a single fingerprint ever being checked.

  Phase 2 — Fingerprint confirmation (slow, only for candidates)
    Source tracks that DO match a destination track on key+BPM+duration are
    fingerprinted and compared.  A match requires Chromaprint similarity ≥
    _FP_SIMILARITY_MIN.  If the fingerprint doesn't confirm it → novel, copy.

The result is that Chromaprint is only called for tracks that look like they
might already be in the destination — the vast majority of genuinely novel
tracks never need fingerprinting at all.

Public interface
----------------
    scan_novel(source, destination, *, dry_run, max_workers, progress_cb)
        -> NovelScanResult
"""

import json
import logging
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

try:
    from path_guard import guard_sources as _guard_sources
except ImportError:  # imported via the chop_shop package
    from chop_shop.path_guard import guard_sources as _guard_sources

log = logging.getLogger(__name__)

# ─── Tolerances (same as duplicate_detector for consistency) ─────────────────
_BPM_TOLERANCE_PCT: float    = 0.03   # ±3%
_DURATION_TOLERANCE_SEC: float = 3.0  # ±3 s

# Chromaprint similarity threshold.
# LOWER than the duplicate detector on purpose — we only skip copying if we
# are highly confident the track is already present.  0.85 = 85% match.
_FP_SIMILARITY_MIN: float = 0.85

# ─── Result types ─────────────────────────────────────────────────────────────

@dataclass
class NovelTrack:
    path:        Path
    action:      str          # "copied" | "dry_run" | "skipped" | "error"
    reason:      str  = ""
    dest:        Path | None = None


@dataclass
class NovelScanResult:
    novel:      list[NovelTrack] = field(default_factory=list)
    present:    list[Path]       = field(default_factory=list)   # confirmed in dest
    errors:     list[Path]       = field(default_factory=list)
    total_src:  int = 0
    dest_index_size: int = 0


# ─── Index helpers ────────────────────────────────────────────────────────────

def _load_scan_index() -> dict[str, dict]:
    """Load ~/fablegear/scan_index.json if it exists.

    scan_index.json is written as a JSON array of objects, each with a
    "path" key. Convert to {path: entry} dict for O(1) lookup.
    """
    p = Path.home() / "rekordbox-toolkit" / "scan_index.json"
    if not p.exists():
        return {}
    try:
        with open(p) as f:
            data = json.load(f)
        if isinstance(data, list):
            return {e["path"]: e for e in data if "path" in e}
        if isinstance(data, dict):
            return data
        return {}
    except Exception as exc:
        log.warning("Could not load scan_index.json: %s", exc)
        return {}


def _build_dest_index(destination: Path) -> dict[str, dict]:
    """
    Build a lightweight {path_str: {bpm, key, duration_sec}} index for every
    audio file found under *destination*.

    Uses scan_index.json for files that have already been processed.
    Falls back to a filesystem-only entry (no BPM/key) for everything else —
    those files will proceed straight to Chromaprint in Phase 2.
    """
    from config import AUDIO_EXTENSIONS, SKIP_DIRS, SKIP_PREFIXES

    scan_index = _load_scan_index()
    dest_str   = str(destination.resolve())

    # Seed from scan_index entries that live under destination
    index: dict[str, dict] = {
        p: v for p, v in scan_index.items()
        if p.startswith(dest_str)
    }

    # Walk destination for any files NOT already in scan_index
    for dirpath, dirnames, filenames in os.walk(destination):
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not any(d.startswith(px) for px in SKIP_PREFIXES)
        ]
        for fname in filenames:
            if any(fname.startswith(px) for px in SKIP_PREFIXES):
                continue
            fp = Path(dirpath) / fname
            if fp.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            key = str(fp)
            if key not in index:
                index[key] = {}   # no metadata yet — will fingerprint if needed

    return index


def _dest_candidates(
    track_bpm: float | None,
    track_key: str | None,
    track_dur: float | None,
    dest_index: dict[str, dict],
    filename_candidates: "list[str] | None" = None,
) -> list[str]:
    """
    Return dest index keys that could be the same track based on
    BPM ± 3%, key match, and duration ± 3 s.

    If a dest entry has no metadata (scanned from disk only) it is always
    returned as a candidate — better to fingerprint it than miss a match.

    filename_candidates are always included regardless of metadata state —
    they come from a normalized-filename match against the destination
    (see _build_dest_filename_index) and are the fallback that keeps
    fingerprinting from being skipped entirely when scan_index.json hasn't
    been built (BPM/key/duration unknown on both sides). Without this, a
    literal file copy sitting in the destination — same filename, zero
    tag metadata on either side — would never be candidate-matched at all
    and would silently get re-copied as "novel".
    """
    candidates = set(filename_candidates or ())
    no_src_metadata = (track_bpm is None and track_key is None and track_dur is None)
    for dest_path, meta in dest_index.items():
        # No metadata on either side → skip (unless already added via a
        # filename match above); fingerprinting the entire library against
        # itself is prohibitively slow and the module philosophy is
        # "when in doubt, copy" — but only once a real check had a chance.
        if not meta:
            if no_src_metadata:
                continue
            candidates.add(dest_path)
            continue

        # Duration check
        dest_dur = meta.get("duration_sec")
        if track_dur and dest_dur:
            if abs(track_dur - dest_dur) > _DURATION_TOLERANCE_SEC:
                continue

        # BPM check — coerce to float; scan_index may store BPM as a string
        dest_bpm = meta.get("bpm")
        try:
            dest_bpm = float(dest_bpm) if dest_bpm is not None else None
        except (ValueError, TypeError):
            dest_bpm = None
        try:
            track_bpm_f = float(track_bpm) if track_bpm is not None else None
        except (ValueError, TypeError):
            track_bpm_f = None
        if track_bpm_f and dest_bpm:
            tolerance = track_bpm_f * _BPM_TOLERANCE_PCT
            if abs(track_bpm_f - dest_bpm) > tolerance:
                continue

        # Key check (skip if either side missing)
        dest_key = meta.get("key")
        if track_key and dest_key and track_key != dest_key:
            continue

        candidates.add(dest_path)

    return list(candidates)


def _build_dest_filename_index(dest_index: dict[str, dict]) -> dict[str, list[str]]:
    """Map normalized filename -> destination paths sharing that name.

    Cheap (O(1) lookup per source track) fallback candidate source for
    _dest_candidates when BPM/key/duration are unknown on both sides — the
    scenario that otherwise skips fingerprinting entirely (see its
    docstring). A same-named file in the destination is exactly the case a
    literal drive-to-drive copy produces, so this is what actually catches it.
    """
    index: dict[str, list[str]] = {}
    for dest_path in dest_index.keys():
        key = _normalized_filename_key(Path(dest_path))
        index.setdefault(key, []).append(dest_path)
    return index


# ─── Fingerprint comparison ───────────────────────────────────────────────────

def _fp_similarity(fp_a: str, fp_b: str) -> float:
    """
    Fast character-level similarity between two Chromaprint fingerprint strings.
    Returns 0.0–1.0.  Not as precise as acoustid's full comparison but avoids
    an extra network/library dependency and is fast enough for local use.
    """
    if not fp_a or not fp_b:
        return 0.0
    # Use the shorter fingerprint as the reference length
    min_len = min(len(fp_a), len(fp_b))
    matches = sum(a == b for a, b in zip(fp_a[:min_len], fp_b[:min_len]))
    return matches / min_len


def _confirmed_in_dest(
    src_path: Path,
    candidates: list[str],
) -> bool:
    """
    Fingerprint src_path and compare against candidate dest files.
    Returns True only if at least one candidate scores ≥ _FP_SIMILARITY_MIN.
    On any error, returns False — when in doubt, copy.
    """
    from duplicate_detector import fingerprint_file

    src_fp = fingerprint_file(src_path)
    if src_fp is None:
        log.warning("Could not fingerprint %s — treating as novel", src_path.name)
        return False

    for dest_path_str in candidates:
        dest_path = Path(dest_path_str)
        if not dest_path.exists():
            continue
        dest_fp = fingerprint_file(dest_path)
        if dest_fp is None:
            continue
        sim = _fp_similarity(src_fp, dest_fp)
        if sim >= _FP_SIMILARITY_MIN:
            log.debug(
                "Confirmed present: %s ↔ %s (sim=%.2f)",
                src_path.name, dest_path.name, sim,
            )
            return True

    return False


# ─── Copy helper ─────────────────────────────────────────────────────────────

def _copy_novel(src: Path, destination: Path, dry_run: bool) -> NovelTrack:
    """
    Copy a novel track into destination, preserving Artist/Album structure
    from the source path where available, falling back to a flat copy.
    Uses library_organizer canonical dest logic when possible.
    """
    if dry_run:
        return NovelTrack(path=src, action="dry_run",
                          reason="novel — not found in destination")

    # Derive a sensible destination path
    try:
        from library_organizer import _canonical_dest, MIX_THRESHOLD_SEC
        from scanner import scan_directory
        # scan just this one file
        tracks = list(scan_directory(src.parent))
        track  = next((t for t in tracks if t.path == src), None)
        if track:
            dest = _canonical_dest(src, destination, track, MIX_THRESHOLD_SEC)
        else:
            dest = destination / src.name
    except Exception:
        dest = destination / src.name

    # Resolve name collision
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        for i in range(1, 1000):
            candidate = dest.with_name(f"{stem}_{i}{suffix}")
            if not candidate.exists():
                dest = candidate
                break
        else:
            return NovelTrack(path=src, action="error",
                              reason="no free destination slot", dest=dest)

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dest))
        return NovelTrack(path=src, action="copied", dest=dest)
    except Exception as exc:
        return NovelTrack(path=src, action="error", reason=str(exc), dest=dest)


def _normalized_filename_key(path: Path) -> str:
    """Return a normalized filename key used by filename-only novelty matching."""
    stem = path.stem.lower()
    stem = re.sub(r"[^a-z0-9]+", " ", stem).strip()
    return stem


# ─── Main entry point ─────────────────────────────────────────────────────────

def scan_novel(
    source:      "Path | list[Path]",
    destination: Path,
    *,
    copy_to:     "Path | None" = None,
    dry_run:     bool = True,
    max_workers: int  = 1,
    match_mode:  str  = "fingerprint",
    progress_cb: "callable | None" = None,
    archive=None,
    skip_paths: "set[str] | None" = None,
    on_result: "callable | None" = None,
) -> NovelScanResult:
    """
    Scan *source* for tracks that do not exist in *destination* and copy them
    across (integrate mode — source is never modified).

    Parameters
    ----------
    source : Path | list[Path]
        One directory or a list of directories to scan for novel tracks.
    destination : Path
        The library to compare against — a track already found here (by
        metadata pre-filter + fingerprint, or by filename in filename mode)
        is skipped as "present". This is NOT necessarily where novel tracks
        land; see copy_to.
    copy_to : Path | None
        Where confirmed-novel tracks actually get copied. Defaults to
        *destination* when omitted, matching the original single-folder
        behavior. Pointing this somewhere separate from *destination* lets a
        scan check against the real home library while keeping newly-found
        tracks segregated in their own folder for review, rather than
        dumping them straight into the compared-against library.
    dry_run : bool
        If True (default), report what would be copied without doing it.
    max_workers : int
        Parallel workers for fingerprint + copy phase (default 1 = sequential).
    match_mode : str
        "fingerprint" (default) uses metadata pre-filter + fingerprint confirmation.
        "filename" compares normalized filenames only (faster, less strict).
    progress_cb : callable | None
        Optional callback(done, total, copied, skipped, errors) called after
        each track is processed.  Used by the Flask SSE route.
    skip_paths : set[str] | None
        Absolute source paths (as str) to exclude entirely — used by the
        caller for checkpoint resume (tracks already evaluated/copied in an
        interrupted run).
    on_result : callable | None
        Invoked once per track as callback(NovelTrack) right after tallying,
        so the caller can persist a checkpoint incrementally.
    """
    from config import AUDIO_EXTENSIONS, SKIP_DIRS, SKIP_PREFIXES

    source_list: list[Path] = [source] if isinstance(source, Path) else list(source)
    _guard_sources(source_list, "the novelty scanner")
    match_mode = (match_mode or "fingerprint").strip().lower()
    if match_mode not in {"fingerprint", "filename"}:
        raise ValueError(f"Unsupported match_mode: {match_mode}")
    copy_root: Path = copy_to if copy_to is not None else destination

    # ── 1. Build destination index ────────────────────────────────────────────
    log.info("Building destination index: %s", destination)
    dest_index = _build_dest_index(destination)
    dest_size  = len(dest_index)
    dest_filename_index = _build_dest_filename_index(dest_index)
    log.info("Destination index: %d tracks", dest_size)

    # ── 2. Collect source tracks ──────────────────────────────────────────────
    src_tracks: list[Path] = []
    scan_index = _load_scan_index()

    for s in source_list:
        for dirpath, dirnames, filenames in os.walk(s):
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_DIRS
                and not any(d.startswith(px) for px in SKIP_PREFIXES)
            ]
            for fname in filenames:
                if any(fname.startswith(px) for px in SKIP_PREFIXES):
                    continue
                fp = Path(dirpath) / fname
                if fp.suffix.lower() in AUDIO_EXTENSIONS:
                    src_tracks.append(fp)

    if skip_paths:
        src_tracks = [p for p in src_tracks if str(p) not in skip_paths]
    total = len(src_tracks)
    log.info("Source tracks to evaluate: %d", total)

    result = NovelScanResult(total_src=total, dest_index_size=dest_size)

    done = copied = skipped = errors = fingerprinted = 0

    def _emit():
        print(
            "FABLEGEAR_PROGRESS: " + json.dumps({
                "done":           done,
                "total":          total,
                "remaining":      total - done,
                "copied":         copied,
                "skipped":        skipped,
                "errors":         errors,
                "fingerprinted":  fingerprinted,
                "dest_size":      dest_size,
            }),
            flush=True,
        )
        if progress_cb:
            progress_cb(done, total, copied, skipped, errors)

    _emit()

    def _process(src: Path) -> NovelTrack:
        nonlocal fingerprinted

        src_filename_key = _normalized_filename_key(src)

        if match_mode == "filename":
            if src_filename_key in dest_filename_index:
                return NovelTrack(path=src, action="skipped", reason="filename match in destination")
            return _copy_novel(src, copy_root, dry_run)

        src_meta   = scan_index.get(str(src), {})
        try:
            src_bpm = float(src_meta["bpm"]) if src_meta.get("bpm") is not None else None
        except (ValueError, TypeError):
            src_bpm = None
        src_key    = src_meta.get("key")
        try:
            src_dur = float(src_meta["duration_sec"]) if src_meta.get("duration_sec") is not None else None
        except (ValueError, TypeError):
            src_dur = None

        # Phase 1: pre-filter — find destination candidates. Filename
        # matches are always folded in (see _dest_candidates) so a literal
        # file copy still gets fingerprint-checked even with zero BPM/key
        # metadata on either side, instead of silently skipping straight to
        # "novel" because scan_index.json hasn't been built.
        filename_candidates = dest_filename_index.get(src_filename_key, [])
        candidates = _dest_candidates(src_bpm, src_key, src_dur, dest_index, filename_candidates)

        if not candidates:
            # No metadata match anywhere in destination → novel, copy immediately
            log.debug("Novel (no pre-filter match): %s", src.name)
            return _copy_novel(src, copy_root, dry_run)

        # Phase 2: fingerprint confirmation — only called when candidates exist
        fingerprinted += 1
        if _confirmed_in_dest(src, candidates):
            return NovelTrack(path=src, action="skipped",
                              reason="confirmed present in destination")

        # Candidates existed but fingerprint didn't confirm — copy it
        log.debug("Novel (fingerprint mismatch): %s", src.name)
        return _copy_novel(src, copy_root, dry_run)

    def _journal_copy(r: NovelTrack) -> None:
        """Journal each copy the moment it lands — an interrupted scan must
        still record every track it brought into the library."""
        if archive is None or r.action != "copied" or r.dest is None:
            return
        try:
            archive.log_operation(
                "novelty_copy", str(r.dest), status="ok",
                metadata={"from": str(r.path)},
            )
        except Exception as exc:
            log.warning("Archive update failed for novelty copy %s: %s", r.path, exc)

    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_process, t): t for t in src_tracks}
            for future in as_completed(futures):
                try:
                    r = future.result()
                except Exception as exc:
                    r = NovelTrack(path=futures[future], action="error",
                                   reason=str(exc))
                if r.action in ("copied", "dry_run"):
                    copied  += 1
                    result.novel.append(r)
                    _journal_copy(r)
                elif r.action == "skipped":
                    skipped += 1
                    result.present.append(futures[future])
                elif r.action == "error":
                    errors  += 1
                    result.errors.append(futures[future])
                    log.error("Error processing %s: %s", futures[future].name, r.reason)
                if on_result is not None:
                    on_result(r)
                done += 1; _emit()
    else:
        for i, src in enumerate(src_tracks):
            try:
                r = _process(src)
            except Exception as exc:
                r = NovelTrack(path=src, action="error", reason=str(exc))

            if r.action in ("copied", "dry_run"):
                copied  += 1
                result.novel.append(r)
                _journal_copy(r)
            elif r.action == "skipped":
                skipped += 1
                result.present.append(src)
            elif r.action == "error":
                errors  += 1
                result.errors.append(src)
                log.error("Error processing %s: %s", src.name, r.reason)

            if on_result is not None:
                on_result(r)

            done += 1
            log.info("[%d/%d] %-8s %s", done, total, r.action.upper(), src.name)
            _emit()

    log.info(
        "Novel scan complete — novel: %d  present: %d  errors: %d  "
        "fingerprinted: %d / %d",
        len(result.novel), len(result.present), len(result.errors),
        fingerprinted, total,
    )

    if archive is not None:
        archive.log_operation(
            "novelty_scan",
            metadata={
                "novel": len(result.novel),
                "present": len(result.present),
                "errors": len(result.errors),
                "match_mode": match_mode,
                "dry_run": dry_run,
            },
        )

    return result
