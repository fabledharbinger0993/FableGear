"""Key-detection lab, stage 2: offline chroma / profile / scoring experiments on cached spectra.

Library plus a `baseline` command. Reads the `.npz` caches written by `key_lab_extract.py`;
never decodes audio and never touches a real library. Truth is Rekordbox 7's key (rb200,
rbfresh, train) or the older Passport tag key (mixed150): agreement with a reference, not
human-verified truth.

`chroma()` reproduces `iron.key._chroma` step for step (band mask, harmonic summation,
36-bin fold, tuning rotation, 3-bin sum) so a baseline run can be compared with the live
detector; `baseline` also does that comparison on a sample of tracks.

Usage:
  PYTHONPATH=<worktree> venv/bin/python scripts/experiments/key_lab.py baseline --lab D
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "fablegear"))

from iron import key as iron_key

BPO = 36
FMIN = 27.5
N_LOG = 270
FREQS = FMIN * 2.0 ** (np.arange(N_LOG) / BPO)
NOTE = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6,
        "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
SETS = ("rb200", "mixed150", "rbfresh")
# Weights of the weighted MIREX key score (docs/IRON_RESEARCH.md SS18.1).
W_FIFTH, W_REL, W_PAR = 0.5, 0.3, 0.2


def parse_key(k: str | None) -> tuple[int, int] | None:
    """'F#m' -> (6, 1); mode 0 = major, 1 = minor."""
    if not k:
        return None
    minor = k.endswith("m")
    root = k[:-1] if minor else k
    return (NOTE[root], int(minor)) if root in NOTE else None


class Cache:
    """One set's cached spectra: feats (n, variants, segments, 270) and truth (n, 2)."""

    def __init__(self, path: Path):
        z = np.load(path, allow_pickle=False)
        self.variants = [str(v) for v in z["variants"]]
        self.meta = [json.loads(str(m)) for m in z["meta"]]
        truth = [parse_key(m["key"]) for m in self.meta]
        keep = [i for i, t in enumerate(truth) if t is not None]
        self.feats = z["feats"][keep].astype(np.float64)
        self.meta = [self.meta[i] for i in keep]
        self.truth = np.array([truth[i] for i in keep])

    def spec(self, variant: str = "w1", segments: slice | None = None) -> np.ndarray:
        v = self.feats[:, self.variants.index(variant)]
        return (v if segments is None else v[:, segments]).sum(axis=1)

    def __len__(self) -> int:
        return len(self.truth)


def load_sets(lab: Path, names=("train", *SETS)) -> dict[str, Cache]:
    return {n: Cache(lab / f"{n}.npz") for n in names}


def chroma(spec: np.ndarray, *, fmin: float = 55.0, fmax: float = 3500.0, harmonics: int = 4,
           decay: float = 0.6, tuning: bool = True) -> np.ndarray:
    """(n, 270) log spectra -> (n, 12) chroma, index 0 = C. Mirrors iron.key._chroma."""
    s = spec * ((FREQS >= fmin) & (FREQS <= fmax))
    summed = s.copy()
    for h in range(2, harmonics + 1):
        shift = round(BPO * np.log2(h))
        summed[:, :-shift] += decay ** (h - 1) * s[:, shift:]
    c36 = np.zeros((s.shape[0], BPO))
    for b in range(N_LOG):
        c36[:, (b + 27) % BPO] += summed[:, b]
    if tuning:
        phase = np.stack([c36[:, j::3].sum(1) for j in range(3)], 1).argmax(1)
        shift = np.where(phase < 2, phase, -1)
        c36 = np.stack([np.roll(c, -k) for c, k in zip(c36, shift, strict=True)])
    return np.roll(c36, 1, axis=1).reshape(-1, 12, 3).sum(2)


def _z(x: np.ndarray) -> np.ndarray:
    x = x - x.mean(-1, keepdims=True)
    return x / (np.linalg.norm(x, axis=-1, keepdims=True) + 1e-12)


def rotations(major: np.ndarray, minor: np.ndarray) -> np.ndarray:
    """(24, 12) profile rotations: rows 0-11 major on tonic 0..11, rows 12-23 minor."""
    return np.stack([np.roll(major, t) for t in range(12)] + [np.roll(minor, t) for t in range(12)])


def scores(c: np.ndarray, major: np.ndarray, minor: np.ndarray) -> np.ndarray:
    """(n, 24) Pearson correlation of each chroma with every profile rotation."""
    return _z(c) @ _z(rotations(major, minor)).T


