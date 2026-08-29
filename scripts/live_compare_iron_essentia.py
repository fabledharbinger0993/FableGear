#!/usr/bin/env python3
"""
Live head-to-head: Iron vs librosa, run right now on real audio, scored against embedded-tag
ground truth (see docs/IRON_RESEARCH.md SS8.1/SS9 for why not a Rekordbox DB path lookup --
stale FolderPath records on this drive). Rekordbox's own stored BPM/key is included as a
bonus column wherever a file's path happens to resolve in master.db AND has a stored BPM --
partial coverage, reported honestly as such (most of a real library's files were never
actually analyzed in Rekordbox at all).

essentia is NOT run live here -- confirmed it cannot currently be installed on this machine
(no pip wheel for this platform/Python, its legacy source build fails under modern
setuptools, and neither conda-forge nor essentia's own MTG conda channel has an osx-64
build). Its numbers below are the already-documented historical reference
(docs/IRON_RESEARCH.md SS1 / requirements_optional.txt), clearly marked as such throughout,
not a live result on this sample.

Two sampling modes:
  - Default: walk --root in natural (sorted) directory order, take the first N files with a
    usable embedded BPM tag, deduplicating near-identical repeats (same true_bpm/true_key/
    duration -- catches the same file copied under a different name) -- a deliberately
    simple "first folders you come across" methodology, not stratified by genre/BPM/length.
  - --format-stratified: guarantee N tracks of EACH extension in --formats (e.g. 100 mp3 +
    100 aiff), same dedup rule, for a direct per-format accuracy/timing comparison.

Every engine call is wrapped in a wall-clock timeout (SIGALRM) so a hang is measured and
reported as its own outcome ("hangup") rather than blocking the whole run or silently
vanishing into a generic error. Tracks are processed in parallel across workers; within one
track, engines run strictly sequentially so per-engine timing for that track is never
confounded by another engine competing for the same CPU.

Usage:
    python3 scripts/live_compare_iron_essentia.py --root /Volumes/Passport/DATABASE --sample 1000
    python3 scripts/live_compare_iron_essentia.py --root /Volumes/Passport/DATABASE \\
        --format-stratified --formats mp3,aiff --per-format 100
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".ogg", ".opus"}
_ENHARMONIC = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}
_MAX_FILES_PER_DIR = 150

# Generous per-engine-call wall-clock budget: a hang IS the thing being measured here, not
# something to paper over with an infinite wait, but a legitimately slow whole-track decode
# (a long DJ-mix-length file) shouldn't false-positive as a hangup either.
_HANGUP_TIMEOUT_S = 90.0


class _Hangup(Exception):
    """Raised when an engine call exceeds _HANGUP_TIMEOUT_S -- see _run_with_timeout."""


def _run_with_timeout(fn, timeout_s: float):
    """
    Run fn() under a SIGALRM wall-clock budget, raising _Hangup if it doesn't return in time.
    Only safe called from a process's main thread (true here -- each ProcessPoolExecutor
    worker's target function runs as its subprocess's main thread) and only on Unix (fine on
    macOS/Linux; this whole benchmark already assumes Unix via ffmpeg subprocess handling
    elsewhere in this codebase).

    Caveat, not fully solved here: a signal can only be delivered to Python at a bytecode
    boundary or an interruptible syscall (I/O, subprocess wait) -- a genuine infinite loop
    deep inside a C extension with no such boundary would not be interrupted by this. Iron
    and librosa are both numpy/Python-level code with plenty of such boundaries in practice;
    this is noted for anyone extending this to a pure-C engine later.
    """
    def _handler(signum, frame):
        raise _Hangup(f"exceeded {timeout_s:.0f}s")

    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(max(1, int(timeout_s)))
    try:
        return fn()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def _read_candidate(path: Path) -> tuple[Path, float, str | None, float | None] | None:
    """(path, true_bpm, true_camelot_or_None, duration_seconds_or_None), or None if no usable
    embedded BPM tag."""
    import mutagen

    from anvil import read_fields
    from iron import key as iron_key

    try:
        fields = read_fields(path)
    except Exception:
        return None
    if fields.bpm is None or fields.bpm <= 0:
        return None

    true_camelot = None
    if fields.initial_key:
        raw = fields.initial_key.strip()
        if raw in iron_key.CAMELOT.values():
            true_camelot = raw
        else:
            note, mode = (raw[:-1], "min") if raw.endswith("m") else (raw, "maj")
            note = _ENHARMONIC.get(note, note)
            true_camelot = iron_key.CAMELOT.get(note + mode)

    duration = None
    try:
        tags = mutagen.File(path, easy=True)
        if tags is not None and tags.info is not None and getattr(tags.info, "length", None):
            duration = float(tags.info.length)
    except Exception:
        pass

    return path, fields.bpm, true_camelot, duration


def _scan_one_dir(dir_str: str) -> list[tuple[str, float, str | None, float | None]]:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    out = []
    checked = 0
    for path in sorted(Path(dir_str).rglob("*")):
        if path.name.startswith(".") or path.suffix.lower() not in _AUDIO_EXTENSIONS:
            continue
        checked += 1
        candidate = _read_candidate(path)
        if candidate is not None:
            p, bpm, camelot, duration = candidate
            out.append((str(p), bpm, camelot, duration))
        if checked >= _MAX_FILES_PER_DIR:
            break
    return out


def _scan_candidates(
    root: Path, *, scan_limit: int, workers: int
) -> list[tuple[Path, float, str | None, float | None]]:
    """
    Natural-order scan (top-level subdirectories sorted alphabetically, not shuffled) --
    "the first folders you come across" browsing the drive, batched in small parallel groups
    for speed (workers*2 folders in flight at once) rather than one-at-a-time. Batching (not
    submitting the whole 10,000+-folder tree up front) means nothing is ever left running-
    but-abandoned when scan_limit is hit, so a plain wait=True at shutdown is always correct
    and fast -- see scripts/benchmark_iron_genre_diverse.py's own SS9.5 for the leaked-
    semaphore bug this pattern avoids.
    """
    top_level = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))

    batch_size = max(1, workers * 2)
    candidates: list[tuple[Path, float, str | None, float | None]] = []
    i = 0
    pool = ProcessPoolExecutor(max_workers=workers)
    try:
        for batch_start in range(0, len(top_level), batch_size):
            if len(candidates) >= scan_limit:
                print(f"  reached scan_limit={scan_limit} after {i}/{len(top_level)} "
                      f"folders -- stopping scan early", flush=True)
                break
            batch = top_level[batch_start:batch_start + batch_size]
            futures = {pool.submit(_scan_one_dir, str(d)): d for d in batch}
            for fut in as_completed(futures):
                i += 1
                try:
                    results = fut.result()
                except Exception as e:
                    print(f"  scan error in {futures[fut]}: {e}", file=sys.stderr)
                    continue
                for path_str, bpm, camelot, duration in results:
                    candidates.append((Path(path_str), bpm, camelot, duration))
                if i % 25 == 0:
                    print(f"  scanned {i}/{len(top_level)} folders, "
                          f"{len(candidates)} candidates so far", flush=True)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return candidates


def _dedup_key(true_bpm: float, true_camelot: str | None, duration: float | None):
    """Same underlying recording copied under a different filename/folder almost always
    shares BPM, key, and duration exactly -- this is the cheap proxy used to skip those
    without full audio fingerprinting (see docs/IRON_RESEARCH.md SS9.4's duplicate-track
    finding for the real case this guards against)."""
    return (round(true_bpm, 1), true_camelot, round(duration, 1) if duration else None)


def _first_n_dedup(
    candidates: list[tuple[Path, float, str | None, float | None]], count: int
) -> list[tuple[Path, float, str | None, float | None]]:
    seen: set = set()
    selected = []
    for item in candidates:
        _p, bpm, camelot, duration = item
        key = _dedup_key(bpm, camelot, duration)
        if key in seen:
            continue
        seen.add(key)
        selected.append(item)
        if len(selected) >= count:
            break
    return selected


def _first_n_dedup_per_format(
    candidates: list[tuple[Path, float, str | None, float | None]],
    formats: list[str],
    per_format: int,
) -> dict[str, list[tuple[Path, float, str | None, float | None]]]:
    seen: set = set()
    buckets: dict[str, list] = {f: [] for f in formats}
    remaining = set(formats)
    for item in candidates:
        if not remaining:
            break
        p, bpm, camelot, duration = item
        ext = p.suffix.lower().lstrip(".")
        if ext not in remaining:
            continue
        key = _dedup_key(bpm, camelot, duration)
        if key in seen:
            continue
        seen.add(key)
        buckets[ext].append(item)
        if len(buckets[ext]) >= per_format:
            remaining.discard(ext)
    return buckets


def _load_rekordbox_ground_truth(db_path: Path | None) -> dict[str, tuple[float | None, str | None]]:
    """{FolderPath: (bpm, camelot)} for every DjmdContent row with a stored BPM -- built once,
    O(1) lookup per candidate file afterwards. Returns {} on any DB error (missing db,
    pyrekordbox issue) so this stays a bonus column, never a hard dependency."""
    try:
        import db_connection
        from iron import key as iron_key
        from pyrekordbox.db6 import tables
    except Exception as e:
        print(f"  (Rekordbox cross-reference unavailable: {e})", file=sys.stderr)
        return {}

    out: dict[str, tuple[float | None, str | None]] = {}
    try:
        with db_connection.read_db(db_path) as db:
            for row in db.query(tables.DjmdContent):
                fp = row.FolderPath
                if not fp:
                    continue
                bpm = (row.BPM / 100.0) if row.BPM else None
                camelot = None
                try:
                    raw_key = row.KeyName
                except Exception:
                    raw_key = None
                if raw_key:
                    raw_key = raw_key.strip()
                    if raw_key.endswith("m"):
                        note, mode = raw_key[:-1], "min"
                    else:
                        note, mode = raw_key, "maj"
                    note = _ENHARMONIC.get(note, note)
                    camelot = iron_key.CAMELOT.get(note + mode)
                if bpm is not None or camelot is not None:
                    out[fp] = (bpm, camelot)
    except Exception as e:
        print(f"  (Rekordbox cross-reference query failed: {e})", file=sys.stderr)
        return {}
    return out


def _analyze_one(
    args: tuple[int, str, float, str | None, float | None, float | None, str | None],
) -> dict:
    idx, path_str, true_bpm, true_camelot, duration, rb_bpm, rb_camelot = args
    path = Path(path_str)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import iron
    import audio_processor

    row: dict = {
        "idx": idx, "path": path_str, "ext": path.suffix.lower().lstrip("."),
        "true_bpm": true_bpm, "true_camelot": true_camelot, "duration": duration,
        "rekordbox_bpm": rb_bpm, "rekordbox_camelot": rb_camelot,
    }

    # Iron -- whole track, its own tuned 60-180 default (docs/IRON_RESEARCH.md SS8.6).
    t0 = time.time()
    try:
        out = _run_with_timeout(
            lambda: iron.analyze(path, want=("bpm", "initial_key"), bpm_min=60.0, bpm_max=180.0),
            _HANGUP_TIMEOUT_S,
        )
        row["iron_bpm"] = out.bpm
        row["iron_key"] = out.initial_key
        row["iron_status"] = "ok" if out.bpm is not None else "no_result"
    except _Hangup:
        row["iron_bpm"], row["iron_key"], row["iron_status"] = None, None, "hangup"
    except Exception as e:
        row["iron_bpm"], row["iron_key"], row["iron_status"] = None, None, "error"
        row["iron_error"] = str(e)
    row["iron_elapsed_s"] = time.time() - t0

    # librosa -- exactly the production fallback path: _load_audio_ffmpeg's real ANALYSIS_
    # DURATION-bounded (90s, centered) window, then _detect_bpm(fix_octaves=True) (the real
    # octave-fold correction applied when essentia is unavailable) and _detect_key. This is a
    # real, current asymmetry vs. Iron/essentia's whole-track view -- reported as such, not
    # equalized, because it's how this codebase actually runs librosa today.
    t0 = time.time()
    try:
        def _librosa_run():
            audio = audio_processor._load_audio_ffmpeg(path)
            if audio is None:
                return None, None
            bpm = audio_processor._detect_bpm(*audio, path.name, fix_octaves=True)
            key = audio_processor._detect_key(*audio, path.name)
            return bpm, key

        bpm, key = _run_with_timeout(_librosa_run, _HANGUP_TIMEOUT_S)
        row["librosa_bpm"] = bpm
        row["librosa_key"] = key
        row["librosa_status"] = "ok" if bpm is not None else "no_result"
    except _Hangup:
        row["librosa_bpm"], row["librosa_key"], row["librosa_status"] = None, None, "hangup"
    except Exception as e:
        row["librosa_bpm"], row["librosa_key"], row["librosa_status"] = None, None, "error"
        row["librosa_error"] = str(e)
    row["librosa_elapsed_s"] = time.time() - t0

    return row


def _bpm_accuracy(rows: list[dict], field: str) -> dict[str, float]:
    total = len(rows)
    pairs = [(r[field], r["true_bpm"]) for r in rows if r.get(field) is not None]
    undetected = total - len(pairs)
    if not pairs:
        return {"n": 0, "total": total, "exact": 0.0, "within_1pct": 0.0, "mirex": 0.0,
                "undetected_rate": (undetected / total) if total else 0.0}
    exact = sum(1 for d, t in pairs if abs(d - t) <= 0.6)
    within_1pct = sum(1 for d, t in pairs if abs(d - t) / t <= 0.01)
    mirex = sum(1 for d, t in pairs if abs(d - t) / t <= 0.04)
    n = len(pairs)
    return {
        "n": n, "total": total, "exact": exact / n, "within_1pct": within_1pct / n,
        "mirex": mirex / n, "undetected_rate": undetected / total,
    }


def _key_accuracy(rows: list[dict], field: str) -> dict[str, float]:
    has_truth = [r for r in rows if r["true_camelot"] is not None]
    total = len(has_truth)
    pairs = [(r[field], r["true_camelot"]) for r in has_truth if r.get(field) is not None]
    undetected = total - len(pairs)
    if not pairs:
        return {"n": 0, "total": total, "exact": 0.0,
                "undetected_rate": (undetected / total) if total else 0.0}
    exact = sum(1 for d, t in pairs if d == t)
    return {"n": len(pairs), "total": total, "exact": exact / len(pairs),
            "undetected_rate": undetected / total}


def _status_counts(rows: list[dict], field: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[r.get(field, "?")] += 1
    return dict(counts)


def _mean_elapsed(rows: list[dict], field: str) -> tuple[float, int]:
    values = [r[field] for r in rows if r.get(field) is not None]
    if not values:
        return 0.0, 0
    return sum(values) / len(values), len(values)


def _print_engine_table(rows: list[dict], title: str) -> None:
    print(f"\n{title}")
    iron_acc = _bpm_accuracy(rows, "iron_bpm")
    librosa_acc = _bpm_accuracy(rows, "librosa_bpm")
    iron_key_acc = _key_accuracy(rows, "iron_key")
    librosa_key_acc = _key_accuracy(rows, "librosa_key")
    rb_acc = _bpm_accuracy(rows, "rekordbox_bpm")
    rb_key_acc = _key_accuracy(rows, "rekordbox_camelot")
    iron_s = _status_counts(rows, "iron_status")
    librosa_s = _status_counts(rows, "librosa_status")
    iron_mean, iron_n = _mean_elapsed(rows, "iron_elapsed_s")
    librosa_mean, librosa_n = _mean_elapsed(rows, "librosa_elapsed_s")

    iron_nt = f"{iron_acc['n']}/{iron_acc['total']}"
    librosa_nt = f"{librosa_acc['n']}/{librosa_acc['total']}"
    rb_nt = f"{rb_acc['n']}/{rb_acc['total']}"

    print(f"  {'':22s} {'Iron':>14s} {'librosa (live)':>16s} {'Rekordbox (stored)':>20s}")
    print(f"  {'BPM compared/total':22s} {iron_nt:>14s} {librosa_nt:>16s} {rb_nt:>20s}")
    print(f"  {'BPM exact (0.6)':22s} {iron_acc['exact']:>13.1%} {librosa_acc['exact']:>15.1%} "
          f"{rb_acc['exact']:>19.1%}")
    print(f"  {'BPM within 1%':22s} {iron_acc['within_1pct']:>13.1%} "
          f"{librosa_acc['within_1pct']:>15.1%} {rb_acc['within_1pct']:>19.1%}")
    print(f"  {'BPM MIREX (4%)':22s} {iron_acc['mirex']:>13.1%} {librosa_acc['mirex']:>15.1%} "
          f"{rb_acc['mirex']:>19.1%}")
    print(f"  {'BPM undetected':22s} {iron_acc['undetected_rate']:>13.1%} "
          f"{librosa_acc['undetected_rate']:>15.1%} {rb_acc['undetected_rate']:>19.1%}")
    print(f"  {'Key exact (Camelot)':22s} {iron_key_acc['exact']:>13.1%} "
          f"{librosa_key_acc['exact']:>15.1%} {rb_key_acc['exact']:>19.1%}")
    print(f"  {'avg wall-clock s':22s} {iron_mean:>13.2f} {librosa_mean:>15.2f} "
          f"{'(no live call)':>20s}")
    iron_ok_nr = f"{iron_s.get('ok', 0)}/{iron_s.get('no_result', 0)}"
    librosa_ok_nr = f"{librosa_s.get('ok', 0)}/{librosa_s.get('no_result', 0)}"
    iron_err_hang = f"{iron_s.get('error', 0)}/{iron_s.get('hangup', 0)}"
    librosa_err_hang = f"{librosa_s.get('error', 0)}/{librosa_s.get('hangup', 0)}"
    print(f"  {'ok / no_result':22s} {iron_ok_nr:>14s} {librosa_ok_nr:>16s}")
    print(f"  {'error / hangup':22s} {iron_err_hang:>14s} {librosa_err_hang:>16s}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=None, help="directory to scan for audio")
    parser.add_argument("--sample", type=int, default=1000, help="target sample size (default mode)")
    parser.add_argument("--format-stratified", action="store_true",
                         help="guarantee --per-format tracks of EACH extension in --formats")
    parser.add_argument("--formats", type=str, default="mp3,aiff",
                         help="comma-separated extensions for --format-stratified (default: mp3,aiff)")
    parser.add_argument("--per-format", type=int, default=100,
                         help="tracks per format for --format-stratified (default: 100)")
    parser.add_argument("--scan-limit", type=int, default=None,
                         help="stop scanning once this many candidates found (default: sample*8)")
    parser.add_argument("--rekordbox-db", type=Path, default=None,
                         help="path to a Rekordbox master.db for the bonus Rekordbox column")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", type=Path, default=None, help="write per-track JSONL results here")
    args = parser.parse_args(argv)

    if not args.root:
        parser.error("--root is required")

    formats = [f.strip().lower().lstrip(".") for f in args.formats.split(",")]
    scan_limit = args.scan_limit or (
        (args.per_format * len(formats) * 8) if args.format_stratified else args.sample * 8
    )

    print(f"Scanning {args.root} for audio with an embedded BPM tag "
          f"(scan_limit={scan_limit}, {args.workers} parallel folder workers, "
          f"natural directory order)...", flush=True)
    t0 = time.time()
    candidates = _scan_candidates(args.root, scan_limit=scan_limit, workers=args.workers)
    print(f"Found {len(candidates)} candidates in {time.time() - t0:.0f}s", flush=True)

    if args.format_stratified:
        buckets = _first_n_dedup_per_format(candidates, formats, args.per_format)
        for fmt in formats:
            print(f"  {fmt}: {len(buckets[fmt])}/{args.per_format} found", flush=True)
        selected = [item for fmt in formats for item in buckets[fmt]]
    else:
        selected = _first_n_dedup(candidates, args.sample)
    print(f"Selected {len(selected)} tracks after dedup\n", flush=True)

    print("Loading Rekordbox ground truth for the bonus column...", flush=True)
    rb_truth = _load_rekordbox_ground_truth(args.rekordbox_db)
    print(f"Rekordbox rows with a stored BPM/key: {len(rb_truth)}\n", flush=True)

    work = []
    for idx, (path, bpm, camelot, duration) in enumerate(selected):
        rb_bpm, rb_camelot = rb_truth.get(str(path), (None, None))
        work.append((idx, str(path), bpm, camelot, duration, rb_bpm, rb_camelot))

    rows: list[dict] = []
    t0 = time.time()
    out_f = args.out.open("w") if args.out else None
    per_track_budget_s = 2 * _HANGUP_TIMEOUT_S + 60.0  # both engines' worst case + overhead
    overall_timeout = max(180.0, (len(work) / max(1, args.workers)) * per_track_budget_s + 180.0)
    timed_out = False
    pool = ProcessPoolExecutor(max_workers=args.workers)
    try:
        futures = {pool.submit(_analyze_one, w): w for w in work}
        i = 0
        try:
            for fut in as_completed(futures, timeout=overall_timeout):
                i += 1
                row = fut.result()
                rows.append(row)
                if out_f:
                    out_f.write(json.dumps(row) + "\n")
                    out_f.flush()
                if i % 25 == 0 or i == len(work):
                    print(f"  [{i}/{len(work)}] elapsed={time.time() - t0:.0f}s", flush=True)
        except TimeoutError:
            timed_out = True
            stuck = [w for fut, w in futures.items() if not fut.done()]
            print(f"\n  overall timeout ({overall_timeout:.0f}s) hit with {len(stuck)} track(s) "
                  f"still running -- treating as track-level hangups:", flush=True)
            for idx, path_str, true_bpm, true_camelot, duration, rb_bpm, rb_camelot in stuck:
                print(f"    stuck: {path_str}", file=sys.stderr)
                row = {
                    "idx": idx, "path": path_str, "ext": Path(path_str).suffix.lower().lstrip("."),
                    "true_bpm": true_bpm, "true_camelot": true_camelot, "duration": duration,
                    "rekordbox_bpm": rb_bpm, "rekordbox_camelot": rb_camelot,
                    "iron_bpm": None, "iron_key": None, "iron_status": "track_timeout",
                    "iron_elapsed_s": None,
                    "librosa_bpm": None, "librosa_key": None, "librosa_status": "track_timeout",
                    "librosa_elapsed_s": None,
                }
                rows.append(row)
                if out_f:
                    out_f.write(json.dumps(row) + "\n")
                    out_f.flush()
    finally:
        if out_f:
            out_f.close()
        pool.shutdown(wait=not timed_out, cancel_futures=True)

    print("\n" + "=" * 78)
    print(f"LIVE 3-WAY COMPARISON -- {len(rows)} tracks, run just now, "
          f"{'format-stratified' if args.format_stratified else 'first-encountered'} sample")
    print("=" * 78)

    _print_engine_table(rows, "Overall:")

    if args.format_stratified:
        for fmt in formats:
            fmt_rows = [r for r in rows if r["ext"] == fmt]
            if fmt_rows:
                _print_engine_table(fmt_rows, f"\n{fmt.upper()} only (n={len(fmt_rows)}):")
    else:
        by_ext = defaultdict(list)
        for r in rows:
            by_ext[r["ext"]].append(r)
        print("\nFormat breakdown (incidental -- not stratified, reflects natural mix found):")
        for ext, ext_rows in sorted(by_ext.items(), key=lambda kv: -len(kv[1])):
            print(f"  {ext:8s} n={len(ext_rows)}")

    # Scale-degradation check: does per-track engine time drift across the run (idx order =
    # scan/submission order, not completion order, so this isolates a real intra-run trend
    # from parallel-worker scheduling noise). docs/iron/RESEARCH.md SS7.4's "wall-clock time
    # per track... matters for real library-scan UX" -- this is the "does a bigger batch
    # slow it down" half of that question.
    ordered = sorted(rows, key=lambda r: r["idx"])
    half = len(ordered) // 2
    first_half, second_half = ordered[:half], ordered[half:]
    print("\nScale-degradation check (first half of the scan vs. second half, by original "
          "encounter order -- a real slowdown here means something accumulates over a long "
          "run, e.g. memory growth or cache thrashing, not just per-track variance):")
    for field, label in (("iron_elapsed_s", "Iron"), ("librosa_elapsed_s", "librosa")):
        m1, n1 = _mean_elapsed(first_half, field)
        m2, n2 = _mean_elapsed(second_half, field)
        delta = m2 - m1
        print(f"  {label:10s} first half avg={m1:6.2f}s (n={n1})   "
              f"second half avg={m2:6.2f}s (n={n2})   delta={delta:+.2f}s")

    total_hangups_iron = sum(1 for r in rows if r.get("iron_status") in ("hangup", "track_timeout"))
    total_hangups_librosa = sum(1 for r in rows if r.get("librosa_status") in ("hangup", "track_timeout"))
    print(f"\nHangups (engine exceeded {_HANGUP_TIMEOUT_S:.0f}s, or the whole track hit the "
          f"overall per-track safety timeout): Iron={total_hangups_iron}  librosa={total_hangups_librosa}")

    print("\nessentia: NOT run live on this machine -- could not be installed (see this "
          "script's own docstring for exactly what was tried). Historical reference only "
          "(docs/IRON_RESEARCH.md SS1, requirements_optional.txt, 12,687-track corpus):")
    print("  essentia: exact 91.4%  within-1% 94.8%  MIREX 98.3%  (no live timing/hangup data)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
