"""Key-detection lab, stage 4: a tonic-equivariant linear model over several chroma views.

Score of class (tonic t, mode m) = sum_g W[m, g] . roll(F_g, -t) + b[m], with F_g the zero-mean,
unit-norm 12-bin chroma of view g (a whitening width, a band, ...). Twelve rotations share one
weight vector per mode, so the model has only 2 * 12 * G + 2 parameters. Fitted by L2-regularised
softmax cross-entropy (L-BFGS, numpy/scipy only). The shipped detector is the special case of one
view, Pearson scoring and learned profiles; this model keeps the learned-profile idea but lets the
weights be discriminative and lets several whitening views vote.

Selection discipline (declared before held-out numbers were read):
  * view set, regularisation `lam` and a minor-logit shift are chosen by 5-fold CV on train+train2;
  * the held-out sets (rb200, mixed150, rbfresh) are scored for the selected configuration and for
    the shipped detector; `confirm` is scored once, after the configuration is frozen.

Usage:
  PYTHONPATH=<worktree> venv/bin/python scripts/experiments/key_lab_logit.py --lab D --out D/logit.json
  # SS28 (whole-track features, the shipped fit):
  ... key_lab_logit.py --lab D --out D/logit_whole.json --train train,train2,train3 --suffix _whole \
      --only "S3" --confirm confirm2,confirm3 --export D/model_whole.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import key_lab as kl

from iron import key as iron_key

# A view: which cached whitening variant, and which chroma settings (key_lab.chroma kwargs).
BASS = dict(fmin=55.0, fmax=260.0, harmonics=1)
VIEWS: dict[str, dict] = {
    "w1": dict(variant="w1"),
    "w05": dict(variant="w05"),
    "w2": dict(variant="w2"),
    "mag": dict(variant="mag"),
    "bass": dict(variant="w1", kw=BASS),
    "t25": dict(variant="w1_t25"),
    "sqrt": dict(variant="w1_sqrt"),
}
VIEW_SETS: dict[str, tuple[str, ...]] = {
    "S1 w1": ("w1",),
    "S2 w1,w05,w2": ("w1", "w05", "w2"),
    "S3 +mag": ("w1", "w05", "w2", "mag"),
    "S4 +bass": ("w1", "w05", "w2", "mag", "bass"),
    "S5 +t25,sqrt": ("w1", "w05", "w2", "mag", "bass", "t25", "sqrt"),
}
LAMS = (0.03, 0.1, 0.3, 1.0)
SHIFTS = (-1.0, -0.5, -0.25, 0.0)
FOLDS = 5


def _zc(c: np.ndarray) -> np.ndarray:
    c = c - c.mean(1, keepdims=True)
    return c / (np.linalg.norm(c, axis=1, keepdims=True) + 1e-12)


def view_chroma(cache: kl.Cache, name: str) -> np.ndarray:
    v = VIEWS[name]
    return _zc(kl.chroma(cache.spec(v["variant"]), **v.get("kw", {})))


def rotated(F: np.ndarray) -> np.ndarray:
    """(n, G, 12) -> (n, 12, G*12): class tonic t sees every view rolled by -t."""
    return np.stack(
        [np.concatenate([np.roll(F[:, g], -t, axis=1) for g in range(F.shape[1])], axis=1) for t in range(12)], 1)


def fit(X: np.ndarray, truth: np.ndarray, lam: float) -> tuple[np.ndarray, np.ndarray]:
    n, _, d = X.shape
    yt, ym = truth[:, 0], truth[:, 1]
    reg = lam * n / 100.0

    def f(w: np.ndarray) -> tuple[float, np.ndarray]:
        W, b = w[: 2 * d].reshape(2, d), w[2 * d:]
        s = np.einsum("ntd,md->ntm", X, W) + b
        s -= s.max((1, 2), keepdims=True)
        p = np.exp(s)
        p /= p.sum((1, 2), keepdims=True)
        nll = -np.log(p[np.arange(n), yt, ym] + 1e-12).sum()
        g = p
        g[np.arange(n), yt, ym] -= 1.0
        gw = np.einsum("ntm,ntd->md", g, X) + reg * W
        return nll + 0.5 * reg * float((W**2).sum()), np.concatenate([gw.ravel(), g.sum((0, 1))])

    res = minimize(f, np.zeros(2 * d + 2), jac=True, method="L-BFGS-B", options={"maxiter": 400})
    return res.x[: 2 * d].reshape(2, d), res.x[2 * d:]


def logits(X: np.ndarray, model: tuple[np.ndarray, np.ndarray], shift: float = 0.0) -> np.ndarray:
    W, b = model
    s = np.einsum("ntd,md->ntm", X, W) + b
    s[:, :, 1] += shift
    return s


def predict(X: np.ndarray, model, shift: float = 0.0) -> np.ndarray:
    i = logits(X, model, shift).reshape(len(X), -1).argmax(1)
    return np.stack([i // 2, i % 2], 1)


def cv_predictions(
    X: np.ndarray, truth: np.ndarray, lam: float
) -> list[tuple[np.ndarray, tuple[np.ndarray, np.ndarray]]]:
    """Per fold: (test-row mask, model fitted on the other folds); the caller applies each shift."""
    idx = np.arange(len(truth)) % FOLDS
    out = []
    for k in range(FOLDS):
        tr, te = idx != k, idx == k
        out.append((te, fit(X[tr], truth[tr], lam)))
    return out


def mcnemar(a_correct: np.ndarray, b_correct: np.ndarray) -> tuple[int, int, float]:
    """b_only, a_only, exact two-sided p (binomial) for paired correctness vectors."""
    from math import comb

    b_only = int((b_correct & ~a_correct).sum())
    a_only = int((a_correct & ~b_correct).sum())
    n = a_only + b_only
    if n == 0:
        return b_only, a_only, 1.0
    k = min(a_only, b_only)
    p = min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2**n)
    return b_only, a_only, p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--train", default="train,train2", help="comma-separated training sets")
    ap.add_argument("--confirm", default="", help="comma-separated untouched sets to score once, after selection")
    ap.add_argument("--suffix", default="", help="cache suffix: '' = tempo body window, '_whole' = whole track (SS28)")
    ap.add_argument("--export", type=Path, help="write the selected model's weights as JSON")
    ap.add_argument("--only", help="restrict the search to the view set whose name starts with this (sensitivity runs)")
    a = ap.parse_args()

    confirm = [c for c in a.confirm.split(",") if c]
    names = tuple(dict.fromkeys([*a.train.split(","), *kl.SETS, *confirm]))
    loaded = kl.load_sets(a.lab, tuple(n + a.suffix for n in names))
    sets = {n: loaded[n + a.suffix] for n in names}
    train_parts = a.train.split(",")
    truth = np.concatenate([sets[t].truth for t in train_parts])
    print(f"training tracks: {len(truth)} ({', '.join(train_parts)}); minor {100 * truth[:, 1].mean():.0f}%")

    chroma_cache: dict[tuple[str, str], np.ndarray] = {}

    def F(setname: str, view_names: tuple[str, ...]) -> np.ndarray:
        for v in view_names:
            if (setname, v) not in chroma_cache:
                chroma_cache[(setname, v)] = view_chroma(sets[setname], v)
        return np.stack([chroma_cache[(setname, v)] for v in view_names], 1)

    def X_of(setname: str | list[str], view_names: tuple[str, ...]) -> np.ndarray:
        parts = [setname] if isinstance(setname, str) else setname
        return rotated(np.concatenate([F(p, view_names) for p in parts]))

    view_sets = {k: v for k, v in VIEW_SETS.items() if not a.only or k.startswith(a.only)}
    rows = []
    for (vs_name, view_names), lam in itertools.product(view_sets.items(), LAMS):
        Xtr = X_of(train_parts, view_names)
        folds = cv_predictions(Xtr, truth, lam)
        for shift in SHIFTS:
            pred = np.zeros_like(truth)
            for te, model in folds:
                pred[te] = predict(Xtr[te], model, shift)
            m = kl.metrics(pred, truth)
            rows.append((m["exact_pct"], m["mirex"], vs_name, lam, shift, m))
        best_shift = max((r for r in rows if r[2] == vs_name and r[3] == lam), key=lambda r: (r[0], r[1]))
        print(f"{vs_name:14s} lam {lam:<5} best shift {best_shift[4]:+.2f}: cv exact {best_shift[0]:.1f}% "
              f"fifth {best_shift[5]['fifth_pct']:.1f} mirex {best_shift[1]:.3f} pred-minor {best_shift[5]['pred_minor_pct']:.0f}%",
              flush=True)

    rows.sort(key=lambda r: (-r[0], -r[1]))
    _, _, vs_name, lam, shift, cvm = rows[0]
    view_names = VIEW_SETS[vs_name]
    print(f"\nSELECTED by train-CV: views [{vs_name}] lam {lam} minor shift {shift:+.2f}  (cv exact {cvm['exact_pct']:.1f}%)")
    model = fit(X_of(train_parts, view_names), truth, lam)

    report = {"selected": dict(views=list(view_names), lam=lam, shift=shift, cv=cvm), "held_out": {}}
    held = [*kl.SETS, *confirm]
    for s in held:
        c = sets[s]
        pred = predict(X_of(s, view_names), model, shift)
        base = kl.shipped_predict(c)
        m, mb = kl.metrics(pred, c.truth), kl.metrics(base, c.truth)
        gain, loss, p = mcnemar((base == c.truth).all(1), (pred == c.truth).all(1))
        print(f"{s:9s} model   {kl.fmt(m)}\n{'':9s} shipped {kl.fmt(mb)}\n{'':9s} paired: model right/shipped wrong {gain}, "
              f"shipped right/model wrong {loss}, McNemar p={p:.4f}")
        report["held_out"][s] = dict(model=m, shipped=mb, gains=gain, losses=loss, p=p)
    a.out.write_text(json.dumps(report, indent=1))
    if a.export:
        W, b = model
        a.export.write_text(json.dumps(dict(views=list(view_names), W=W.tolist(), b=b.tolist(), shift=shift,
                                             lam=lam, minor_bias_shipped=iron_key._MINOR_BIAS), indent=1))


if __name__ == "__main__":
    main()
