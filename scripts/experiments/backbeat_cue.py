"""Backbeat cue experiment (standalone; does not import or change iron/).

Question: does drum-and-bass have a snare pattern that Iron's own grid can see at half tempo?
Iron's candidate b is the undoubled BPM. For each track the script computes three cues.

  Backbeat ratio: mean snare-band onset strength on the between-beat positions of Iron's grid,
      divided by the strength on Iron's beat positions. Snare on the 2 and 4 of a bar at the
      doubled tempo sits between Iron's beats, so the ratio is above 1 for a doubled-tempo backbeat.
      The phase of Iron's grid comes from the kick band, as in candidate160_185.py.
  Snare distance: median interval between snare onsets, in beats of Iron's grid, and the
      fraction of intervals within 0.15 beat of 1.0 (snare on every Iron beat) and of 2.0.
  Snare density: snare onsets per 4 Iron beats (one bar).

Snare band: 1-5 kHz band-pass, rectified, positive first difference, 200 frames/s.
Kick band: 40-120 Hz, as in candidate160_185.py.

Rules tested (each doubles only when 160 <= 2b <= 185, as in the candidate experiment):
  R_backbeat: backbeat ratio >= 1.0
  R_distance: fraction of snare intervals at 1 beat >= 0.5
  R_density_backbeat: density >= 3 per bar and backbeat ratio >= 1.0

Usage:
  venv/bin/python scripts/experiments/backbeat_cue.py \
      --iron-jsonl ~/FableGearTestbed/results/iron_current_rb_analysed.jsonl \
      --ref ~/FableGearTestbed/results/rb_reference.json \
      --out ~/FableGearTestbed/results/backbeat_cue1993.json --workers 6
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candidate160_185 import grid_strength

SR = 22050
FPS = 200


def _envelope(y: np.ndarray, lo: float, hi: float) -> np.ndarray:
    from scipy.signal import butter, sosfiltfilt

    sos = butter(4, [lo, hi], btype="bandpass", fs=SR, output="sos")
    x = np.abs(sosfiltfilt(sos, y))
    hop = SR // FPS
    n = len(x) // hop
    env = x[: n * hop].reshape(n, hop).mean(axis=1)
    return np.maximum(np.diff(env, prepend=env[0]), 0.0)


def features(path: str, b: float) -> dict:
    import librosa
    from scipy.signal import find_peaks

    y, _ = librosa.load(path, sr=SR, mono=True)
    kick = _envelope(y, 40, 120)
    snare = _envelope(y, 1000, 5000)
    P = 60.0 / b
    _, phi = grid_strength(kick, P)
    t_end = len(snare) / FPS

    beats = np.arange(0, t_end - P, P)
    on_idx = np.round((phi + beats) * FPS).astype(int)
    off_idx = np.round((phi + (beats + 0.5 * P)) * FPS).astype(int)
    on_idx = on_idx[(on_idx >= 0) & (on_idx < len(snare))]
    off_idx = off_idx[(off_idx >= 0) & (off_idx < len(snare))]
    s_on = float(snare[on_idx].mean()) if len(on_idx) else 0.0
    s_off = float(snare[off_idx].mean()) if len(off_idx) else 0.0
    ratio = s_off / s_on if s_on > 0 else 0.0

    thr = 0.25 * np.percentile(snare, 99.5)
    peaks, _ = find_peaks(snare, height=thr, distance=max(1, int(0.3 * P * FPS)))
    times = peaks / FPS
    iv = np.diff(times) / P
    iv = iv[iv < 4.0]
    n_beats = max(t_end / P, 1e-9)
    out = dict(backbeat_ratio=round(ratio, 4), snare_hits=len(peaks),
               density_per_bar=round(len(peaks) / (n_beats / 4.0), 3))
    if len(iv):
        out.update(distance_median_beats=round(float(np.median(iv)), 3),
                   frac_interval_1=round(float(np.mean(np.abs(iv - 1) <= 0.15)), 3),
                   frac_interval_2=round(float(np.mean(np.abs(iv - 2) <= 0.15)), 3))
    return out


def work(task: dict) -> dict:
    row = dict(id=task["id"], name=task["name"], rb_bpm=task["rb_bpm"], iron_before=task["iron_bpm"])
    try:
        row.update(features(task["path"], task["iron_bpm"]))
    except Exception as e:  # record and continue
        row.update(error=f"{type(e).__name__}: {e}")
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iron-jsonl", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()

    ref = {int(r["id"]): r for r in json.load(open(a.ref))}
    tasks = []
    for line in open(a.iron_jsonl):
        x = json.loads(line)
        if "crash" in x or not x.get("bpm") or x["id"] not in ref:
            continue
        r = ref[x["id"]]
        tasks.append(dict(id=x["id"], name=r["name"], path=r["path"], rb_bpm=r["rb_bpm"], iron_bpm=x["bpm"]))
    print(f"tracks {len(tasks)}", flush=True)
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        rows = list(ex.map(work, tasks, chunksize=8))
    print(f"wall {time.time() - t0:.0f}s", flush=True)
    json.dump(dict(population=len(rows), errors=sum("error" in x for x in rows), tracks=rows),
              open(a.out, "w"), indent=1)
    print("errors", sum("error" in x for x in rows))


if __name__ == "__main__":
    main()
