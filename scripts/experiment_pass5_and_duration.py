#!/usr/bin/env python3
"""
One-off combined experiment: (1) does Pass 5 (core-range octave tie-break) help or hurt at
real scale, and (2) does a shorter fixed-window duration (2/3/4s vs 6s) at start=120s change
accuracy. Throwaway diagnostic script, not a proposed production tool.
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark_iron_genre_diverse import _scan_candidates

START = 120.0
DURATIONS = (2.0, 3.0, 4.0, 6.0)


def _analyze_one(args: tuple[str, float]) -> dict:
    path_str, true_bpm = args
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from iron import tempo
    from iron.api import _decode

    row = {"path": path_str, "true_bpm": true_bpm, "error": None}
    try:
        for d in DURATIONS:
            y, sr = _decode(Path(path_str), d, start=START)
            outcome = tempo.detect_tempo(y, sr)
            row[f"on_{d}"] = outcome[0] if outcome else None
        # Pass 5 OFF, at the reference 6s window only
        y, sr = _decode(Path(path_str), 6.0, start=START)
        tempo._ENABLE_CORE_RANGE_TIEBREAK = False
        outcome = tempo.detect_tempo(y, sr)
        tempo._ENABLE_CORE_RANGE_TIEBREAK = True
        row["off_6.0"] = outcome[0] if outcome else None
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

    work = [(str(p), b) for p, b, _k, _g, _d in candidates[:count]]
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

    print(f"\n{'='*60}\nRESULTS -- n={len(rows)}\n{'='*60}")

    print("\n-- Pass 5 A/B at 6s window --")
    on_ok = sum(1 for r in rows if _mirex(r.get("on_6.0"), r["true_bpm"]))
    off_ok = sum(1 for r in rows if _mirex(r.get("off_6.0"), r["true_bpm"]))
    flips = sum(
        1 for r in rows
        if r.get("on_6.0") is not None and r.get("off_6.0") is not None
        and abs(r["on_6.0"] - r["off_6.0"]) > 0.5
    )
    n = len(rows)
    print(f"Pass5 ON  MIREX: {on_ok}/{n} ({on_ok/n:.1%})")
    print(f"Pass5 OFF MIREX: {off_ok}/{n} ({off_ok/n:.1%})")
    print(f"tracks where Pass5 changed the answer: {flips}/{n}")

    print("\n-- Duration sensitivity (Pass5 ON, start=120s) --")
    for d in DURATIONS:
        key = f"on_{d}"
        ok = sum(1 for r in rows if _mirex(r.get(key), r["true_bpm"]))
        print(f"  {d:>4.1f}s  MIREX: {ok}/{n} ({ok/n:.1%})")

    errors = sum(1 for r in rows if r["error"])
    if errors:
        print(f"\n{errors} tracks errored")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
