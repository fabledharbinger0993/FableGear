#!/usr/bin/env python3
"""
Diagnose the <85 BPM catastrophic failure (12.5% MIREX at n=32, docs/IRON_RESEARCH.md §12/§13)
by ablating each of detect_tempo's correction passes independently.

Hypothesis under test: Iron's own octave-correction passes are systematically destroying
genuine slow tracks by pulling them up into the house band / core range. Every observed
failure in that bucket lands at ~1.5x or ~2x the true tempo, in 118-130 or 70-140 -- exactly
where _GENRE_BANDS and _CORE_RANGE_* would put them.

Ablations (each toggled independently, all sharing one decode per track so this stays cheap):
  none            -- production default, all passes on
  no_pass5        -- core-range tie-break off (_ENABLE_CORE_RANGE_TIEBREAK=False)
  no_lowband_fix  -- _in_low_band always False, so the low band shortcuts Pass 2 like any
                     other band instead of being forced to keep searching for a rival
  no_genre_band   -- _in_genre_band always True, so Pass 2's rival search never fires
  no_cyclic       -- cyclic-tempogram octave correction off (_CYCLIC_MARGIN=inf)
  raw_pass1       -- all of the above off at once: the honest unconditioned signal

Reports on the SLOW set (true <85 BPM, the failing population) and a CONTROL set
(true >=85 BPM) so a "fix" that helps slow tracks by wrecking everything else is visible
immediately rather than looking like a win.

Throwaway diagnostic script, not a proposed production change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from iron import tempo as tempo_module
from iron.api import _decode

_ORIG_IN_GENRE_BAND = tempo_module._in_genre_band
_ORIG_IN_LOW_BAND = tempo_module._in_low_band
_ORIG_CYCLIC_MARGIN = tempo_module._CYCLIC_MARGIN


def _reset() -> None:
    tempo_module._in_genre_band = _ORIG_IN_GENRE_BAND
    tempo_module._in_low_band = _ORIG_IN_LOW_BAND
    tempo_module._CYCLIC_MARGIN = _ORIG_CYCLIC_MARGIN
    tempo_module._ENABLE_CORE_RANGE_TIEBREAK = True


def _apply(mode: str) -> None:
    _reset()
    if mode in ("no_pass5", "raw_pass1"):
        tempo_module._ENABLE_CORE_RANGE_TIEBREAK = False
    if mode in ("no_lowband_fix", "raw_pass1"):
        tempo_module._in_low_band = lambda bpm: False
    if mode in ("no_genre_band", "raw_pass1"):
        tempo_module._in_genre_band = lambda bpm: True
    if mode in ("no_cyclic", "raw_pass1"):
        tempo_module._CYCLIC_MARGIN = float("inf")


def _mirex(d: float | None, t: float) -> bool:
    return d is not None and abs(d - t) / t <= 0.04


MODES = ("none", "no_pass5", "no_lowband_fix", "no_genre_band", "no_cyclic", "raw_pass1")


def main() -> int:
    jsonl = Path(sys.argv[1])
    rows = [json.loads(line) for line in jsonl.open()]
    rows = [r for r in rows if r.get("true_bpm") and Path(r["path"]).exists()]

    slow = [r for r in rows if r["true_bpm"] < 85.0]
    control = [r for r in rows if r["true_bpm"] >= 85.0]
    # keep the control set bounded -- it only needs to show regressions, not be exhaustive
    control = control[:120]
    print(f"slow set (true <85 BPM): n={len(slow)}   control set (>=85): n={len(control)}\n")

    decoded: dict[str, tuple] = {}
    for r in slow + control:
        try:
            decoded[r["path"]] = _decode(Path(r["path"]))
        except Exception:
            continue
    print(f"decoded {len(decoded)}/{len(slow) + len(control)} tracks once, reusing for all ablations\n")

    print(f"{'ablation':16s} {'slow MIREX':>18s} {'control MIREX':>18s}")
    results: dict[str, tuple] = {}
    for mode in MODES:
        _apply(mode)
        counts = {}
        for label, group in (("slow", slow), ("control", control)):
            ok = n = 0
            for r in group:
                dec = decoded.get(r["path"])
                if dec is None:
                    continue
                try:
                    outcome = tempo_module.detect_tempo(dec[0], dec[1])
                except Exception:
                    continue
                n += 1
                if outcome and _mirex(outcome[0], r["true_bpm"]):
                    ok += 1
            counts[label] = (ok, n)
        results[mode] = counts
        s_ok, s_n = counts["slow"]
        c_ok, c_n = counts["control"]
        print(f"  {mode:14s} {s_ok:4d}/{s_n:<4d} ({s_ok/max(1,s_n):5.1%})  "
              f"{c_ok:4d}/{c_n:<4d} ({c_ok/max(1,c_n):5.1%})")
    _reset()

    base_s = results["none"]["slow"]
    base_c = results["none"]["control"]
    print("\ndelta vs production default (positive = ablating that pass HELPS):")
    for mode in MODES[1:]:
        s_ok, s_n = results[mode]["slow"]
        c_ok, c_n = results[mode]["control"]
        ds = s_ok / max(1, s_n) - base_s[0] / max(1, base_s[1])
        dc = c_ok / max(1, c_n) - base_c[0] / max(1, base_c[1])
        print(f"  {mode:14s} slow {ds:+6.1%}   control {dc:+6.1%}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
