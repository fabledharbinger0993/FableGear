#!/usr/bin/env python3
"""
Stack log_gaussian, genre_prior, and tiered as a genuinely SEQUENTIAL chain -- each stage
takes whatever (bpm, confidence) the previous stage arrived at and may override it, same
architecture as the existing production pipeline's Pass 1->2->2b->4. Tests all 6 orderings
of the 3 stages against the same 40 tracks (20 house + 20 disco) to see whether order
changes the outcome, and what it costs.

Throwaway diagnostic script, not a proposed production tool.
"""

from __future__ import annotations

import itertools
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from iron import dsp
from iron.api import _decode
from iron.tempo import _combined_score

_OCTAVE_RATIOS = (2.0, 0.5, 1.5, 2.0 / 3.0)
_BPM_MIN, _BPM_MAX = 60.0, 180.0
_HOP = 512
_GENRE_CENTERS = {
    "house": 124.0, "disco": 115.0, "nu disco": 118.0, "soul": 108.0, "funk": 108.0,
}
_TIERED_CONF_THRESHOLD = 0.75


def _lag_range(frame_rate: float, acf_len: int) -> tuple[int, int]:
    lo = max(1, int(frame_rate * 60.0 / _BPM_MAX))
    hi = min(acf_len - 1, int(frame_rate * 60.0 / _BPM_MIN))
    return lo, hi


def _shared_state(y: np.ndarray, sr: int):
    """One decode's worth of shared analysis: acf, band_acfs, frame_rate, lag bounds, and
    the raw Pass-1 winner as (bpm, confidence)."""
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
    peak_strength = float(np.max(np.abs(acf[1:]))) if acf.shape[0] > 1 else 0.0
    if peak_strength <= 0:
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
    conf = float(np.clip(acf[best_lag] / peak_strength, 0.0, 1.0))
    return {
        "acf": acf, "band_acfs": band_acfs, "frame_rate": frame_rate,
        "lag_lo": lag_lo, "lag_hi": lag_hi, "peak_strength": peak_strength,
        "bpm": frame_rate * 60.0 / best_lag, "conf": conf,
    }


def _lag_of(bpm: float, frame_rate: float) -> int:
    return round(frame_rate * 60.0 / bpm)


def _confidence_at(bpm: float, state: dict) -> float:
    lag = _lag_of(bpm, state["frame_rate"])
    if lag < 1 or lag >= state["acf"].shape[0]:
        return 0.0
    return float(np.clip(state["acf"][lag] / state["peak_strength"], 0.0, 1.0))


def stage_log_gaussian(bpm: float, conf: float, state: dict, genre: str, path: Path, duration: float,
                        center: float = 120.0, sigma: float = 0.5):
    lag = _lag_of(bpm, state["frame_rate"])
    best_weighted = _combined_score(state["acf"], state["band_acfs"], lag) * math.exp(
        -((math.log2(bpm / center)) ** 2) / (2 * sigma ** 2)
    )
    best_bpm = bpm
    for ratio in _OCTAVE_RATIOS:
        rlag = round(lag * ratio)
        if rlag < state["lag_lo"] or rlag > state["lag_hi"]:
            continue
        rbpm = state["frame_rate"] * 60.0 / rlag
        weight = math.exp(-((math.log2(rbpm / center)) ** 2) / (2 * sigma ** 2))
        score = _combined_score(state["acf"], state["band_acfs"], rlag) * weight
        if score > best_weighted:
            best_weighted, best_bpm = score, rbpm
    return best_bpm, _confidence_at(best_bpm, state)


def stage_genre_prior(bpm: float, conf: float, state: dict, genre: str, path: Path, duration: float,
                       rival_threshold: float = 0.5):
    center = None
    for key, c in _GENRE_CENTERS.items():
        if key in genre:
            center = c
            break
    if center is None:
        return bpm, conf
    lag = _lag_of(bpm, state["frame_rate"])
    cur_score = _combined_score(state["acf"], state["band_acfs"], lag)
    cur_dist = abs(math.log2(bpm / center))
    best_bpm, best_dist = bpm, cur_dist
    for ratio in _OCTAVE_RATIOS:
        rlag = round(lag * ratio)
        if rlag < state["lag_lo"] or rlag > state["lag_hi"]:
            continue
        rbpm = state["frame_rate"] * 60.0 / rlag
        dist = abs(math.log2(rbpm / center))
        score = _combined_score(state["acf"], state["band_acfs"], rlag)
        if dist < best_dist and score >= rival_threshold * cur_score:
            best_dist, best_bpm = dist, rbpm
    return best_bpm, _confidence_at(best_bpm, state)


def stage_tiered(bpm: float, conf: float, state: dict, genre: str, path: Path, duration: float):
    if conf >= _TIERED_CONF_THRESHOLD:
        return bpm, conf
    from iron import tempo as tempo_module
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
        return bpm, conf
    best_cluster, best_weight = [], -1.0
    for b, _c in results:
        cluster = [(bb, cc) for bb, cc in results if abs(bb - b) / b <= 0.02]
        w = sum(cc for _bb, cc in cluster)
        if w > best_weight:
            best_weight, best_cluster = w, cluster
    if len(best_cluster) >= 2:
        total_c = sum(cc for _bb, cc in best_cluster)
        return sum(bb * cc for bb, cc in best_cluster) / total_c, best_weight / len(best_cluster)
    best_bpm, best_c = max(results, key=lambda r: r[1])
    return best_bpm, best_c


_STAGES = {"log_gaussian": stage_log_gaussian, "genre_prior": stage_genre_prior, "tiered": stage_tiered}


def run_chain(order: tuple[str, ...], path: Path, duration: float, genre: str):
    start = duration * 0.1318
    y, sr = _decode(path, 2.0, start=start)
    state = _shared_state(y, sr)
    if state is None:
        return None
    bpm, conf = state["bpm"], state["conf"]
    for stage_name in order:
        bpm, conf = _STAGES[stage_name](bpm, conf, state, genre, path, duration)
    return bpm


def _mirex(detected, true_bpm) -> bool:
    return detected is not None and abs(detected - true_bpm) / true_bpm <= 0.04


def main() -> int:
    with open(sys.argv[1]) as f:
        sets = json.load(f)
    tracks = sets["house"] + sets["disco"]
    print(f"Testing all orderings against n={len(tracks)} tracks (20 house + 20 disco)\n")

    for order in itertools.permutations(("log_gaussian", "genre_prior", "tiered")):
        correct = 0
        times = []
        for t in tracks:
            path = Path(t["path"])
            t0 = time.time()
            try:
                detected = run_chain(order, path, t["dur"], t["genre"])
            except Exception as e:
                print(f"  ERROR {path.name[:30]}: {e}")
                continue
            times.append(time.time() - t0)
            if _mirex(detected, t["bpm"]):
                correct += 1
        n = len(times)
        order_str = " -> ".join(order)
        print(f"  {order_str:55s} MIREX: {correct}/{n} ({correct/max(1,n):.1%})  "
              f"mean_time={sum(times)/max(1,n):.2f}s  min={min(times):.2f}s  max={max(times):.2f}s")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
