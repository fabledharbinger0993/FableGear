#!/usr/bin/env python3
"""
Does stacking energy_flux WITH the tiered architecture actually do anything, and does the
order matter?

energy_flux isn't a separate pass that runs before/after tiered -- it's an onset FEATURE
that feeds detect_tempo's scoring, while tiered is an ARCHITECTURE that decides how many
detect_tempo calls to make and how to combine them. So "flux first vs tiered first" is
really: does tiered's benefit compound with energy_flux's, or do they overlap/cancel?

Tested as a 2x2: {energy_flux off, on} x {single grab, tiered escalation}. Stages share
state in memory (no tag-writing -- iron is read-only by design, and in-memory passing tests
the identical hypothesis without touching real files).

Throwaway diagnostic script, not a proposed production tool.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from iron import tempo as tempo_module
from iron.api import _decode

_TIERED_CONF_THRESHOLD = 0.75
_GRAB_SECONDS = 2.0


def _cluster_combine(results: list[tuple[float, float]]) -> float:
    best_cluster, best_weight = [], -1.0
    for b, _c in results:
        cluster = [(bb, cc) for bb, cc in results if abs(bb - b) / b <= 0.02]
        w = sum(cc for _bb, cc in cluster)
        if w > best_weight:
            best_weight, best_cluster = w, cluster
    if len(best_cluster) >= 2:
        total_c = sum(cc for _bb, cc in best_cluster)
        return sum(bb * cc for bb, cc in best_cluster) / total_c
    return max(results, key=lambda r: r[1])[0]


def single_grab(path: Path, duration: float) -> float | None:
    y, sr = _decode(path, _GRAB_SECONDS, start=duration * 0.1318)
    outcome = tempo_module.detect_tempo(y, sr)
    return outcome[0] if outcome else None


def tiered(path: Path, duration: float) -> tuple[float | None, bool]:
    """Returns (bpm, escalated)."""
    y, sr = _decode(path, _GRAB_SECONDS, start=duration * 0.1318)
    outcome = tempo_module.detect_tempo(y, sr)
    if outcome is not None and outcome[1] >= _TIERED_CONF_THRESHOLD:
        return outcome[0], False
    results = []
    for frac in (0.10, 0.35, 0.60):
        try:
            y2, sr2 = _decode(path, _GRAB_SECONDS, start=duration * frac)
            o2 = tempo_module.detect_tempo(y2, sr2)
            if o2 is not None:
                results.append(o2)
        except Exception:
            continue
    if not results:
        return (outcome[0] if outcome else None), True
    return _cluster_combine(results), True


def _mirex(detected, true_bpm) -> bool:
    return detected is not None and abs(detected - true_bpm) / true_bpm <= 0.04


def main() -> int:
    with open(sys.argv[1]) as f:
        sets = json.load(f)
    groups = {"house": sets["house"], "disco": sets["disco"],
              "ALL": sets["house"] + sets["disco"]}

    flux_weights = [0.0, 0.5, 2.0]
    print(f"2x2: energy_flux weight x architecture, grab={_GRAB_SECONDS}s\n")

    for group_name, tracks in groups.items():
        print(f"--- {group_name} (n={len(tracks)}) ---")
        for weight in flux_weights:
            tempo_module._ENERGY_FLUX_WEIGHT = weight
            for arch in ("single", "tiered"):
                correct, n, escalations = 0, 0, 0
                times = []
                for t in tracks:
                    path = Path(t["path"])
                    t0 = time.time()
                    try:
                        if arch == "single":
                            detected = single_grab(path, t["dur"])
                        else:
                            detected, esc = tiered(path, t["dur"])
                            if esc:
                                escalations += 1
                    except Exception:
                        continue
                    times.append(time.time() - t0)
                    n += 1
                    if _mirex(detected, t["bpm"]):
                        correct += 1
                extra = f"  escalated={escalations}/{n}" if arch == "tiered" else ""
                print(f"  flux_w={weight:4.1f}  {arch:7s}  MIREX: {correct}/{n} "
                      f"({correct/max(1,n):.1%})  mean_time={sum(times)/max(1,len(times)):.2f}s{extra}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
