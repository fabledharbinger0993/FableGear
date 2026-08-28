#!/usr/bin/env python3
"""
One-off experiment: instead of one longer window, take several independent 2-second grabs
from different positions in the track and see if combining them (simple majority-agreement
voting) beats any single grab alone. Throwaway diagnostic, not a proposed production tool.
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark_iron_genre_diverse import _scan_candidates

GRAB_DURATION = 2.0
GRAB_STARTS = (45.0, 90.0, 180.0, 240.0)
AGREEMENT_TOLERANCE = 0.02  # 2% relative -- roughly MIREX-adjacent, for clustering grabs


def _combine(bpms: list[float]) -> float | None:
    """Simple majority-agreement combiner: the largest cluster of mutually-close estimates
    (within AGREEMENT_TOLERANCE of each other), averaged. Falls back to the plain average
    of everything when no two grabs agree at all (no real consensus to lean on)."""
    if not bpms:
        return None
    best_cluster: list[float] = []
    for b in bpms:
        cluster = [x for x in bpms if abs(x - b) / b <= AGREEMENT_TOLERANCE]
        if len(cluster) > len(best_cluster):
            best_cluster = cluster
    if len(best_cluster) >= 2:
        return sum(best_cluster) / len(best_cluster)
    return sum(bpms) / len(bpms)


def _analyze_one(args: tuple[str, float, float | None]) -> dict:
    path_str, true_bpm, duration = args
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from iron import tempo
    from iron.api import _decode

    row: dict = {"path": path_str, "true_bpm": true_bpm, "grabs": {}, "error": None}
    try:
        for start in GRAB_STARTS:
            if duration is not None and start + GRAB_DURATION > duration:
                row["grabs"][start] = None  # doesn't apply -- track too short
                continue
            y, sr = _decode(Path(path_str), GRAB_DURATION, start=start)
            outcome = tempo.detect_tempo(y, sr)
            row["grabs"][start] = outcome[0] if outcome else None
    except Exception as e:
        row["error"] = str(e)
    return row


def _mirex(detected: float | None, true_bpm: float) -> bool:
    return detected is not None and abs(detected - true_bpm) / true_bpm <= 0.04


def main() -> int:
    root = Path("/Volumes/Passport/DATABASE")
    count = 400
    print(f"Scanning for {count} candidates...", flush=True)
    t0 = time.time()
    candidates = _scan_candidates(root, scan_limit=count, workers=4, seed=99)
    print(f"Found {len(candidates)} in {time.time() - t0:.0f}s", flush=True)

    work = [(str(p), b, d) for p, b, _k, _g, d in candidates[:count]]
    rows = []
    t0 = time.time()
    pool = ProcessPoolExecutor(max_workers=4)
    stuck = False
    try:
        futures = {pool.submit(_analyze_one, w): w for w in work}
        i = 0
        try:
            for fut in as_completed(futures, timeout=max(120.0, len(work) * 10)):
                i += 1
                rows.append(fut.result())
                if i % 50 == 0 or i == len(work):
                    print(f"  [{i}/{len(work)}] elapsed={time.time() - t0:.0f}s", flush=True)
        except TimeoutError:
            stuck = True
            print("  timeout, moving on with what completed", flush=True)
    finally:
        pool.shutdown(wait=not stuck, cancel_futures=True)

    n = len(rows)
    print(f"\n{'='*60}\nRESULTS -- n={n}\n{'='*60}")

    print("\n-- Each grab position alone --")
    for start in GRAB_STARTS:
        applicable = [r for r in rows if r["grabs"].get(start) is not None]
        ok = sum(1 for r in applicable if _mirex(r["grabs"][start], r["true_bpm"]))
        na = n - len(applicable)
        print(f"  {start:>5.0f}s  n_applicable={len(applicable):4d} (skipped {na:3d} too-short)  "
              f"MIREX: {ok}/{len(applicable)} ({ok/max(1,len(applicable)):.1%})")

    print("\n-- Combined (majority-agreement across whichever grabs applied) --")
    combined_ok = 0
    combined_n = 0
    grab_counts: dict[int, int] = {}
    for r in rows:
        bpms = [v for v in r["grabs"].values() if v is not None]
        grab_counts[len(bpms)] = grab_counts.get(len(bpms), 0) + 1
        if not bpms:
            continue
        combined = _combine(bpms)
        combined_n += 1
        if _mirex(combined, r["true_bpm"]):
            combined_ok += 1
    print(f"  MIREX: {combined_ok}/{combined_n} ({combined_ok/max(1,combined_n):.1%})")
    print(f"  grabs-per-track distribution: {dict(sorted(grab_counts.items()))}")

    errors = sum(1 for r in rows if r["error"])
    if errors:
        print(f"\n{errors} tracks errored")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
