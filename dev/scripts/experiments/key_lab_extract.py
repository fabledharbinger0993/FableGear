"""Key-detection lab, stage 1: decode each track once and cache bin-resolved spectra.

Standalone research script (does not change the detector). Each track is decoded using the
selected window: `body` reproduces the tempo window and old key path, while `whole` matches
the current production key path (from 0:00, capped at ten minutes). Audio is mapped onto 36
log-frequency bins per octave from A0 (the same mapping as `dsp.whitened_log_spectrum`), and
reduced to a handful of whitening variants. Every variant is summed over frames inside each of
`N_SEG` equal time segments, so offline experiments can rebuild the whole-clip chroma
(sum over segments), a per-segment chroma, or restrict to a frequency band, without decoding
again. Truth is never read here; it travels with the manifest row.

Variants (all `a` = log-binned magnitude, `env_k` = moving average of `a` over k octaves):
  w1       max(a / env_1 - 1, 0)           the shipped whitening (1-octave envelope)
  w05      same, 0.5-octave envelope
  w2       same, 2-octave envelope
  w1_t25   max(a / env_1 - 1.25, 0)        only peaks well above the envelope
  w1_sqrt  sqrt of w1 per frame             compresses very strong peaks
  w1_sq    square of w1 per frame           emphasises very strong peaks
  mag      a                               raw magnitude (no whitening)

Usage:
  PYTHONPATH=<worktree> venv/bin/python scripts/experiments/key_lab_extract.py sets --out-dir D
  PYTHONPATH=<worktree> venv/bin/python scripts/experiments/key_lab_extract.py extract \
      --manifest D/rbfresh.json --out D/rbfresh.npz --workers 5
  # SS31: a larger pool from a newer Rekordbox snapshot, then more training/untouched sets from it
  ... key_lab_extract.py pool --snapshot SNAPDIR/master.db --out ~/FableGearTestbed/refresh2/tracks.json
  ... key_lab_extract.py extra train4 --out-dir D --n 12000 --pool ~/FableGearTestbed/refresh2/tracks.json
  ... key_lab_extract.py extract --manifest D/train4.json --out D/train4_whole.npz --window whole \
      --variants w1,w05,w2,mag
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "fablegear"))

from iron import api, dsp

BED = Path.home() / "FableGearTestbed"
BPO = 36
FMIN = 27.5
N_OCT = 7.5
N_LOG = int(BPO * N_OCT)
N_FFT, HOP = 16384, 4096
N_SEG = 8
VARIANTS = ("w1", "w05", "w2", "w1_t25", "w1_sqrt", "w1_sq", "mag")


def _weights(sr: int) -> np.ndarray:
    """FFT bin -> log bin triangular map; identical construction to dsp.whitened_log_spectrum."""
    freqs = np.fft.rfftfreq(N_FFT, d=1.0 / sr)
    rows = np.nonzero(freqs >= FMIN)[0]
    pos = BPO * np.log2(freqs[rows] / FMIN)
    lower = np.floor(pos).astype(np.int64)
    frac = pos - lower
    w = np.zeros((freqs.size, N_LOG), dtype=np.float64)
    for b, wt in ((lower, 1.0 - frac), (lower + 1, frac)):
        keep = b < N_LOG
        np.add.at(w, (rows[keep], b[keep]), wt[keep])
    return w


def _env(a: np.ndarray, octaves: float) -> np.ndarray:
    k = np.ones(max(round(BPO * octaves), 1))
    k /= k.size
    return np.stack([np.convolve(row, k, mode="same") for row in a]) + 1e-9


WHOLE_MAX_SECONDS = 600.0


def features(path: str, window: str = "body", variants: tuple[str, ...] = VARIANTS) -> np.ndarray | None:
    """(len(variants), N_SEG, N_LOG) float32, or None for a clip with no frames.

    "body" matches the tempo window and old key path; "whole" matches the production key window."""
    p = Path(path)
    duration = api._probe_duration(p)
    if window == "whole":
        start, span = 0.0, min(duration or WHOLE_MAX_SECONDS, WHOLE_MAX_SECONDS)
    else:
        start, span = api._pick_body_window(duration)
    y, sr = api._decode(p, span, start=start)
    mag = dsp.magnitude_spectrogram(y, n_fft=N_FFT, hop_length=HOP)
    if mag.shape[0] == 0:
        return None
    with np.errstate(all="ignore"):  # Accelerate BLAS raises spurious matmul flags
        a = np.nan_to_num(mag @ _weights(sr))
    w1 = np.maximum(a / _env(a, 1.0) - 1.0, 0.0)
    per_frame = {
        "w1": w1,
        "w05": np.maximum(a / _env(a, 0.5) - 1.0, 0.0),
        "w2": np.maximum(a / _env(a, 2.0) - 1.0, 0.0),
        "w1_t25": np.maximum(a / _env(a, 1.0) - 1.25, 0.0),
        "w1_sqrt": np.sqrt(w1),
        "w1_sq": w1**2,
        "mag": a,
    }
    out = np.zeros((len(variants), N_SEG, N_LOG), dtype=np.float32)
    for vi, name in enumerate(variants):
        for si, idx in enumerate(np.array_split(np.arange(a.shape[0]), N_SEG)):
            if idx.size:
                out[vi, si] = per_frame[name][idx].sum(axis=0)
    return out


_WINDOW = "body"
_VARIANTS: tuple[str, ...] = VARIANTS


def _init_worker(window: str, variants: tuple[str, ...]) -> None:
    global _WINDOW, _VARIANTS
    _WINDOW, _VARIANTS = window, variants


def _one(row: dict) -> tuple[dict, np.ndarray | None, str | None]:
    try:
        return row, features(row["audio"], _WINDOW, _VARIANTS), None
    except Exception as exc:
        return row, None, f"{type(exc).__name__}: {exc}"


def extract(manifest: Path, out: Path, workers: int, window: str = "body",
            variants: tuple[str, ...] = VARIANTS) -> None:
    rows = json.loads(manifest.read_text())
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(window, variants)) as ex:
        results = list(ex.map(_one, rows, chunksize=4))
    good = [(r, f) for r, f, _ in results if f is not None]
    bad = [(r, e) for r, f, e in results if f is None]
    np.savez_compressed(
        out,
        feats=np.stack([f for _, f in good]),
        meta=np.array([json.dumps(r, ensure_ascii=False) for r, _ in good]),
        variants=np.array(variants),
    )
    print(f"{manifest.name}: {len(good)} tracks cached, {len(bad)} failed -> {out}")
    for r, e in bad[:8]:
        print("  FAILED", r.get("audio"), e)


def check_against_production(manifest: Path, n: int) -> None:
    """The cached 'w1' summed over segments must equal dsp.whitened_log_spectrum exactly."""
    rows = json.loads(manifest.read_text())[:n]
    worst = 0.0
    for row in rows:
        f = features(row["audio"])
        p = Path(row["audio"])
        start, span = api._pick_body_window(api._probe_duration(p))
        y, sr = api._decode(p, span, start=start)
        ref = dsp.whitened_log_spectrum(y, sr, fmin=FMIN, bins_per_octave=BPO)
        got = f[0].sum(axis=0).astype(np.float64)
        worst = max(worst, float(np.max(np.abs(got - ref)) / (np.max(ref) + 1e-12)))
    print(f"max relative difference vs dsp.whitened_log_spectrum over {len(rows)} tracks: {worst:.2e}")


def build_sets(out_dir: Path, fresh_n: int, seed: int) -> None:
    """Write manifests: train (the 798 usable tracks of the shipped training set), rb200,
    mixed150 and `rbfresh` -- a seeded sample of Rekordbox-analysed tracks that appears in none
    of the other three (neither the same path nor the same (size, length) copy)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    train = json.loads((BED / "keyfeat/train_manifest.json").read_text())
    train_rows = [dict(audio=m["path"], key=m["key"], id=m["rb_id"]) for m in train if os.path.exists(m["path"])]
    rb = json.loads((BED / "rb200/manifest.json").read_text())
    rb_rows = [dict(audio=str(BED / "rb200/pristine" / m["file"]), key=m.get("key"), id=m["file"])
               for m in rb if m.get("source") == "rekordbox" and m.get("bpm") and m.get("key")]
    mx = json.loads((BED / "manifest.json").read_text())
    mx_rows = [dict(audio=str(BED / "pristine" / m["file"]), key=m.get("key"), id=m["file"])
               for m in mx if m.get("source") == "rekordbox" and m.get("bpm") and m.get("key")]

    used_paths = {m["path"] for m in train} | {m["path"] for m in rb} | {m["path"] for m in mx}
    # File size of every already-used track: a byte-identical copy elsewhere must not leak across sets.
    used_sizes = {os.path.getsize(p) for p in used_paths if os.path.exists(p)}
    pool = json.loads((BED / "refresh/tracks.json").read_text())
    seen: set[tuple[int, int]] = set()
    cand = []
    for t in pool:
        if t["path"] in used_paths or t["size"] in used_sizes or not t.get("key"):
            continue
        key = (t["size"], t["length"])
        if key in seen:
            continue
        seen.add(key)
        cand.append(dict(audio=t["path"], key=t["key"], id=t["rb_id"]))
    cand.sort(key=lambda r: r["audio"])
    random.Random(seed).shuffle(cand)
    fresh = [r for r in cand[:fresh_n * 2] if os.path.exists(r["audio"])][:fresh_n]
    for name, rows in (("train", train_rows), ("rb200", rb_rows), ("mixed150", mx_rows), ("rbfresh", fresh)):
        (out_dir / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
        print(f"{name}: {len(rows)} tracks")
    print(f"fresh pool after exclusions: {len(cand)}")


EXTRA_SETS = ("train2", "confirm", "train3", "confirm2", "confirm3", "train4", "train5", "confirm4")


def build_pool(snapshot: Path, out: Path) -> None:
    """Pool of Rekordbox-analysed tracks from a COPY of master.db (+wal, +shm): BPM, key and Analysed set,
    the file present at its FolderPath, one row per (size, length). Same rule as harness/refresh_rb_bench.py."""
    import logging

    from pyrekordbox import Rekordbox6Database

    logging.disable(logging.WARNING)
    db = Rekordbox6Database(str(snapshot), unlock=True)
    tracks, seen = [], set()
    for r in db.get_content().all():
        key = getattr(r.Key, "ScaleName", None)
        if not (r.BPM and key and r.Analysed and r.FolderPath) or not os.path.exists(r.FolderPath):
            continue
        size = os.path.getsize(r.FolderPath)
        if (size, r.Length) in seen:
            continue
        seen.add((size, r.Length))
        tracks.append(dict(rb_id=str(r.ID), path=r.FolderPath, bpm=r.BPM / 100, key=key,
                           genre=getattr(r.Genre, "Name", None), length=r.Length, size=size))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(tracks, indent=1, ensure_ascii=False))
    print(f"pool: {len(tracks)} tracks -> {out}")


