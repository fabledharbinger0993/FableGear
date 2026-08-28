#!/usr/bin/env python3
"""
Multi-pass combine experiment: 3 short (2s) grabs per track at proportional positions
(10%/35%/60% of duration -- not fixed absolute seconds, which left real coverage gaps for
shorter tracks in earlier same-day experiments), combined via confidence-weighted
agreement clustering. Fixes two specific flaws found in an earlier naive majority-vote
combiner: (1) votes are weighted by their own detection confidence, not just counted, and
(2) when nothing agrees, falls back to the single highest-confidence grab, not a blind
average of unrelated values (which produced meaningless mid-octave blends before).

Throwaway diagnostic script, not a proposed production tool.
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
POSITION_FRACTIONS = (0.10, 0.35, 0.60)
AGREEMENT_TOLERANCE = 0.02  # 2% relative


def _combine(results: list[tuple[float, float]]) -> tuple[float, str]:
    """results: list of (bpm, confidence). Returns (combined_bpm, method_used)."""
    if not results:
        return None, "none"
    if len(results) == 1:
        return results[0][0], "single"

    best_cluster: list[tuple[float, float]] = []
    best_weight = -1.0
    for bpm, _conf in results:
        cluster = [(b, c) for b, c in results if abs(b - bpm) / bpm <= AGREEMENT_TOLERANCE]
        weight = sum(c for _b, c in cluster)
        if weight > best_weight:
            best_weight = weight
            best_cluster = cluster

    if len(best_cluster) >= 2:
        total_conf = sum(c for _b, c in best_cluster)
        combined = sum(b * c for b, c in best_cluster) / total_conf
        return combined, f"cluster_of_{len(best_cluster)}"

    # No two grabs agreed -- fall back to the single highest-confidence grab, not a blind
    # average (the earlier combiner's flaw: averaging unrelated/octave-split values produces
    # a number that's wrong for every reading).
    best_bpm, _best_conf = max(results, key=lambda r: r[1])
    return best_bpm, "fallback_highest_confidence"


def _analyze_one(args: tuple[str, float, float | None]) -> dict:
    path_str, true_bpm, duration = args
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from iron import tempo
    from iron.api import _decode

    row: dict = {
        "path": path_str, "true_bpm": true_bpm, "passes": [],
        "combined_bpm": None, "combined_method": None, "error": None,
    }
    if duration is None or duration < 10.0:
        row["error"] = "too short for 3 proportional passes"
        return row
    try:
        t0 = time.time()
        results = []
        for frac in POSITION_FRACTIONS:
            start = duration * frac
            y, sr = _decode(Path(path_str), GRAB_DURATION, start=start)
            outcome = tempo.detect_tempo(y, sr)
            if outcome is not None:
                results.append(outcome)
            row["passes"].append({"start": start, "bpm": outcome[0] if outcome else None,
                                   "conf": outcome[1] if outcome else None})
        row["elapsed_s"] = time.time() - t0
        combined_bpm, method = _combine(results)
        row["combined_bpm"] = combined_bpm
        row["combined_method"] = method
    except Exception as e:
        row["error"] = str(e)
    return row


def _mirex(detected: float | None, true_bpm: float) -> bool:
    return detected is not None and abs(detected - true_bpm) / true_bpm <= 0.04


def main() -> int:
    root = Path("/Volumes/Passport/DATABASE")
    count = 100
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
            for fut in as_completed(futures, timeout=max(60.0, len(work) * 10)):
                i += 1
                rows.append(fut.result())
                if i % 25 == 0 or i == len(work):
                    print(f"  [{i}/{len(work)}] elapsed={time.time() - t0:.0f}s", flush=True)
        except TimeoutError:
            stuck = True
            print("  timeout, moving on with what completed", flush=True)
    finally:
        pool.shutdown(wait=not stuck, cancel_futures=True)

    valid_rows = [r for r in rows if r["error"] is None]
    n = len(valid_rows)
    print(f"\n{'='*60}\nRESULTS -- n_valid={n} (of {len(rows)} attempted)\n{'='*60}")

    print("\n-- Each pass position alone --")
    for i, frac in enumerate(POSITION_FRACTIONS):
        ok = sum(1 for r in valid_rows
                 if len(r["passes"]) > i and _mirex(r["passes"][i]["bpm"], r["true_bpm"]))
        print(f"  pass {i+1} ({frac:.0%} through)  MIREX: {ok}/{n} ({ok/max(1,n):.1%})")

    print("\n-- Combined (confidence-weighted agreement) --")
    combined_ok = sum(1 for r in valid_rows if _mirex(r["combined_bpm"], r["true_bpm"]))
    print(f"  MIREX: {combined_ok}/{n} ({combined_ok/max(1,n):.1%})")

    methods: dict[str, int] = {}
    for r in valid_rows:
        methods[r["combined_method"]] = methods.get(r["combined_method"], 0) + 1
    print(f"  combine method breakdown: {methods}")

    print("\n-- Distance distribution (combined result vs. true BPM, relative error) --")
    errs = sorted(
        abs(r["combined_bpm"] - r["true_bpm"]) / r["true_bpm"]
        for r in valid_rows if r["combined_bpm"] is not None
    )
    if errs:
        within = lambda pct: sum(1 for e in errs if e <= pct) / len(errs)  # noqa: E731
        print(f"  within 1%:  {within(0.01):.1%}")
        print(f"  within 4% (MIREX): {within(0.04):.1%}")
        print(f"  within 10%: {within(0.10):.1%}")
        print(f"  within 25%: {within(0.25):.1%}")
        print(f"  median error: {errs[len(errs)//2]:.1%}")
        print(f"  mean error:   {sum(errs)/len(errs):.1%}")
        print(f"  worst error:  {errs[-1]:.1%}")

    times = [r["elapsed_s"] for r in valid_rows if "elapsed_s" in r]
    if times:
        print("\n-- Timing (3 passes, decode+detect only) --")
        print(f"  mean: {sum(times)/len(times):.2f}s/track  min={min(times):.2f}s  max={max(times):.2f}s")

    skipped = len(rows) - n
    if skipped:
        print(f"\n{skipped} tracks skipped (too short for 3 proportional passes)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
