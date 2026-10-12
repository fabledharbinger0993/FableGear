#!/usr/bin/env python3
"""
Benchmark Iron's key detector against real Rekordbox ground truth (DjmdContent.KeyName),
and A/B it against the previous (pre-§18: CQT chroma + Krumhansl-Kessler) detector in the same run -- an ablation, same
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

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import iron
from iron import key

# Krumhansl & Kessler (1982) profiles, used by iron/key.py before §18 -- kept here only so
# this script can A/B against the previous detector.
_KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

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

    print(f"Comparing {len(truth)} tracks (current detector vs. pre-SS18 CQT+KS detector)...\n",
          flush=True)

    new_pairs: list[tuple[str, str]] = []
    old_pairs: list[tuple[str, str]] = []
    undetected = 0

    for i, (path, true_camelot) in enumerate(truth, 1):
        try:
            new_result = iron.analyze(path, want=("initial_key",))
            new_key = new_result.initial_key
        except Exception as e:
            new_key = None
            print(f"  error on {path.name}: {e}", file=sys.stderr)

        # The pre-§18 detector: power CQT chroma + Krumhansl-Kessler profiles.
        with mock.patch.multiple(key, _chroma=key.dsp.chroma_cqt,
                                 PROFILE_MAJOR=_KS_MAJOR, PROFILE_MINOR=_KS_MINOR):
            try:
                old_result = iron.analyze(path, want=("initial_key",))
                old_key = old_result.initial_key
            except Exception:
                old_key = None

        if new_key is None and old_key is None:
            undetected += 1
            continue
        if new_key is not None:
            new_pairs.append((new_key, true_camelot))
        if old_key is not None:
            old_pairs.append((old_key, true_camelot))

        if i % 10 == 0 or i == len(truth):
            print(f"  [{i}/{len(truth)}]", flush=True)

    print("\n" + "=" * 60)
    print("IRON KEY BENCHMARK -- current vs. pre-SS18 (CQT chroma + KS profiles)")
    print("=" * 60)
    print(f"Compared: {len(truth)}   no result from either path: {undetected}")
    print(f"exact Camelot match -- current:        {_accuracy(new_pairs):.1%}  (n={len(new_pairs)})")
    print(f"exact Camelot match -- pre-SS18 CQT+KS: {_accuracy(old_pairs):.1%}  (n={len(old_pairs)})")
    print()
    print("Historical reference (docs/IRON_RESEARCH.md \xa72.1, 130-track sample):")
    print("  Iron (pre-CQT, linear chroma): 18.5% exact match vs Rekordbox")
    print("  librosa chroma_cqt (historical): 24.6% exact match vs Rekordbox")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
