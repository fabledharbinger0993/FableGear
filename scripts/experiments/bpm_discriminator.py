"""Candidate-tempo discriminator experiment (standalone; does not import or change the detector).

Question: can Iron choose among its own octave / compound-meter candidates using only evidence
from the audio? Iron's BPM is the candidate anchor b. The candidates are b times a small set of
ratios. For each candidate the script records audio-only features:

  acf      normalised autocorrelation of the kick-band onset envelope at the candidate's period
  grid     kick-band grid strength at the candidate's period (best phase, mean over grid points)
  snare    snare-band grid strength at the candidate's period (best phase, mean over grid points)
  bbr      backbeat ratio on Iron's own grid: snare energy between beats / on beats. Only used
           for the doubled candidate; it is computed once per track.

Rekordbox BPM never enters a feature. It is read only to label the rows. Ground truth for the
owner's ear labels (golden set) is read from docs/iron/golden/iron_golden_labels.jsonl.

Stage 1 (this file, `extract`): decode one 45 s window per track and write features to JSONL.
Stage 2 (`evaluate`, separate file): tune a rule on a dev half and report a holdout half.

Usage:
  venv/bin/python scripts/experiments/bpm_discriminator.py extract \
      --refresh ~/FableGearTestbed/results/rb_refresh_iron.jsonl \
      --golden docs/iron/golden/iron_golden_labels.jsonl \
      --out ~/FableGearTestbed/results/bpm_disc_features.jsonl --n-per-split 600 --workers 8
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfiltfilt

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from iron import api

FPS_TARGET = 200
WINDOW_S = 45.0
# Candidate multipliers of Iron's pick. Chosen before looking at any result.
RATIOS = (1.0, 2.0, 0.5, 1.5, 2.0 / 3.0, 4.0 / 3.0, 0.75)
CAND_MIN, CAND_MAX = 55.0, 200.0
KICK_BAND = (40.0, 120.0)
SNARE_BAND = (1000.0, 5000.0)


def band_envelope(y: np.ndarray, sr: int, lo: float, hi: float) -> tuple[np.ndarray, float]:
    """Rectified band-pass energy, positive first difference, downsampled to ~200 frames/s."""
    sos = butter(4, [lo, hi], btype="bandpass", fs=sr, output="sos")
    x = np.abs(sosfiltfilt(sos, y))
    hop = round(sr / FPS_TARGET)
    n = x.size // hop
    env = x[: n * hop].reshape(n, hop).mean(axis=1)
    return np.maximum(np.diff(env, prepend=env[0]), 0.0), sr / hop


def grid_strength(env: np.ndarray, fps: float, period_s: float, steps: int = 60) -> tuple[float, float]:
    """Best-phase mean of env on a grid of the given period, normalised by the whole-window mean."""
    mean_all = float(env.mean()) + 1e-12
    t_end = env.size / fps
    best, best_phi = -1.0, 0.0
    for phi in np.linspace(0.0, period_s, steps, endpoint=False):
        pos = phi + np.arange(0.0, t_end - period_s, period_s)
        idx = np.round(pos * fps).astype(int)
        idx = idx[(idx >= 0) & (idx < env.size)]
        if idx.size == 0:
            continue
        s = float(env[idx].mean())
        if s > best:
            best, best_phi = s, phi
    return best / mean_all, best_phi


def lag_corr(env: np.ndarray, fps: float, period_s: float) -> float:
    """Pearson correlation of env with itself shifted by one period, max over +-1 frame."""
    lag0 = round(period_s * fps)
    x = env - env.mean()
    denom = float(np.dot(x, x)) + 1e-12
    best = -1.0
    for lag in (lag0 - 1, lag0, lag0 + 1):
        if 1 <= lag < x.size:
            best = max(best, float(np.dot(x[:-lag], x[lag:])) / denom)
    return best


def backbeat_ratio(snare: np.ndarray, fps: float, beat_period_s: float, kick_phase_s: float) -> float:
    """Snare energy on the half-period (between-beat) grid over the beat grid, on Iron's grid."""
    t_end = snare.size / fps
    on_pos = kick_phase_s + np.arange(0.0, t_end - beat_period_s, beat_period_s)
    off_pos = on_pos + beat_period_s / 2.0
    on_idx = np.round(on_pos * fps).astype(int)
    off_idx = np.round(off_pos * fps).astype(int)
    on_idx = on_idx[on_idx < snare.size]
    off_idx = off_idx[off_idx < snare.size]
    if on_idx.size == 0 or off_idx.size == 0:
        return float("nan")
    return float(snare[off_idx].mean()) / (float(snare[on_idx].mean()) + 1e-12)


