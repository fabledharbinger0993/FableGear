"""Categorize Iron BPM disagreements with the ground-truth tag, by ratio and by source.

Input is the JSONL written by the genre-diverse / DB benchmark runs: one object per track
with at least `path`, `bpm`, `db_bpm`, `bpm_conf`. The ratio classes are the musically
meaningful octave and compound-meter relations (IRON_RESEARCH.md §2, §3); a track that
matches none of them is "other". The output shows, for each class, which folders and which
ground-truth values the disagreements cluster on, so a bad tag can be told apart from a
bad Iron answer. It does NOT decide which side is wrong: that needs Rekordbox's own beat
grid for the same file (see IRON_RESEARCH.md §19).

Usage:
    python scripts/categorize_iron_bpm_mismatches.py results.jsonl [--tolerance 0.04]
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

# (label, Iron BPM / ground-truth BPM). Ordered so the first hit wins.
_RATIO_CLASSES: tuple[tuple[str, float], ...] = (
    ("match", 1.0),
    ("2x", 2.0),
    ("half", 0.5),
    ("3:2", 1.5),
    ("2:3", 2 / 3),
    ("4:3", 4 / 3),
    ("3:4", 0.75),
)


def classify(iron_bpm: float, truth_bpm: float, tolerance: float) -> str:
    ratio = iron_bpm / truth_bpm
    for label, target in _RATIO_CLASSES:
        if abs(ratio / target - 1.0) <= tolerance:
            return label
    return "other"


def top_folder(path: str) -> str:
    marker = "/DATABASE/"
    rel = path.split(marker, 1)[1] if marker in path else path
    return rel.split("/", 1)[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--tolerance", type=float, default=0.04)
    parser.add_argument("--show", type=int, default=5, help="example rows per class")
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.jsonl.read_text().splitlines() if line.strip()]
    scored = [r for r in rows if r.get("bpm") and r.get("db_bpm")]
    classes: dict[str, list[dict]] = collections.defaultdict(list)
    for r in scored:
        classes[classify(r["bpm"], r["db_bpm"], args.tolerance)].append(r)

    n = len(scored)
    print(f"scored tracks: {n}")
    for label in ("match", "3:2", "2x", "half", "2:3", "3:4", "4:3", "other"):
        group = classes.get(label, [])
        if not group:
            continue
        folders = collections.Counter(top_folder(r["path"]) for r in group).most_common(4)
        truths = collections.Counter(r["db_bpm"] for r in group).most_common(5)
        print(f"\n{label:6} {len(group):5} ({100 * len(group) / n:5.1f}%)")
        print(f"  folders:          {folders}")
        print(f"  ground-truth BPM: {truths}")
        if label != "match":
            for r in group[: args.show]:
                print(f"  iron={r['bpm']:7.2f} tag={r['db_bpm']:7.2f} conf={r.get('bpm_conf', float('nan')):.2f}  {Path(r['path']).name[:70]}")


if __name__ == "__main__":
    main()
