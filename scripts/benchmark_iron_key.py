#!/usr/bin/env python3
"""
Benchmark Iron's key detector against real Rekordbox ground truth (DjmdContent.KeyName),
and A/B it against the pre-CQT linear-chroma path in the same run -- an ablation, same
pattern as scripts/ablate_genre_bands.py, so drift in library composition between two
separate runs can't confound the before/after comparison.

Rekordbox stores KeyName in traditional notation (e.g. "F#m", "Bb", "Dbm"), not Camelot;
this script normalizes both to Camelot before comparing.

Usage:
    python3 scripts/benchmark_iron_key.py --rekordbox-db /path/to/master.db
    python3 scripts/benchmark_iron_key.py --rekordbox-db /path/to/master.db --sample 300
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import iron
from iron import key

_ENHARMONIC = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}


def _parse_rekordbox_key(raw: str) -> str | None:
    """Rekordbox KeyName ("F#m", "Bb", "Dbm", ...) -> Camelot, or None if unparseable."""
    raw = raw.strip()
    if not raw:
        return None
    if raw.endswith("m"):
        note, mode = raw[:-1], "min"
    else:
        note, mode = raw, "maj"
    note = _ENHARMONIC.get(note, note)
    if note not in key.NOTES:
        return None
    return key.CAMELOT.get(note + mode)


def _accuracy(pairs: list[tuple[str, str]]) -> float:
    """Exact Camelot match rate."""
    if not pairs:
        return 0.0
    return sum(1 for d, t in pairs if d == t) / len(pairs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--rekordbox-db", type=Path, default=None,
                         help="path to a Rekordbox master.db (default: this user's "
                              "configured LOCAL_DB)")
    parser.add_argument("--sample", type=int, default=None, help="random sample size")
    parser.add_argument("--seed", type=int, default=42, help="random seed for --sample")
    parser.add_argument("--limit", type=int, default=None, help="stop after N tracks")
    args = parser.parse_args(argv)

    from pyrekordbox.db6 import tables

    import db_connection

    print("Querying Rekordbox for candidate (path, key) rows...", flush=True)
    truth: list[tuple[Path, str]] = []
    with db_connection.read_db(args.rekordbox_db) as db:
        for row in db.query(tables.DjmdContent):
            if not row.FolderPath:
                continue
            try:
                raw_key = row.KeyName
            except Exception:
                continue
            if not raw_key:
                continue
            camelot = _parse_rekordbox_key(raw_key)
            if camelot is None:
                continue
            path = Path(row.FolderPath)
            if path.exists():
                truth.append((path, camelot))

    print(f"Ground truth tracks (valid key, file exists on disk): {len(truth)}", flush=True)

    if args.sample:
        rng = random.Random(args.seed)
        truth = rng.sample(truth, min(args.sample, len(truth)))
    if args.limit:
        truth = truth[: args.limit]

    print(f"Comparing {len(truth)} tracks (CQT chroma vs. pre-CQT linear chroma)...\n",
          flush=True)

    cqt_pairs: list[tuple[str, str]] = []
    linear_pairs: list[tuple[str, str]] = []
    undetected = 0

    for i, (path, true_camelot) in enumerate(truth, 1):
        try:
            cqt_result = iron.analyze(path, want=("initial_key",))
            cqt_key = cqt_result.initial_key
        except Exception as e:
            cqt_key = None
            print(f"  error on {path.name}: {e}", file=sys.stderr)

        with mock.patch.object(key.dsp, "chroma_cqt", key.dsp.chroma):
            try:
                linear_result = iron.analyze(path, want=("initial_key",))
                linear_key = linear_result.initial_key
            except Exception:
                linear_key = None

        if cqt_key is None and linear_key is None:
            undetected += 1
            continue
        if cqt_key is not None:
            cqt_pairs.append((cqt_key, true_camelot))
        if linear_key is not None:
            linear_pairs.append((linear_key, true_camelot))

        if i % 10 == 0 or i == len(truth):
            print(f"  [{i}/{len(truth)}]", flush=True)

    print("\n" + "=" * 60)
    print("IRON KEY BENCHMARK -- CQT chroma vs. pre-CQT linear chroma")
    print("=" * 60)
    print(f"Compared: {len(truth)}   no result from either path: {undetected}")
    print(f"exact Camelot match -- CQT chroma:    {_accuracy(cqt_pairs):.1%}  (n={len(cqt_pairs)})")
    print(f"exact Camelot match -- linear chroma: {_accuracy(linear_pairs):.1%}  (n={len(linear_pairs)})")
    print()
    print("Historical reference (docs/IRON_RESEARCH.md \xa72.1, 130-track sample):")
    print("  Iron (pre-CQT, linear chroma): 18.5% exact match vs Rekordbox")
    print("  librosa chroma_cqt (historical): 24.6% exact match vs Rekordbox")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
