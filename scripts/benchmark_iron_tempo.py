#!/usr/bin/env python3
"""
Benchmark Iron's tempo detector against real ground truth.

Not a pytest suite -- a standalone report, the same role
`requirements_optional.txt`'s essentia-vs-librosa numbers played (91.4% / 13.4% exact-BPM
agreement against 12,687 real Rekordbox ground-truth beat grids). This script reproduces the
same three metrics against whatever ground truth is available, so there's a real, current
number to weigh against those before Iron is trusted as FableGear's primary tempo path -- see
the plan's validate-first rollout gate. It does not assume Iron will hit essentia's numbers;
it exists to find out.

Ground truth source, in priority order:

  1. A live Rekordbox database (--rekordbox-db, or the default path db_connection.py already
     knows) -- DjmdContent.BPM for every track FableGear can also locate on disk. This is the
     same kind of source the original essentia/librosa comparison used.
  2. A CSV of "path,true_bpm" pairs (--csv), for a benchmark set assembled some other way.

Usage:
    python3 scripts/benchmark_iron_tempo.py --rekordbox-db /path/to/master.db
    python3 scripts/benchmark_iron_tempo.py --csv ground_truth.csv
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import iron


def _ground_truth_from_rekordbox(db_path: Path | None) -> dict[Path, float]:
    from pyrekordbox.db6 import tables

    import db_connection

    truth: dict[Path, float] = {}
    with db_connection.read_db(db_path) as db:
        rows = db.query(tables.DjmdContent).with_entities(
            tables.DjmdContent.FolderPath, tables.DjmdContent.BPM
        )
        for folder_path, bpm in rows:
            if not folder_path or bpm is None or bpm <= 0:
                continue
            path = Path(folder_path)
            if path.exists():
                truth[path] = bpm / 100.0  # DjmdContent.BPM is centi-BPM
    return truth


def _ground_truth_from_csv(csv_path: Path) -> dict[Path, float]:
    truth: dict[Path, float] = {}
    with csv_path.open(newline="") as f:
        for row in csv.reader(f):
            if not row or row[0].strip().lower() == "path":
                continue
            path_str, bpm_str = row[0], row[1]
            path = Path(path_str)
            if path.exists():
                truth[path] = float(bpm_str)
    return truth


def _accuracy(pairs: list[tuple[float, float]]) -> dict[str, float]:
    """(detected, true) pairs -> the same three metrics requirements_optional.txt reports."""
    if not pairs:
        return {"exact": 0.0, "within_1pct": 0.0, "mirex": 0.0}
    exact = sum(1 for d, t in pairs if abs(d - t) <= 0.6)
    within_1pct = sum(1 for d, t in pairs if abs(d - t) / t <= 0.01)
    mirex = sum(1 for d, t in pairs if abs(d - t) / t <= 0.04)
    n = len(pairs)
    return {"exact": exact / n, "within_1pct": within_1pct / n, "mirex": mirex / n}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--rekordbox-db", type=Path, default=None,
                         help="path to a Rekordbox master.db (default: db_connection's own default)")
    parser.add_argument("--csv", type=Path, default=None,
                         help="CSV of path,true_bpm pairs, instead of a Rekordbox DB")
    parser.add_argument("--limit", type=int, default=None,
                         help="stop after N tracks (useful for a quick check)")
    parser.add_argument("--bpm-min", type=float, default=30.0,
                         help="lower bound passed to iron.analyze() (default: 30.0)")
    parser.add_argument("--bpm-max", type=float, default=300.0,
                         help="upper bound passed to iron.analyze() (default: 300.0)")
    parser.add_argument("--sample", type=int, default=None,
                         help="take a random sample of this size before --limit truncates it "
                              "(matches the 'random 300-track sample' methodology this "
                              "script's own historical reference numbers used, rather than "
                              "just the first N tracks in database order)")
    parser.add_argument("--seed", type=int, default=42,
                         help="random seed for --sample, for a reproducible run (default: 42)")
    args = parser.parse_args(argv)

    if args.csv:
        truth = _ground_truth_from_csv(args.csv)
        source = f"CSV ({args.csv})"
    else:
        try:
            truth = _ground_truth_from_rekordbox(args.rekordbox_db)
        except Exception as exc:
            print(f"error: could not read a Rekordbox database ({exc}). "
                  f"Pass --csv instead.", file=sys.stderr)
            return 2
        source = "Rekordbox database"

    if not truth:
        print("error: no ground-truth (path, bpm) pairs found.", file=sys.stderr)
        return 2

    items = list(truth.items())
    if args.sample:
        rng = random.Random(args.seed)
        items = rng.sample(items, min(args.sample, len(items)))
    if args.limit:
        items = items[: args.limit]

    print(f"Ground truth: {len(items)} tracks from {source}")
    print("Running iron.analyze() on each ... (this decodes real audio, expect it to take a while)\n")

    pairs: list[tuple[float, float]] = []
    undetected = 0
    for i, (path, true_bpm) in enumerate(items, 1):
        result = iron.analyze(path, want=("bpm",), bpm_min=args.bpm_min, bpm_max=args.bpm_max)
        if result.bpm is not None:
            pairs.append((result.bpm, true_bpm))
        else:
            undetected += 1
        if i % 50 == 0:
            print(f"  ... {i}/{len(items)}", file=sys.stderr)

    acc = _accuracy(pairs)
    print("\n" + "=" * 62)
    print("IRON TEMPO BENCHMARK")
    print("=" * 62)
    print(f"Ground truth tracks:     {len(items)}")
    print(f"Iron found no tempo for: {undetected}")
    print(f"Compared:                {len(pairs)}")
    print()
    print(f"  exact (within 0.6 BPM)   {acc['exact']:.1%}")
    print(f"  within 1%                {acc['within_1pct']:.1%}")
    print(f"  MIREX (within 4%)        {acc['mirex']:.1%}")
    print()
    print("For reference, requirements_optional.txt's historical numbers on a 300-track")
    print("sample (12,687 total ground-truth grids), essentia -> librosa:")
    print("  exact  91.4% -> 13.4%   within-1%  94.8% -> 36.8%   MIREX  98.3% -> 90.7%")
    print()
    print("This number is not a pass/fail gate by itself -- it's the input to the")
    print("validate-first decision on whether Iron is ready to become FableGear's")
    print("primary tempo path. essentia and librosa stay in requirements.txt until then.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
