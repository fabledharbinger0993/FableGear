"""
Offline key-detection experiments over key_features.py's cached log-frequency spectra.

Selection discipline: options are chosen by 5-fold cross-validation on the TRAIN set only
(profiles learned on 4 folds, scored on the 5th); rb200 / mixed150 are held out and only
scored, never tuned on.

    venv/bin/python key_eval.py
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np

OUT = Path.home() / "FableGearTestbed/keyfeat"
BPO = 36
FMIN = 27.5
NOTE = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6,
        "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def parse_key(k: str | None) -> tuple[int, int] | None:
    """'F#m' -> (6, 1); mode 0 = major, 1 = minor."""
    if not k:
        return None
    minor = k.endswith("m")
    root = k[:-1] if minor else k
    return (NOTE[root], int(minor)) if root in NOTE else None


def load(name: str) -> tuple[dict[str, np.ndarray], np.ndarray]:
    rows = [r for r in json.loads((OUT / f"{name}.json").read_text()) if r.get("feat") and parse_key(r["key"])]
    feats = {v: np.array([r["feat"][v] for r in rows]) for v in rows[0]["feat"]}
    truth = np.array([parse_key(r["key"]) for r in rows])
    return feats, truth


def chroma12(spec: np.ndarray, *, fmin: float, fmax: float, harmonics: int, tuning: bool,
             compress: float) -> np.ndarray:
    """(n, 270) log-frequency spectra -> (n, 12) chroma, index 0 = C."""
    n_log = spec.shape[1]
    freqs = FMIN * 2 ** (np.arange(n_log) / BPO)
    s = spec * ((freqs >= fmin) & (freqs <= fmax))
    if compress:
        s = np.log1p(compress * s / (s.max(axis=1, keepdims=True) + 1e-12))
    if harmonics > 1:
        # A partial at f also votes for fundamentals f/2, f/3, ... (decaying weight), so a
        # note's overtones reinforce its own pitch class instead of a fifth / third above.
        acc = s.copy()
        for h in range(2, harmonics + 1):
            shift = round(BPO * np.log2(h))
            acc[:, :-shift] += (0.6 ** (h - 1)) * s[:, shift:]
        s = acc
    # fold to 36 bins: bin 0 is A (pitch class 9) -> chroma36 index 27
    c36 = np.zeros((s.shape[0], BPO))
    for b in range(n_log):
        c36[:, (b + 27) % BPO] += s[:, b]
    if tuning:
        # Per track: which of the 3 sub-semitone phases carries the most energy; rotate so it
        # sits on the semitone centre (corrects a track tuned up to +-1/3 semitone off A440).
        phase = np.stack([c36[:, j::3].sum(1) for j in range(3)], 1).argmax(1)
        shift = np.where(phase == 2, -1, phase)  # phase 2 = 1/3 flat of the next centre
        c36 = np.stack([np.roll(c, -k) for c, k in zip(c36, shift)])
    # 3 bins per semitone, centred: bins (3p-1, 3p, 3p+1)
    c36r = np.roll(c36, 1, axis=1)
    return c36r.reshape(-1, 12, 3).sum(2)


def rotate_to_tonic(c: np.ndarray, truth: np.ndarray) -> np.ndarray:
    return np.stack([np.roll(v, -t) for v, (t, _) in zip(c, truth)])


def learn_profiles(c: np.ndarray, truth: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    z = c / (c.sum(1, keepdims=True) + 1e-12)
    r = rotate_to_tonic(z, truth)
    return r[truth[:, 1] == 0].mean(0), r[truth[:, 1] == 1].mean(0)


def classify(c: np.ndarray, major: np.ndarray, minor: np.ndarray, minor_bias: float = 0.0) -> np.ndarray:
    """Pearson correlation against all 24 rotations; returns (n, 2) (tonic, mode)."""
    def z(x: np.ndarray) -> np.ndarray:
        x = x - x.mean(-1, keepdims=True)
        return x / (np.linalg.norm(x, axis=-1, keepdims=True) + 1e-12)
    zc = z(c)
    profs = np.stack([np.roll(major, t) for t in range(12)] + [np.roll(minor, t) for t in range(12)])
    scores = zc @ z(profs).T
    scores[:, 12:] += minor_bias
    best = scores.argmax(1)
    return np.stack([best % 12, best // 12], 1)


def score(pred: np.ndarray, truth: np.ndarray) -> dict[str, float]:
    exact = (pred == truth).all(1)
    same_mode = pred[:, 1] == truth[:, 1]
    d = (pred[:, 0] - truth[:, 0]) % 12
    fifth = same_mode & ((d == 7) | (d == 5))
    # relative: minor tonic = major tonic - 3
    rel = ~same_mode & (((truth[:, 1] == 0) & (d == 9)) | ((truth[:, 1] == 1) & (d == 3)))
    par = ~same_mode & (d == 0)
    w = exact + 0.5 * fifth + 0.3 * rel + 0.2 * par
    return {"exact": exact.mean(), "mirex": w.mean(), "fifth": fifth.mean(), "rel": rel.mean(),
            "par": par.mean(), "pred_minor": pred[:, 1].mean()}


def cv(feats, truth, opts, *, profile: str, minor_bias: float = 0.0, folds: int = 5) -> dict[str, float]:
    c = chroma12(feats[opts["variant"]], **{k: v for k, v in opts.items() if k != "variant"})
    idx = np.arange(len(truth)) % folds
    preds = np.zeros_like(truth)
    for f in range(folds):
        tr, te = idx != f, idx == f
        maj, mnr = (KS_MAJOR, KS_MINOR) if profile == "ks" else learn_profiles(c[tr], truth[tr])
        preds[te] = classify(c[te], maj, mnr, minor_bias)
    return score(preds, truth)


def held_out(train, test, opts, *, profile: str, minor_bias: float = 0.0) -> dict[str, float]:
    kw = {k: v for k, v in opts.items() if k != "variant"}
    ctr = chroma12(train[0][opts["variant"]], **kw)
    cte = chroma12(test[0][opts["variant"]], **kw)
    maj, mnr = (KS_MAJOR, KS_MINOR) if profile == "ks" else learn_profiles(ctr, train[1])
    return score(classify(cte, maj, mnr, minor_bias), test[1])


def fmt(d: dict[str, float]) -> str:
    return " ".join(f"{k}={v:.3f}" for k, v in d.items())


def main() -> None:
    train = load("train")
    tests = {n: load(n) for n in ("rb200", "mixed150")}
    print(f"train n={len(train[1])} (minor {train[1][:, 1].mean():.0%})")

    base = dict(variant="power", fmin=55.0, fmax=5000.0, harmonics=1, tuning=False, compress=0.0)
    print("\nBASELINE (current Iron-like: power, 55-5000, KS profiles)")
    print("  train-cv", fmt(cv(*train, base, profile="ks")))
    for n, t in tests.items():
        print(f"  {n:9s}", fmt(held_out(train, t, base, profile="ks")))

    grid = {
        "variant": ["power", "mag", "log", "white", "framenorm"],
        "fmin": [27.5, 55.0, 110.0],
        "fmax": [2000.0, 3500.0, 5000.0],
        "harmonics": [1, 2, 4],
        "tuning": [False, True],
        "compress": [0.0, 100.0],
    }
    results = []
    for profile in ("ks", "learned"):
        for combo in itertools.product(*grid.values()):
            opts = dict(zip(grid, combo))
            results.append((cv(*train, opts, profile=profile)["exact"], profile, opts))
    results.sort(key=lambda r: -r[0])
    print("\nTOP 10 by train-cv exact")
    for acc, profile, opts in results[:10]:
        print(f"  {acc:.3f} {profile:8s} {opts}")
    best_ks = next(r for r in results if r[1] == "ks")
    print(f"\nbest with KS profiles: {best_ks[0]:.3f} {best_ks[2]}")

    acc, profile, opts = results[0]
    print("\nWINNER (chosen on train-cv only), held-out:")
    for mb in (0.0, 0.02, 0.05):
        print(f"  minor_bias={mb}: train-cv", fmt(cv(*train, opts, profile=profile, minor_bias=mb)))
    for n, t in tests.items():
        print(f"  {n:9s}", fmt(held_out(train, t, opts, profile=profile)))
    (OUT / "best.json").write_text(json.dumps({"profile": profile, "opts": opts, "train_cv_exact": acc}))


if __name__ == "__main__":
    main()
