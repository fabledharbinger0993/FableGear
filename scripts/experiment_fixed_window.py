#!/usr/bin/env python3
"""
Experiment: how well does tempo/key detection do on a tiny, FIXED window (e.g. start=45s,
duration=6s) instead of iron.api's body window or a whole-track decode?

Reuses scripts/benchmark_iron_genre_diverse.py's scanning + stratified-sampling machinery
(genre-diverse, tag-based ground truth -- see that script's own docstring for why), but
analyzes each track with a fixed (--start, --duration) window via iron.dsp/iron.tempo/
iron.key directly, bypassing iron.api.analyze()'s own windowing entirely -- this is a
throwaway diagnostic, not a proposed production change.

Usage:
    python3 scripts/experiment_fixed_window.py --root /Volumes/Passport/DATABASE \\
        --count 2000 --start 45 --duration 6
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark_iron_genre_diverse import _scan_candidates


def _analyze_fixed_window(args: tuple[str, float, str | None, str, float, float]) -> dict:
    path_str, true_bpm, true_camelot, genre, start, duration = args
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from iron import key as key_detect
    from iron import tempo as tempo_detect
    from iron.api import _decode

    result = {
        "path": path_str, "genre": genre, "true_bpm": true_bpm, "true_camelot": true_camelot,
        "detected_bpm": None, "detected_camelot": None, "error": None,
    }
    try:
        y, sr = _decode(Path(path_str), duration, start=start)
        tempo_outcome = tempo_detect.detect_tempo(y, sr)
        if tempo_outcome is not None:
            result["detected_bpm"] = tempo_outcome[0]
        key_outcome = key_detect.detect_key(y, sr)
        if key_outcome is not None:
            result["detected_camelot"] = key_outcome[0]
    except Exception as e:
        result["error"] = str(e)
    return result


def _accuracy(rows: list[dict]) -> dict[str, float]:
    pairs = [(r["detected_bpm"], r["true_bpm"]) for r in rows if r["detected_bpm"] is not None]
    if not pairs:
        return {"n": 0, "exact": 0.0, "within_1pct": 0.0, "mirex": 0.0}
    exact = sum(1 for d, t in pairs if abs(d - t) <= 0.6)
    within_1pct = sum(1 for d, t in pairs if abs(d - t) / t <= 0.01)
    mirex = sum(1 for d, t in pairs if abs(d - t) / t <= 0.04)
    n = len(pairs)
    return {"n": n, "exact": exact / n, "within_1pct": within_1pct / n, "mirex": mirex / n}


def _key_accuracy(rows: list[dict]) -> dict[str, float]:
    pairs = [
        (r["detected_camelot"], r["true_camelot"])
        for r in rows if r["detected_camelot"] is not None and r["true_camelot"] is not None
    ]
    if not pairs:
        return {"n": 0, "exact": 0.0}
    exact = sum(1 for d, t in pairs if d == t)
    return {"n": len(pairs), "exact": exact / len(pairs)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--count", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start", type=float, default=45.0, help="seconds into the file to start")
    parser.add_argument("--duration", type=float, default=6.0, help="seconds of audio to decode")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--scan-limit", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    # At this sample size, no need for stratified sampling or scan headroom -- just take the
    # first `count` tracks with a valid BPM tag as they're found. scan_limit=count means the
    # scan stops as soon as there's enough, not 6x over.
    scan_limit = args.scan_limit or args.count
    print(f"Scanning {args.root} (scan_limit={scan_limit}, {args.workers} workers)...", flush=True)
    t0 = time.time()
    candidates = _scan_candidates(args.root, scan_limit=scan_limit, workers=args.workers, seed=args.seed)
    print(f"Found {len(candidates)} candidates in {time.time() - t0:.0f}s", flush=True)

    sample = candidates[: args.count]
    by_genre = defaultdict(int)
    for _p, _b, _k, g, _d in sample:
        by_genre[g] += 1
    print(f"Sampled {len(sample)} tracks across {len(by_genre)} genres", flush=True)

    # Skip tracks too short for the requested window -- not a fair test of the window itself.
    work = [
        (str(p), b, k, g, args.start, args.duration)
        for p, b, k, g, d in sample
        if d is None or d >= args.start + args.duration
    ]
    skipped = len(sample) - len(work)
    print(f"Testing window start={args.start}s duration={args.duration}s on {len(work)} tracks "
          f"({skipped} too short for this window, skipped)\n", flush=True)

    rows: list[dict] = []
    t0 = time.time()
    out_f = args.out.open("w") if args.out else None
    per_track_budget_s = 30.0
    overall_timeout = max(60.0, (len(work) / max(1, args.workers)) * per_track_budget_s + 60.0)
    pool = ProcessPoolExecutor(max_workers=args.workers)
    try:
        futures = {pool.submit(_analyze_fixed_window, w): w for w in work}
        i = 0
        stuck_timeout = False
        try:
            for fut in as_completed(futures, timeout=overall_timeout):
                i += 1
                row = fut.result()
                rows.append(row)
                if out_f:
                    out_f.write(json.dumps(row) + "\n")
                    out_f.flush()
                if i % 100 == 0 or i == len(work):
                    print(f"  [{i}/{len(work)}] elapsed={time.time() - t0:.0f}s", flush=True)
        except TimeoutError:
            stuck_timeout = True
            stuck = [w for fut, w in futures.items() if not fut.done()]
            print(f"\n  overall timeout hit, {len(stuck)} track(s) still running -- moving on",
                  flush=True)
    finally:
        if out_f:
            out_f.close()
        pool.shutdown(wait=not stuck_timeout, cancel_futures=True)

    print("\n" + "=" * 70)
    print(f"FIXED-WINDOW EXPERIMENT -- start={args.start}s duration={args.duration}s, "
          f"{len(rows)} tracks")
    print("=" * 70)
    tempo_acc = _accuracy(rows)
    key_acc = _key_accuracy(rows)
    print(f"Overall tempo (n={tempo_acc['n']}): exact {tempo_acc['exact']:.1%}  "
          f"within-1% {tempo_acc['within_1pct']:.1%}  MIREX {tempo_acc['mirex']:.1%}")
    print(f"Overall key   (n={key_acc['n']}): exact Camelot match {key_acc['exact']:.1%}")
    print(f"Total elapsed: {time.time() - t0:.0f}s ({(time.time() - t0) / max(1, len(work)):.2f}s/track)")

    print("\nPer-genre tempo accuracy (genres with >= 15 compared tracks):")
    by_genre_rows = defaultdict(list)
    for r in rows:
        by_genre_rows[r["genre"]].append(r)
    for g, g_rows in sorted(by_genre_rows.items(), key=lambda kv: -len(kv[1])):
        acc = _accuracy(g_rows)
        if acc["n"] >= 15:
            print(f"  {g:30s} n={acc['n']:4d}  exact={acc['exact']:.1%}  "
                  f"within1%={acc['within_1pct']:.1%}  mirex={acc['mirex']:.1%}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