def window_for(path: Path) -> tuple[float, float]:
    """A 45 s window at one third into the track, the same position Iron's body window starts at."""
    dur = api._probe_duration(path)
    if not dur or dur <= WINDOW_S:
        return 0.0, WINDOW_S
    return dur / 3.0, WINDOW_S


def features(path_str: str, iron_bpm: float) -> dict:
    path = Path(path_str)
    start, length = window_for(path)
    y, sr = api._decode(path, length, start=start)
    kick, fps = band_envelope(y, sr, *KICK_BAND)
    snare, _ = band_envelope(y, sr, *SNARE_BAND)
    out: dict = {"path": path_str, "iron_bpm": iron_bpm, "candidates": []}
    beat_period = 60.0 / iron_bpm
    _, kick_phase = grid_strength(kick, fps, beat_period)
    out["bbr"] = backbeat_ratio(snare, fps, beat_period, kick_phase)
    for r in RATIOS:
        cand = iron_bpm * r
        if not (CAND_MIN <= cand <= CAND_MAX):
            continue
        period = 60.0 / cand
        grid_k, _ = grid_strength(kick, fps, period)
        grid_s, _ = grid_strength(snare, fps, period)
        out["candidates"].append(
            {
                "ratio": round(r, 6),
                "bpm": round(cand, 4),
                "acf": lag_corr(kick, fps, period),
                "grid": grid_k,
                "snare": grid_s,
            }
        )
    return out


def _work(task: dict) -> dict:
    try:
        res = features(task["path"], task["iron_bpm"])
    except Exception as exc:  # record and continue; one bad file must not abort the run
        res = {"path": task["path"], "iron_bpm": task["iron_bpm"], "error": repr(exc)[:200]}
    res.update({k: v for k, v in task.items() if k not in res})
    return res


def _split_of(rb_id: str) -> str:
    # A split on a hash of the Rekordbox id, so it is fixed across runs and independent of sampling.
    return "dev" if int(hashlib.sha1(rb_id.encode()).hexdigest(), 16) % 2 == 0 else "hold"


def golden_match(file_stem: str, golden_stem: str | set[str]) -> bool:
    """Exact stem, or the same stem after the 'Unknown ' artist prefix that some files carry."""
    names = golden_stem if isinstance(golden_stem, set) else {golden_stem}
    return any(file_stem == t or file_stem.endswith(" " + t) for t in names)


def _load_golden(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def extract(args: argparse.Namespace) -> None:
    rows = [json.loads(line) for line in Path(args.refresh).expanduser().read_text().splitlines()]
    rows = [r for r in rows if r.get("detected_bpm") and r.get("true_bpm") and Path(r["path"]).exists()]
    golden = _load_golden(Path(args.golden))
    golden_stems = {Path(g["track"]).stem for g in golden}
    by_split = {"dev": [], "hold": []}
    gold_rows = []
    for r in rows:
        if golden_match(Path(r["path"]).stem, golden_stems):
            gold_rows.append(r)  # kept out of dev/hold so the owner labels stay independent
            continue
        by_split[_split_of(str(r["rb_id"]))].append(r)
    rng = random.Random(args.seed)
    tasks = []
    for split in ("dev", "hold"):
        pool = by_split[split]
        for r in rng.sample(pool, min(args.n_per_split, len(pool))):
            tasks.append({"split": split, "path": r["path"], "iron_bpm": r["detected_bpm"],
                          "true_bpm": r["true_bpm"], "rb_id": r["rb_id"]})
    for r in gold_rows:
        tasks.append({"split": "golden", "path": r["path"], "iron_bpm": r["detected_bpm"],
                      "true_bpm": r["true_bpm"], "rb_id": r["rb_id"]})
    print(f"tasks: {len(tasks)} (dev/hold {args.n_per_split} each, golden-matched {len(gold_rows)})", flush=True)
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    n_err = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool, out.open("w") as fh:
        for i, res in enumerate(pool.map(_work, tasks, chunksize=4), 1):
            n_err += "error" in res
            fh.write(json.dumps(res) + "\n")
            if i % 100 == 0:
                print(f"  {i}/{len(tasks)}", flush=True)
    print(f"wrote {out} with {len(tasks)} rows, {n_err} errors", flush=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--refresh", required=True)
    e.add_argument("--golden", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--n-per-split", type=int, default=600)
    e.add_argument("--seed", type=int, default=42)
    e.add_argument("--workers", type=int, default=8)
    e.set_defaults(func=extract)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
