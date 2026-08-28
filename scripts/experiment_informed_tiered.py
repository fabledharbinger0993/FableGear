#!/usr/bin/env python3
"""
Fix the "no real information sharing" problem from experiment_stack_order.py: when tiered
escalates, it used to throw away whatever log_gaussian/genre_prior already refined and start
its 3-window consensus from scratch. This version includes that refined answer as an
ADDITIONAL, informed vote in tiered's own consensus -- genuine sharing, not override-or-
ignore. Compares "log_gaussian -> genre_prior -> informed_tiered" against solo tiered
(unchanged from before) on the same 40 tracks.

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


def stage_log_gaussian(bpm, conf, state, genre, center=120.0, sigma=0.5):
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


def stage_genre_prior(bpm, conf, state, genre, rival_threshold=0.5):
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


def _cluster_combine(results: list[tuple[float, float]]) -> tuple[float, float]:
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


def stage_tiered_informed(bpm: float, conf: float, path: Path, duration: float):
    """Unlike the earlier version, the incoming (bpm, conf) -- already refined by
    log_gaussian + genre_prior -- is INCLUDED as a vote in the consensus when escalating,
    not discarded and recomputed from scratch."""
    if conf >= _TIERED_CONF_THRESHOLD:
        return bpm, conf
    from iron import tempo as tempo_module
    results = [(bpm, conf)]  # the informed answer gets a seat at the table
    for frac in (0.10, 0.35, 0.60):
        try:
            y2, sr2 = _decode(path, 2.0, start=duration * frac)
            o2 = tempo_module.detect_tempo(y2, sr2)
            if o2 is not None:
                results.append(o2)
        except Exception:
            continue
    return _cluster_combine(results)


def stage_tiered_solo(path: Path, duration: float):
    """Unchanged from before -- baseline for comparison."""
    from iron import tempo as tempo_module
    start1 = duration * 0.1318
    y, sr = _decode(path, 2.0, start=start1)
    outcome = tempo_module.detect_tempo(y, sr)
    if outcome is not None and outcome[1] >= _TIERED_CONF_THRESHOLD:
        return outcome[0]
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
        return outcome[0] if outcome else None
    bpm, _c = _cluster_combine(results)
    return bpm


def run_informed_chain(path: Path, duration: float, genre: str):
    start = duration * 0.1318
    y, sr = _decode(path, 2.0, start=start)
    state = _shared_state(y, sr)
    if state is None:
        return None
    bpm, conf = state["bpm"], state["conf"]
    bpm, conf = stage_log_gaussian(bpm, conf, state, genre)
    bpm, conf = stage_genre_prior(bpm, conf, state, genre)
    bpm, _conf = stage_tiered_informed(bpm, conf, path, duration)
    return bpm


def _mirex(detected, true_bpm) -> bool:
    return detected is not None and abs(detected - true_bpm) / true_bpm <= 0.04


def main() -> int:
    with open(sys.argv[1]) as f:
        sets = json.load(f)
    tracks = sets["house"] + sets["disco"]
    n = len(tracks)
    print(f"n={n} tracks (20 house + 20 disco)\n")

    for label, fn in (
        ("log_gaussian -> genre_prior -> informed_tiered",
         lambda t: run_informed_chain(Path(t["path"]), t["dur"], t["genre"])),
        ("tiered (solo, unchanged baseline)",
         lambda t: stage_tiered_solo(Path(t["path"]), t["dur"])),
    ):
        correct = 0
        times = []
        for t in tracks:
            t0 = time.time()
            try:
                detected = fn(t)
            except Exception as e:
                print(f"  ERROR {Path(t['path']).name[:30]}: {e}")
                continue
            times.append(time.time() - t0)
            if _mirex(detected, t["bpm"]):
                correct += 1
        print(f"  {label:48s} MIREX: {correct}/{n} ({correct/n:.1%})  "
              f"mean_time={sum(times)/len(times):.2f}s  min={min(times):.2f}s  max={max(times):.2f}s")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
