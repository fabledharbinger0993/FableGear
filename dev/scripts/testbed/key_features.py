"""
Key-detection research: decode each track once (Iron's own body window) and cache several
whole-track log-frequency spectra (36 bins/octave), so profile / whitening / tuning /
harmonic ideas can be tested offline without re-decoding.

    venv/bin/python key_features.py trainset <snapshot master.db> [--count 800] [--seed 7]
    venv/bin/python key_features.py extract <set name: train|rb200|mixed150> [--workers 6]

`trainset` picks Rekordbox-ANALYSED tracks (Analysed != 0; a key with Analysed == 0 came
from a file tag, not Rekordbox) on Passport, excluding every track in the test sets, deduped
by (size, length), seeded sample. Audio is read in place (ffmpeg), never written or copied.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

BED = Path.home() / "FableGearTestbed"
OUT = BED / "keyfeat"
REPO = Path.home() / "Documents/FableGear"

BPO = 36                       # bins per octave (1/3 semitone)
FMIN = 27.5                    # A0 -- semitone centres on bins 0,3,6,...: pitch class of bin b = (9 + round(b / 3)) % 12
N_OCT = 7.5                    # up to ~4.98 kHz
N_LOG = int(BPO * N_OCT)
N_FFT, HOP = 16384, 4096       # Iron's chroma_cqt settings at 22050 Hz


def _log_map(sr: int) -> np.ndarray:
    """(n_lin, N_LOG) triangular-interpolation matrix: linear FFT bin -> fractional log bin."""
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / sr)
    w = np.zeros((freqs.size, N_LOG))
    ok = freqs >= FMIN
    pos = BPO * np.log2(freqs[ok] / FMIN)
    lo = np.floor(pos).astype(int)
    frac = pos - lo
    rows = np.nonzero(ok)[0]
    for r, b, f in zip(rows, lo, frac):
        if 0 <= b < N_LOG:
            w[r, b] += 1 - f
        if 0 <= b + 1 < N_LOG:
            w[r, b + 1] += f
    return w


def features(path: str) -> dict[str, list[float]] | None:
    sys.path.insert(0, str(REPO))
    from iron import api, dsp
    dur = api._probe_duration(Path(path))
    start, span = api._pick_body_window(dur)
    y, sr = api._decode(Path(path), span, start=start)
    mag = dsp.magnitude_spectrogram(y, n_fft=N_FFT, hop_length=HOP)
    if mag.shape[0] == 0:
        return None
    w = _log_map(sr)
    with np.errstate(all="ignore"):  # Accelerate BLAS raises spurious matmul flags; outputs checked finite
        a = mag @ w                                # (frames, N_LOG) magnitude in log bins
        p = (mag ** 2) @ w                         # power in log bins
    # Spectral whitening: divide each frame by its local envelope (moving average over one
    # octave of log bins), so a loud bass/kick region can't outweigh quieter tonal content.
    k = np.ones(BPO) / BPO
    env = np.apply_along_axis(lambda r: np.convolve(r, k, mode="same"), 1, a) + 1e-9
    wh = np.maximum(a / env - 1.0, 0.0)            # keep only bins standing above their envelope
    frame_max = a.max(axis=1, keepdims=True) + 1e-12
    return {
        "power": p.sum(0).tolist(),
        "mag": a.sum(0).tolist(),
        "log": np.log1p(100 * a / frame_max).sum(0).tolist(),
        "white": wh.sum(0).tolist(),
        "framenorm": (a / frame_max).sum(0).tolist(),
    }


def _one(item: dict) -> dict:
    try:
        item["feat"] = features(item["path"])
    except Exception as e:  # a bad file must not stop the batch; recorded per row
        item["error"] = f"{type(e).__name__}: {e}"
    return item


def trainset(db_path: str, count: int, seed: int) -> None:
    logging.disable(logging.WARNING)
    from pyrekordbox import Rekordbox6Database
    exclude = set()
    for f in (BED / "manifest.json", BED / "rb200" / "manifest.json"):
        exclude |= {m["path"] for m in json.loads(f.read_text())}
    db = Rekordbox6Database(db_path, unlock=True)
    seen, pool = set(), []
    for r in db.get_content().all():
        key = getattr(r.Key, "ScaleName", None)
        if not (r.Analysed and key and r.FolderPath and r.FolderPath.startswith("/Volumes/Passport")):
            continue
        if r.FolderPath in exclude or not os.path.exists(r.FolderPath):
            continue
        dup = (os.path.getsize(r.FolderPath), r.Length)
        if dup in seen:
            continue
        seen.add(dup)
        pool.append(dict(path=r.FolderPath, key=key, genre=getattr(r.Genre, "Name", None), rb_id=str(r.ID)))
    pool.sort(key=lambda m: m["path"])
    random.Random(seed).shuffle(pool)
    OUT.mkdir(exist_ok=True)
    (OUT / "train_manifest.json").write_text(json.dumps(pool[:count], indent=1, ensure_ascii=False))
    print(f"eligible {len(pool)} -> train {min(count, len(pool))}")


def _set_items(name: str) -> list[dict]:
    if name == "train":
        return json.loads((OUT / "train_manifest.json").read_text())
    set_dir = BED / ("rb200" if name == "rb200" else "")
    rows = json.loads((set_dir / "manifest.json").read_text())
    # Same selection as iron_real.py; audio from the testbed copies.
    return [dict(path=str(set_dir / "pristine" / m["file"]), key=m.get("key"), file=m["file"])
            for m in rows if m.get("source") == "rekordbox" and m.get("bpm")]


def extract(name: str, workers: int) -> None:
    items = _set_items(name)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(_one, items, chunksize=4))
    OUT.mkdir(exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(rows))
    bad = [r for r in rows if r.get("feat") is None]
    print(f"{name}: {len(rows)} tracks, {len(bad)} without features")
    for r in bad[:5]:
        print("  ", r["path"], r.get("error"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("trainset")
    t.add_argument("db")
    t.add_argument("--count", type=int, default=800)
    t.add_argument("--seed", type=int, default=7)
    e = sub.add_parser("extract")
    e.add_argument("name", choices=["train", "rb200", "mixed150"])
    e.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    if a.cmd == "trainset":
        trainset(a.db, a.count, a.seed)
    else:
        extract(a.name, a.workers)
