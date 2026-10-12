"""Candidate experiment: double Iron's BPM when the doubled tempo is in 160-185 BPM and kick-band
onset strength favours the doubled grid. Standalone -- does not import or change iron/.

Rule, per track, with Iron's candidate b (the undoubled BPM):
  1. Only if 160 <= 2b <= 185 (so b is 80-92.5). Otherwise the candidate is kept.
  2. Kick-band onset envelope E(t): 40-120 Hz Butterworth band-pass, rectified, smoothed (5 ms),
     positive first difference, sampled at 200 frames/s.
  3. Undoubled strength U: max over phase phi of mean E at phi + k*60/b (the Iron beat grid).
  4. Doubled-period strength O: mean E at the between-beat positions of the doubled grid, i.e.
     phi* + (k + 0.5)*60/b, where phi* is the phase that maximised U.
     (The doubled grid's own phase-max mean is always <= U, because it is the average of the two
     beat sub-grids. So the doubled period is judged on its new, between-beat positions.)
  5. Double when O > U (literal rule, the primary result). A tolerant variant doubles when
     O >= TOL * U, with TOL = 0.8, reported as a sensitivity check.

Metrics vs Rekordbox (the reference): exact = |d - r| <= 0.6 BPM, within 1% and within 4% relative
to r; double = |d - 2r| <= 4% of 2r; half = |d - r/2| <= 4% of r/2.

Usage:
  venv/bin/python scripts/experiments/candidate160_185.py \
      --iron-jsonl ~/FableGearTestbed/results/iron_current_rb_analysed.jsonl \
      --ref ~/FableGearTestbed/results/rb_reference.json \
      --out ~/FableGearTestbed/results/candidate160_185.json --workers 6
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

SR = 22050
FPS = 200  # envelope frames per second (5 ms)
TOL = 0.8


def kick_envelope(path: str) -> np.ndarray:
    import librosa
    from scipy.signal import butter, sosfiltfilt

    y, _ = librosa.load(path, sr=SR, mono=True)
    sos = butter(4, [40, 120], btype="bandpass", fs=SR, output="sos")
    x = np.abs(sosfiltfilt(sos, y))
    hop = SR // FPS
    n = len(x) // hop
    env = x[: n * hop].reshape(n, hop).mean(axis=1)
    return np.maximum(np.diff(env, prepend=env[0]), 0.0)


def grid_strength(E: np.ndarray, period_s: float, phase_steps: int = 150):
    """Max over phase of mean E on a grid of the given period. Returns (strength, best phase in s)."""
    t_end = len(E) / FPS
    best, best_phi = -1.0, 0.0
    for phi in np.linspace(0.0, period_s, phase_steps, endpoint=False):
        idx = np.round((phi + np.arange(0, t_end - period_s, period_s)) * FPS).astype(int)
        idx = idx[idx < len(E)]
        if len(idx) == 0:
            continue
        s = float(E[idx].mean())
        if s > best:
            best, best_phi = s, phi
    return best, best_phi


def decide(E: np.ndarray, b: float):
    """Return (doubled_literal, doubled_tolerant, U, O, ratio) for undoubled candidate b."""
    if not (160 <= 2 * b <= 185):
        return False, False, None, None, None
    P = 60.0 / b
    U, phi = grid_strength(E, P)
    t_end = len(E) / FPS
    mids = phi + (np.arange(0, t_end - P, P) + 0.5) * P
    idx = np.round(mids * FPS).astype(int)
    idx = idx[(idx >= 0) & (idx < len(E))]
    off = float(E[idx].mean()) if len(idx) else 0.0
    ratio = off / U if U > 0 else 0.0
    return off > U, off >= TOL * U, U, off, ratio


def work(task: dict) -> dict:
    row = dict(id=task["id"], name=task["name"], rb_bpm=task["rb_bpm"], iron_before=task["iron_bpm"])
    try:
        # The rule can only fire when 160 <= 2b <= 185; other candidates are kept as they are, so the
        # envelope is decoded only for those (equivalent to decoding every track).
        if 160 <= 2 * task["iron_bpm"] <= 185:
            E = kick_envelope(task["path"])
        else:
            E = np.zeros(1)
        dl, dt, U, off, ratio = decide(E, task["iron_bpm"])
        row.update(U=U, O=off, O_over_U=None if ratio is None else round(ratio, 4),
                   doubled_literal=dl, doubled_tolerant=dt)
    except Exception as e:  # keep the run going; record the failure
        row.update(error=f"{type(e).__name__}: {e}", doubled_literal=False, doubled_tolerant=False)
    b = row["iron_before"]
    row["iron_after_literal"] = round(2 * b, 2) if row["doubled_literal"] else b
    row["iron_after_tolerant"] = round(2 * b, 2) if row["doubled_tolerant"] else b
    return row


def classify(d: float, r: float) -> dict:
    return dict(exact=abs(d - r) <= 0.6, within1=abs(d - r) / r <= 0.01, within4=abs(d - r) / r <= 0.04,
                double=abs(d - 2 * r) / (2 * r) <= 0.04, half=abs(d - r / 2) / (r / 2) <= 0.04)


def summarise(rows: list[dict], key: str) -> dict:
    ok = [x for x in rows if x.get(key) and x.get("rb_bpm")]
    n = len(ok)
    acc = {k: 0 for k in ("exact", "within1", "within4", "double", "half")}
    for x in ok:
        for k, v in classify(x[key], x["rb_bpm"]).items():
            acc[k] += int(v)
    out = {k: acc[k] for k in acc}
    out.update({f"{k}_pct": round(100 * acc[k] / n, 2) for k in ("exact", "within1", "within4")}, n=n)
    return out


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

    before = summarise(rows, "iron_before")
    literal = summarise(rows, "iron_after_literal")
    tolerant = summarise(rows, "iron_after_tolerant")
    res = dict(
        rule=dict(band_hz=[40, 120], doubled_range_bpm=[160, 185], literal="O > U", tolerant=f"O >= {TOL} * U"),
        population=len(tasks),
        doubled_literal=sum(x["doubled_literal"] for x in rows),
        doubled_tolerant=sum(x["doubled_tolerant"] for x in rows),
        before=before, after_literal=literal, after_tolerant=tolerant,
        errors=sum(1 for x in rows if "error" in x),
    )
    json.dump(dict(summary=res, tracks=rows), open(a.out, "w"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
