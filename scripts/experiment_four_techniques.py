#!/usr/bin/env python3
"""
Test 4 candidate techniques from external AI consultation (docs/IRON_RESEARCH.md), each in
ISOLATION (not stacked on the existing production pipeline), to measure each one's own
accuracy and time cost individually before considering combining them.

1. log_gaussian  -- smooth log-normal tempo prior (generic center=120bpm) instead of
   discrete genre bands.
2. energy_window -- scan several candidate short windows, pick whichever has the most
   rhythmic (onset) energy, analyze only that one.
3. genre_prior   -- use the TRACK'S OWN genre tag as a per-track expected-tempo center,
   gated on real score evidence (lesson from today's Pass-5 core-range-tiebreak revert:
   never flip unconditionally).
4. tiered        -- cheap single grab first; only escalate to a 3-window consensus pass
   when confidence is low.

Throwaway diagnostic script, not a proposed production tool.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from iron import dsp
from iron.api import _decode
from iron.tempo import _combined_score  # reuse existing scoring primitives

_OCTAVE_RATIOS = (1.0, 2.0, 0.5, 1.5, 2.0 / 3.0)
_BPM_MIN, _BPM_MAX = 60.0, 180.0
_HOP = 512

_GENRE_CENTERS = {
    "house": 124.0, "disco": 115.0, "nu disco": 118.0, "soul": 108.0, "funk": 108.0,
}


def _lag_range(frame_rate: float, acf_len: int) -> tuple[int, int]:
    lo = max(1, int(frame_rate * 60.0 / _BPM_MAX))
    hi = min(acf_len - 1, int(frame_rate * 60.0 / _BPM_MIN))
    return lo, hi


def _raw_pass1(y: np.ndarray, sr: int) -> tuple[int, np.ndarray, np.ndarray, float] | None:
    """Shared setup: broadband+multiband acf, raw Pass-1 winner (no corrections at all).
    Returns (best_lag, acf, band_acfs, frame_rate) or None."""
    env = dsp.onset_envelope(y, sr, hop_length=_HOP)
    if env.shape[0] < 8 or not np.any(env):
        return None
    acf = dsp.autocorrelate(env - env.mean())
    frame_rate = sr / _HOP
    band_envs = dsp.onset_envelope_multiband(y, sr, hop_length=_HOP)
    band_acfs = np.array([dsp.autocorrelate(r - r.mean()) for r in band_envs])
    lag_lo, lag_hi = _lag_range(frame_rate, acf.shape[0])
    if lag_hi <= lag_lo:
        return None
    best_lag, best_score = None, -np.inf
    for lag in range(lag_lo, lag_hi + 1):
        if acf[lag] <= 0:
            continue
        s = _combined_score(acf, band_acfs, lag)
        if s > best_score:
            best_score, best_lag = s, lag
    if best_lag is None:
        return None
    return best_lag, acf, band_acfs, frame_rate


def technique_log_gaussian(y: np.ndarray, sr: int, genre: str, center: float = 120.0, sigma: float = 0.5):
    setup = _raw_pass1(y, sr)
    if setup is None:
        return None
    best_lag, acf, band_acfs, frame_rate = setup
    lag_lo, lag_hi = _lag_range(frame_rate, acf.shape[0])
    best_weighted, best_final_lag = -np.inf, best_lag
    for ratio in _OCTAVE_RATIOS:
        lag = round(best_lag * ratio) if ratio != 1.0 else best_lag
        # also just scan the raw candidate directly via the ratio table around best_lag
        if lag < lag_lo or lag > lag_hi:
            continue
        bpm = frame_rate * 60.0 / lag
        weight = math.exp(-((math.log2(bpm / center)) ** 2) / (2 * sigma ** 2))
        score = _combined_score(acf, band_acfs, lag) * weight
        if score > best_weighted:
            best_weighted, best_final_lag = score, lag
    return frame_rate * 60.0 / best_final_lag


def technique_energy_window(path: Path, duration: float, genre: str):
    """Scan candidate windows for onset energy, decode+analyze only the winner."""
    candidates_frac = (0.10, 0.25, 0.40, 0.55, 0.70, 0.85)
    best_energy, best_start = -1.0, duration * 0.10
    for frac in candidates_frac:
        start = duration * frac
        try:
            y, sr = _decode(path, 1.0, start=start)
        except Exception:
            continue
        env = dsp.onset_envelope(y, sr, hop_length=_HOP)
        energy = float(np.sum(env))
        if energy > best_energy:
            best_energy, best_start = energy, start
    y, sr = _decode(path, 2.0, start=best_start)
    setup = _raw_pass1(y, sr)
    if setup is None:
        return None
    best_lag, _acf, _band_acfs, frame_rate = setup
    return frame_rate * 60.0 / best_lag


def technique_genre_prior(y: np.ndarray, sr: int, genre: str, rival_threshold: float = 0.5):
    setup = _raw_pass1(y, sr)
    if setup is None:
        return None
    best_lag, acf, band_acfs, frame_rate = setup
    center = None
    for key, c in _GENRE_CENTERS.items():
        if key in genre:
            center = c
            break
    if center is None:
        return frame_rate * 60.0 / best_lag  # no prior available, just use raw pass1

    best_score = _combined_score(acf, band_acfs, best_lag)
    best_bpm = frame_rate * 60.0 / best_lag
    lag_lo, lag_hi = _lag_range(frame_rate, acf.shape[0])
    best_dist = abs(math.log2(best_bpm / center))
    chosen_lag = best_lag
    for ratio in _OCTAVE_RATIOS[1:]:
        lag = round(best_lag * ratio)
        if lag < lag_lo or lag > lag_hi:
            continue
        bpm = frame_rate * 60.0 / lag
        dist = abs(math.log2(bpm / center))
        score = _combined_score(acf, band_acfs, lag)
        # GATED, per today's earlier lesson: only prefer the genre-closer candidate if it's
        # still a real, scoring rival, not just numerically closer to the genre center.
        if dist < best_dist and score >= rival_threshold * best_score:
            best_dist = dist
            chosen_lag = lag
    return frame_rate * 60.0 / chosen_lag


def technique_tiered(path: Path, duration: float, genre: str, confidence_threshold: float = 0.75):
    from iron import tempo as tempo_module

    start1 = duration * 0.1318
    y, sr = _decode(path, 2.0, start=start1)
    outcome = tempo_module.detect_tempo(y, sr)
    if outcome is not None and outcome[1] >= confidence_threshold:
        return outcome[0], False  # fast path, no escalation

    # Escalate: 3-window proportional consensus (today's earlier validated combiner)
    results = []
    for frac in (0.10, 0.35, 0.60):
        try:
            y2, sr2 = _decode(path, 2.0, start=duration * frac)
            o2 = tempo_module.detect_tempo(y2, sr2)
            if o2 is not None:
                results.append(o2)
        except Exception:
            continue
    if not results:
        return (outcome[0] if outcome else None), True
    best_cluster, best_weight = [], -1.0
    for bpm, _c in results:
        cluster = [(b, c) for b, c in results if abs(b - bpm) / bpm <= 0.02]
        w = sum(c for _b, c in cluster)
        if w > best_weight:
            best_weight, best_cluster = w, cluster
    if len(best_cluster) >= 2:
        total_c = sum(c for _b, c in best_cluster)
        return sum(b * c for b, c in best_cluster) / total_c, True
    best_bpm, _bc = max(results, key=lambda r: r[1])
    return best_bpm, True


def _mirex(detected, true_bpm) -> bool:
    return detected is not None and abs(detected - true_bpm) / true_bpm <= 0.04


def run_set(name: str, tracks: list[dict]) -> None:
    print(f"\n{'='*70}\n{name}  (n={len(tracks)})\n{'='*70}")

    for technique_name in ("log_gaussian", "energy_window", "genre_prior", "tiered"):
        correct = 0
        n = 0
        times = []
        escalated = 0
        for t in tracks:
            path = Path(t["path"])
            true_bpm = t["bpm"]
            genre = t["genre"]
            duration = t["dur"]
            t0 = time.time()
            try:
                if technique_name == "log_gaussian":
                    y, sr = _decode(path, 2.0, start=duration * 0.1318)
                    detected = technique_log_gaussian(y, sr, genre)
                elif technique_name == "energy_window":
                    detected = technique_energy_window(path, duration, genre)
                elif technique_name == "genre_prior":
                    y, sr = _decode(path, 2.0, start=duration * 0.1318)
                    detected = technique_genre_prior(y, sr, genre)
                elif technique_name == "tiered":
                    detected, esc = technique_tiered(path, duration, genre)
                    if esc:
                        escalated += 1
            except Exception as e:
                print(f"    ERROR {path.name[:40]}: {e}")
                continue
            elapsed = time.time() - t0
            times.append(elapsed)
            n += 1
            if _mirex(detected, true_bpm):
                correct += 1

        mean_t = sum(times) / max(1, len(times))
        line = (f"  {technique_name:15s} MIREX: {correct}/{n} ({correct/max(1,n):.1%})  "
                f"mean_time={mean_t:.2f}s/track  min={min(times):.2f}s  max={max(times):.2f}s")
        if technique_name == "tiered":
            line += f"  escalated={escalated}/{n}"
        print(line)


def main() -> int:
    with open(sys.argv[1]) as f:
        sets = json.load(f)
    run_set("HOUSE (basic, simple)", sets["house"])
    run_set("DISCO (high difficulty)", sets["disco"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
