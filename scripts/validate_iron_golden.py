#!/usr/bin/env python3
"""Validate docs/iron/golden/iron_golden_labels.jsonl."""
import json
import sys
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "docs/iron/golden/iron_golden_labels.jsonl"
VERDICTS = {"correct", "wrong_half_time", "wrong_two_thirds"}
CONVENTIONS = {"drum_pulse", "felt_tempo"}


def main() -> int:
    errors, seen = [], set()
    for n, line in enumerate(PATH.read_text().splitlines(), 1):
        if not line.strip():
            continue
        r = json.loads(line)
        lo, hi = r.get("truth_bpm_min"), r.get("truth_bpm_max")
        if not r.get("track") or r["track"] in seen:
            errors.append(f"line {n}: missing or duplicate track")
        seen.add(r.get("track"))
        if not (isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and 0 < lo <= hi):
            errors.append(f"line {n}: bad BPM range")
        if r.get("iron_verdict") not in VERDICTS:
            errors.append(f"line {n}: bad iron_verdict")
        if r.get("convention") not in CONVENTIONS:
            errors.append(f"line {n}: bad convention")
    for e in errors:
        print(e)
    print(f"{len(seen)} labels, {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
