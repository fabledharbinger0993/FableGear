#!/usr/bin/env python3
"""
Live head-to-head: Iron vs essentia's production RhythmExtractor2013 path
(audio_processor._detect_bpm_essentia), both scored against the SAME random sample of
real Rekordbox ground-truth BPMs, run right now rather than compared against the old
historical numbers in requirements_optional.txt.

Companion to scripts/benchmark_iron_tempo.py (which only runs Iron and prints those old
numbers for reference) -- this one runs BOTH detectors live, on the same sample, so drift
in essentia's own numbers over time/library composition doesn't confound the comparison.

Usage:
    python3 scripts/live_compare_iron_essentia.py
    python3 scripts/live_compare_iron_essentia.py --sample 300 --seed 7
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyrekordbox.db6 import tables

import audio_processor
import db_connection
import iron


def accuracy(pairs: list[tuple[float, float]]) -> dict[str, float]:
    if not pairs:
        return {"exact": 0.0, "within_1pct": 0.0, "mirex": 0.0}
    exact = sum(1 for d, t in pairs if abs(d - t) <= 0.6)
    within_1pct = sum(1 for d, t in pairs if abs(d - t) / t <= 0.01)
    mirex = sum(1 for d, t in pairs if abs(d - t) / t <= 0.04)
    n = len(pairs)
    return {"exact": exact / n, "within_1pct": within_1pct / n, "mirex": mirex / n}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sample", type=int, default=150, help="sample size (default: 150)")
    parser.add_argument("--seed", type=int, default=42, help="random seed (default: 42)")
    args = parser.parse_args(argv)

    print("Querying Rekordbox for candidate (path, bpm) rows...", flush=True)
    candidates = []
    with db_connection.read_db() as db:
        rows = db.query(tables.DjmdContent).with_entities(
            tables.DjmdContent.FolderPath, tables.DjmdContent.BPM
        )
        for folder_path, bpm in rows:
            if not folder_path or bpm is None or bpm <= 0:
                continue
            candidates.append((folder_path, bpm / 100.0))

    print(f"Candidate rows with a valid BPM: {len(candidates)}", flush=True)

    # Sample FIRST (cheap, in-memory), then only pay for filesystem .exists() checks on
    # the sample itself, not every row -- oversample since some sampled paths won't exist
    # on disk (moved/renamed/on an unmounted volume). Checking .exists() on all rows
    # before sampling was tried first and was drastically slower on a large library.
    rng = random.Random(args.seed)
    rng.shuffle(candidates)

    items: list[tuple[Path, float]] = []
    checked = 0
    for folder_path, true_bpm in candidates:
        checked += 1
        p = Path(folder_path)
        if p.exists():
            items.append((p, true_bpm))
        if len(items) >= args.sample:
            break

    print(f"Found {len(items)} existing files after checking {checked} candidates "
          f"(seed={args.seed})\n", flush=True)

    iron_pairs: list[tuple[float, float]] = []
    essentia_pairs: list[tuple[float, float]] = []
    iron_undetected = 0
    essentia_undetected = 0
    t_start = time.time()

    for i, (path, true_bpm) in enumerate(items, 1):
        t0 = time.time()
        try:
            result = iron.analyze(path, want=("bpm",))
            if result.bpm is not None:
                iron_pairs.append((result.bpm, true_bpm))
            else:
                iron_undetected += 1
        except Exception as e:
            iron_undetected += 1
            print(f"  iron error on {path.name}: {e}", file=sys.stderr)
        t1 = time.time()

        try:
            out = audio_processor._detect_bpm_essentia(path)
            if out is not None:
                essentia_pairs.append((out[0], true_bpm))
            else:
                essentia_undetected += 1
        except Exception as e:
            essentia_undetected += 1
            print(f"  essentia error on {path.name}: {e}", file=sys.stderr)
        t2 = time.time()

        print(f"  [{i}/{len(items)}] {path.name[:50]:50s} "
              f"iron={t1-t0:5.2f}s essentia={t2-t1:5.2f}s", flush=True)

    elapsed = time.time() - t_start
    iron_acc = accuracy(iron_pairs)
    essentia_acc = accuracy(essentia_pairs)

    print("\n" + "=" * 70)
    print(f"LIVE COMPARISON -- {len(items)}-track random sample, run just now")
    print("=" * 70)
    print(f"Total wall time: {elapsed:.1f}s\n")
    print(f"{'':20s} {'Iron':>12s} {'essentia (live)':>18s}")
    print(f"{'compared':20s} {len(iron_pairs):>12d} {len(essentia_pairs):>18d}")
    print(f"{'no result':20s} {iron_undetected:>12d} {essentia_undetected:>18d}")
    print(f"{'exact (0.6 BPM)':20s} {iron_acc['exact']:>11.1%} {essentia_acc['exact']:>17.1%}")
    print(f"{'within 1%':20s} {iron_acc['within_1pct']:>11.1%} {essentia_acc['within_1pct']:>17.1%}")
    print(f"{'MIREX (4%)':20s} {iron_acc['mirex']:>11.1%} {essentia_acc['mirex']:>17.1%}")
    print()
    print("Historical reference (12,687-track corpus, from requirements_optional.txt):")
    print("  essentia: exact 91.4%  within-1% 94.8%  MIREX 98.3%")
    print("  librosa:  exact 13.4%  within-1% 36.8%  MIREX 90.7%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