def build_extra(out_dir: Path, name: str, n: int, seed: int, pool: Path | None = None) -> None:
    """Extra tracks (`train2` for more training data, `confirm` for a final untouched check):
    Rekordbox-analysed and disjoint (path and file size) from every set already in `out_dir`.
    Run after `sets`; build `train2` first, then `confirm`."""
    used: set[str] = set()
    for other in ("train", "rb200", "mixed150", "rbfresh", *EXTRA_SETS):
        f = out_dir / f"{other}.json"
        if other != name and f.exists():
            used |= {r["audio"] for r in json.loads(f.read_text())}
    used_sizes = {os.path.getsize(p) for p in used if os.path.exists(p)}
    seen: set[tuple[int, int]] = set()
    cand = []
    for t in json.loads((pool or BED / "refresh/tracks.json").read_text()):
        if t["path"] in used or t["size"] in used_sizes or not t.get("key") or (t["size"], t["length"]) in seen:
            continue
        seen.add((t["size"], t["length"]))
        cand.append(dict(audio=t["path"], key=t["key"], id=t["rb_id"]))
    cand.sort(key=lambda r: r["audio"])
    random.Random(seed + len(name)).shuffle(cand)
    rows = [r for r in cand[: n * 2] if os.path.exists(r["audio"])][:n]
    (out_dir / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print(f"{name}: {len(rows)} tracks (pool after exclusions {len(cand)})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sets")
    s.add_argument("--out-dir", type=Path, required=True)
    s.add_argument("--fresh-n", type=int, default=1000)
    s.add_argument("--seed", type=int, default=20261014)
    t2 = sub.add_parser("extra")
    t2.add_argument("name", choices=list(EXTRA_SETS))
    t2.add_argument("--out-dir", type=Path, required=True)
    t2.add_argument("--n", type=int, default=2000)
    t2.add_argument("--seed", type=int, default=20261014)
    t2.add_argument("--pool", type=Path, help="pool JSON from the `pool` command (default refresh/tracks.json)")
    pl = sub.add_parser("pool")
    pl.add_argument("--snapshot", type=Path, required=True, help="a COPY of master.db, never the live file")
    pl.add_argument("--out", type=Path, required=True)
    e = sub.add_parser("extract")
    e.add_argument("--manifest", type=Path, required=True)
    e.add_argument("--out", type=Path, required=True)
    e.add_argument("--workers", type=int, default=5)
    e.add_argument("--window", choices=["body", "whole"], default="body")
    e.add_argument("--variants", default=",".join(VARIANTS), help="comma-separated subset of VARIANTS to cache")
    c = sub.add_parser("check")
    c.add_argument("--manifest", type=Path, required=True)
    c.add_argument("--n", type=int, default=3)
    a = ap.parse_args()
    if a.cmd == "sets":
        build_sets(a.out_dir, a.fresh_n, a.seed)
    elif a.cmd == "extra":
        build_extra(a.out_dir, a.name, a.n, a.seed, a.pool)
    elif a.cmd == "pool":
        build_pool(a.snapshot, a.out)
    elif a.cmd == "extract":
        chosen = tuple(v for v in a.variants.split(",") if v)
        unknown = set(chosen) - set(VARIANTS)
        if unknown:
            ap.error(f"unknown variants: {sorted(unknown)}")
        extract(a.manifest, a.out, a.workers, a.window, chosen)
    else:
        check_against_production(a.manifest, a.n)