def decide(sc: np.ndarray, minor_bias: float = 0.0) -> np.ndarray:
    """(n, 2) (tonic, mode) from (n, 24) scores, with the shipped minor bias on the choice."""
    sc = sc.copy()
    sc[:, 12:] += minor_bias
    best = sc.argmax(1)
    return np.stack([best % 12, best // 12], 1)


def learn_profiles(c: np.ndarray, truth: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mean tonic-rotated, sum-normalised chroma per mode, scaled so the tonic = 1."""
    z = c / (c.sum(1, keepdims=True) + 1e-12)
    r = np.stack([np.roll(v, -t) for v, (t, _) in zip(z, truth, strict=True)])
    maj, mnr = r[truth[:, 1] == 0].mean(0), r[truth[:, 1] == 1].mean(0)
    return maj / maj[0], mnr / mnr[0]


def metrics(pred: np.ndarray, truth: np.ndarray) -> dict[str, float]:
    """Counts and rates. Classes are exclusive: exact, fifth (same mode, tonic +-5/7), relative,
    parallel (same tonic, other mode), other. `wt_up_minor` / `wt_down_minor` (both minor,
    detected tonic 2 semitones above / below the reference) are subsets of `other`."""
    n = len(truth)
    same = pred[:, 1] == truth[:, 1]
    d = (pred[:, 0] - truth[:, 0]) % 12
    exact = same & (d == 0)
    fifth = same & ((d == 7) | (d == 5))
    rel = ~same & (((truth[:, 1] == 0) & (d == 9)) | ((truth[:, 1] == 1) & (d == 3)))
    par = ~same & (d == 0)
    both_minor = same & (truth[:, 1] == 1)
    out: dict[str, float] = {"n": n}
    for name, m in (("exact", exact), ("fifth", fifth), ("relative", rel), ("parallel", par),
                    ("wt_up_minor", both_minor & (d == 2)), ("wt_down_minor", both_minor & (d == 10))):
        out[name] = int(m.sum())
        out[name + "_pct"] = 100.0 * m.sum() / n
    other = n - exact.sum() - fifth.sum() - rel.sum() - par.sum()
    out["other"], out["other_pct"] = int(other), 100.0 * other / n
    out["mirex"] = float((exact + W_FIFTH * fifth + W_REL * rel + W_PAR * par).mean())
    out["pred_minor_pct"] = 100.0 * pred[:, 1].mean()
    out["true_minor_pct"] = 100.0 * truth[:, 1].mean()
    return out


def fmt(m: dict[str, float]) -> str:
    return (f"n={m['n']:<4} exact {m['exact']:>4} ({m['exact_pct']:.1f}%)  fifth {m['fifth']:>3} ({m['fifth_pct']:.1f}%)  "
            f"rel {m['relative']:>3} ({m['relative_pct']:.1f}%)  par {m['parallel']:>3} ({m['parallel_pct']:.1f}%)  "
            f"wt-up-min {m['wt_up_minor']:>3} ({m['wt_up_minor_pct']:.1f}%)  other {m['other']:>3}  "
            f"MIREX {m['mirex']:.3f}  pred-minor {m['pred_minor_pct']:.0f}% (true {m['true_minor_pct']:.0f}%)")


def shipped_predict(c: Cache, **kw) -> np.ndarray:
    ch = chroma(c.spec("w1"), **kw)
    return decide(scores(ch, iron_key.PROFILE_MAJOR, iron_key.PROFILE_MINOR), iron_key._MINOR_BIAS)


def check_against_live(lab: Path, name: str, n: int) -> None:
    """The offline replica of the SS18 detector must agree with `iron.key.detect_key_profile` on the same audio."""
    from iron import api

    cache = Cache(lab / f"{name}.npz")
    pred = shipped_predict(cache)
    mism = 0
    for i in range(min(n, len(cache))):
        p = Path(cache.meta[i]["audio"])
        start, span = api._pick_body_window(api._probe_duration(p))
        y, sr = api._decode(p, span, start=start)
        live = iron_key.detect_key_profile(y, sr)
        t, m = pred[i]
        offline = iron_key.CAMELOT[iron_key.NOTES[t] + ("min" if m else "maj")]
        mism += live is None or live[0] != offline
    print(f"live vs offline replica on {min(n, len(cache))} {name} tracks: {mism} disagreements")


def cmd_baseline(lab: Path, live_n: int) -> None:
    sets = load_sets(lab)
    print("== shipped detector (offline replica), per set ==")
    out = {}
    for name in SETS:
        m = metrics(shipped_predict(sets[name]), sets[name].truth)
        out[name] = m
        print(f"{name:9s} {fmt(m)}")
    m = metrics(shipped_predict(sets["train"]), sets["train"].truth)
    print(f"{'train':9s} {fmt(m)}   (profiles were fitted on this set)")
    tr = sets["train"]
    maj, mnr = learn_profiles(chroma(tr.spec("w1")), tr.truth)
    print(f"relearned profile vs shipped constants: max |diff| major "
          f"{np.abs(maj - iron_key.PROFILE_MAJOR).max():.4f}, minor {np.abs(mnr - iron_key.PROFILE_MINOR).max():.4f}")
    for name in ("rb200", "mixed150"):
        check_against_live(lab, name, live_n)
    Path(lab / "baseline.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("baseline")
    b.add_argument("--lab", type=Path, required=True)
    b.add_argument("--live-n", type=int, default=40)
    a = ap.parse_args()
    cmd_baseline(a.lab, a.live_n)
