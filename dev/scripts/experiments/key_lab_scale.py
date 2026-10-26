"""Key-detection lab, stage 5 (docs/IRON_RESEARCH.md SS31): does a larger training pool help?

Rekordbox has now analysed about 57k unique tracks of the owner's library (SS28 trained on 5,297).
Two families, both tonic-equivariant over the shipped four whole-track chroma views (w1, w05, w2, mag;
optionally the same four views on each half of the track):

  linear  score(t, m) = W[m] . x_t + b[m]                       (the shipped model's form)
  net     score(t, m) = W[m] . x_t + v[m] . tanh(A x_t + c) + b[m]   (one hidden layer, H units,
          shared by all twelve tonics; SS28.2 tried H = 24 at n = 2,795-5,295)

with x_t the views rolled so tonic t is index 0. Softmax over 24 keys, L2-regularised
cross-entropy, L-BFGS (numpy/scipy only).

SELECTION RULE, declared before any untouched number was computed:
  * Pools: T0 = train+train2+train3 (the shipped model's 5,297 tracks), T1 = T0+train4, T2 = T1+train5.
  * Every configuration (family, views, H, lam) is scored on a fixed 20% validation split of the
    pool it was fitted on (seeded; one seed per net configuration), never on an evaluation set.
  * The candidate is the best-validating configuration on T2; it is refitted on all of T2 (net: three
    seeds, logits averaged).
  * confirm4 (3,000 tracks drawn 2026-10-20, never scored before) is the untouched test. SHIP only
    if the candidate beats the shipped detector on confirm4 by >= 1.0 point with McNemar p < 0.01
    and does not lose on confirm2+confirm3 pooled (SS28's untouched sets, not trained on).
  * Every other arm is reported on every set for transparency; none of them is the decision.

Usage:
  PYTHONPATH=fablegear venv/bin/python dev/scripts/experiments/key_lab_scale.py --lab ~/FableGearTestbed/keylab \
      --out ~/FableGearTestbed/keylab/scale.json [--export ~/FableGearTestbed/keylab/scale_model.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from math import comb
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "fablegear"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import key_lab as kl

from iron import key as iron_key

VIEW_VARIANTS = ("w1", "w05", "w2", "mag")
TRAIN_SETS = ("train", "train2", "train3", "train4", "train5")
POOLS = {"T0": ("train", "train2", "train3"), "T1": ("train", "train2", "train3", "train4"),
         "T2": TRAIN_SETS}
EVAL_SETS = ("confirm4", "confirm2", "confirm3", "rbfresh", "rb200", "mixed150")
SUFFIX = "_whole"
SEED = 20261020


def _zc(c: np.ndarray) -> np.ndarray:
    c = c - c.mean(1, keepdims=True)
    return c / (np.linalg.norm(c, axis=1, keepdims=True) + 1e-12)


def load_chroma(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(whole (n,4,12), halves (n,8,12), truth (n,2)) without keeping the spectra in memory."""
    z = np.load(path, allow_pickle=False)
    variants = [str(v) for v in z["variants"]]
    meta = [json.loads(str(m)) for m in z["meta"]]
    truth = [kl.parse_key(m["key"]) for m in meta]
    keep = np.array([t is not None for t in truth])
    feats = z["feats"]
    whole, halves = [], []
    for v in VIEW_VARIANTS:
        f = feats[:, variants.index(v)].astype(np.float64)[keep]
        whole.append(_zc(kl.chroma(f.sum(1))))
        n_seg = f.shape[1]
        halves.append(_zc(kl.chroma(f[:, : n_seg // 2].sum(1))))
        halves.append(_zc(kl.chroma(f[:, n_seg // 2:].sum(1))))
    return np.stack(whole, 1), np.stack(halves, 1), np.array([t for t in truth if t is not None])


def rotated(F: np.ndarray) -> np.ndarray:
    """(n, G, 12) -> (n, 12, G*12), g-major (the shipped weights' order)."""
    return np.stack([np.concatenate([np.roll(F[:, g], -t, axis=1) for g in range(F.shape[1])], 1)
                     for t in range(12)], 1).astype(np.float64)


def shipped_logits(X: np.ndarray) -> np.ndarray:
    W = iron_key._MODEL_W.reshape(2, -1)
    s = np.einsum("ntd,md->ntm", X[:, :, : W.shape[1]], W) + iron_key._MODEL_B
    s[:, :, 1] += iron_key._MINOR_SHIFT
    return s


# ─── Models ─────────────────────────────────────────────────────────────────

def _softmax_grad(s: np.ndarray, truth: np.ndarray) -> tuple[float, np.ndarray]:
    n = len(truth)
    s = s - s.max((1, 2), keepdims=True)
    p = np.exp(s)
    p /= p.sum((1, 2), keepdims=True)
    nll = -float(np.log(p[np.arange(n), truth[:, 0], truth[:, 1]] + 1e-12).sum())
    p[np.arange(n), truth[:, 0], truth[:, 1]] -= 1.0
    return nll, p


def fit_linear(X, truth, lam):
    n, _, d = X.shape
    reg = lam * n / 100.0
    Xf = X.reshape(-1, d)

    def f(w):
        W, b = w[: 2 * d].reshape(2, d), w[2 * d:]
        with np.errstate(all="ignore"):  # Accelerate BLAS raises spurious matmul flags
            nll, g = _softmax_grad((Xf @ W.T).reshape(n, 12, 2) + b, truth)
            gw = g.reshape(-1, 2).T @ Xf + reg * W
        return nll + 0.5 * reg * float((W ** 2).sum()), np.concatenate([gw.ravel(), g.sum((0, 1))])

    r = minimize(f, np.zeros(2 * d + 2), jac=True, method="L-BFGS-B", options={"maxiter": 500})
    return ("linear", r.x[: 2 * d].reshape(2, d), r.x[2 * d:])


def logits_linear(model, X):
    _, W, b = model
    with np.errstate(all="ignore"):
        return (X.reshape(-1, X.shape[2]) @ W.T).reshape(len(X), 12, 2) + b


def _net_sizes(d: int, H: int):
    return [(2, d), (H, d), (H,), (2, H), (2,)]


def _unpack(w, sizes):
    out, i = [], 0
    for s in sizes:
        k = int(np.prod(s))
        out.append(w[i:i + k].reshape(s))
        i += k
    return out


def net_loss(w, Xf, truth, n, d, H, reg):
    W, A, c, v, b = _unpack(w, _net_sizes(d, H))
    with np.errstate(all="ignore"):
        h = np.tanh(Xf @ A.T + c)
        s = (Xf @ W.T + h @ v.T).reshape(n, 12, 2) + b
        nll, g = _softmax_grad(s, truth)
        gf = g.reshape(-1, 2)
        gW = gf.T @ Xf + reg * W
        gv = gf.T @ h + reg * v
        dz = (gf @ v) * (1 - h ** 2)
        gA = dz.T @ Xf + reg * A
    gc = dz.sum(0)
    pen = 0.5 * reg * float((W ** 2).sum() + (A ** 2).sum() + (v ** 2).sum())
    return nll + pen, np.concatenate([gW.ravel(), gA.ravel(), gc, gv.ravel(), g.sum((0, 1))])


def fit_net(X, truth, H, lam, seed, maxiter=300):
    n, _, d = X.shape
    reg = lam * n / 100.0
    rng = np.random.default_rng(seed)
    w0 = np.concatenate([np.zeros(2 * d), rng.normal(0, 1 / np.sqrt(d), H * d), np.zeros(H),
                         rng.normal(0, 0.1, 2 * H), np.zeros(2)])
    Xf = X.reshape(-1, d)
    r = minimize(net_loss, w0, args=(Xf, truth, n, d, H, reg), jac=True, method="L-BFGS-B",
                 options={"maxiter": maxiter})
    return ("net", *_unpack(r.x, _net_sizes(d, H)))


def logits_net(model, X):
    _, W, A, c, v, b = model
    Xf = X.reshape(-1, X.shape[2])
    with np.errstate(all="ignore"):
        h = np.tanh(Xf @ A.T + c)
        return (Xf @ W.T + h @ v.T).reshape(len(X), 12, 2) + b


def logits_of(models, X):
    return np.mean([logits_linear(m, X) if m[0] == "linear" else logits_net(m, X) for m in models], 0)


def predict(s: np.ndarray) -> np.ndarray:
    i = s.reshape(len(s), -1).argmax(1)
    return np.stack([i // 2, i % 2], 1)


def mcnemar(base_ok, new_ok):
    gain, loss = int((new_ok & ~base_ok).sum()), int((base_ok & ~new_ok).sum())
    n = gain + loss
    p = 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, i) for i in range(min(gain, loss) + 1)) / 2 ** n)
    return gain, loss, p


# ─── Experiment ─────────────────────────────────────────────────────────────

CONFIGS = (
    [dict(family="linear", views="whole", lam=lam) for lam in (0.003, 0.01, 0.03, 0.1)]
    + [dict(family="linear", views="whole+halves", lam=lam) for lam in (0.01, 0.03, 0.1)]
    + [dict(family="net", views=v, H=H, lam=lam)
       for v in ("whole", "whole+halves") for H in (24, 48, 96) for lam in (0.01, 0.03, 0.1)]
)


def _X(data, names, views):
    parts = [data[n]["whole"] if views == "whole" else np.concatenate([data[n]["whole"], data[n]["halves"]], 1)
             for n in names]
    return rotated(np.concatenate(parts))


def fit_config(cfg, X, y, seeds=(0,)):
    if cfg["family"] == "linear":
        return [fit_linear(X, y, cfg["lam"])]
    return [fit_net(X, y, cfg["H"], cfg["lam"], SEED + s) for s in seeds]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--export", type=Path)
    ap.add_argument("--pools", default="T0,T1,T2")
    ap.add_argument("--scan-only", action="store_true",
                    help="score the pools' configurations on their validation splits and stop (no selection)")
    a = ap.parse_args()

    t0 = time.time()
    data = {}
    pools = a.pools.split(",")
    needed = dict.fromkeys(n for p in pools for n in POOLS[p])
    for name in (*needed, *([] if a.scan_only else EVAL_SETS)):
        whole, halves, truth = load_chroma(a.lab / f"{name}{SUFFIX}.npz")
        data[name] = dict(whole=whole, halves=halves, truth=truth)
        print(f"loaded {name}: {len(truth)} tracks ({time.time() - t0:.0f}s)", flush=True)

    report: dict = {"configs": {}, "selected": None, "eval": {}}
    for pool in pools:
        names = POOLS[pool]
        y = np.concatenate([data[n]["truth"] for n in names])
        idx = np.random.default_rng(SEED + len(y)).permutation(len(y))
        n_val = len(y) // 5
        val, tr = idx[:n_val], idx[n_val:]
        print(f"\n== pool {pool}: {len(y)} tracks (train {len(tr)}, validation {len(val)}), "
              f"minor {100 * y[:, 1].mean():.0f}%", flush=True)
        cache: dict[str, np.ndarray] = {}
        for cfg in CONFIGS:
            Xall = cache.setdefault(cfg["views"], _X(data, names, cfg["views"]))
            t1 = time.time()
            models = fit_config(cfg, Xall[tr], y[tr])
            acc = float((predict(logits_of(models, Xall[val])) == y[val]).all(1).mean() * 100)
            key = f"{pool} {cfg}"
            report["configs"][key] = dict(pool=pool, cfg=cfg, val_exact=acc, seconds=round(time.time() - t1))
            print(f"  {cfg}  val exact {acc:.2f}%  ({time.time() - t1:.0f}s)", flush=True)
        # the shipped model on this pool's validation split, for reference
        Xw = cache["whole"]
        ship_acc = float((predict(shipped_logits(Xw[val])) == y[val]).all(1).mean() * 100)
        report["configs"][f"{pool} shipped"] = dict(pool=pool, cfg="shipped", val_exact=ship_acc)
        print(f"  shipped detector on this validation split: {ship_acc:.2f}% (it was trained on T0, so on "
              f"T0 this number is in-sample)", flush=True)
        a.out.write_text(json.dumps(report, indent=1))

    if a.scan_only:
        print(f"scan only: done in {time.time() - t0:.0f}s", flush=True)
        return
    final_pool = pools[-1]
    cands = [v for v in report["configs"].values() if v["pool"] == final_pool and v["cfg"] != "shipped"]
    best = max(cands, key=lambda v: v["val_exact"])
    cfg = best["cfg"]
    print(f"\nSELECTED (best validation on {final_pool}): {cfg}  val {best['val_exact']:.2f}%", flush=True)
    names = POOLS[final_pool]
    y = np.concatenate([data[n]["truth"] for n in names])
    final = fit_config(cfg, _X(data, names, cfg["views"]), y, seeds=(0, 1, 2))
    report["selected"] = dict(pool=final_pool, cfg=cfg, val_exact=best["val_exact"], n_train=len(y))

    # Also refit the shipped form (linear, whole views) on the final pool: "more data, same model".
    lin_cands = [v for v in cands if v["cfg"]["family"] == "linear" and v["cfg"]["views"] == "whole"]
    lin_cfg = max(lin_cands, key=lambda v: v["val_exact"])["cfg"]
    lin = fit_config(lin_cfg, _X(data, names, "whole"), y)

    for name in EVAL_SETS:
        d = data[name]
        yt = d["truth"]
        base = predict(shipped_logits(rotated(d["whole"])))
        new = predict(logits_of(final, _X(data, [name], cfg["views"])))
        lin_pred = predict(logits_of(lin, rotated(d["whole"])))
        b_ok, n_ok, l_ok = (base == yt).all(1), (new == yt).all(1), (lin_pred == yt).all(1)
        g, lo, p = mcnemar(b_ok, n_ok)
        gl, ll, pl = mcnemar(b_ok, l_ok)
        report["eval"][name] = dict(
            n=len(yt), shipped=float(b_ok.mean() * 100), selected=float(n_ok.mean() * 100),
            selected_fixed=g, selected_broke=lo, selected_p=p,
            linear_more_data=float(l_ok.mean() * 100), linear_fixed=gl, linear_broke=ll, linear_p=pl,
            shipped_metrics=kl.metrics(base, yt), selected_metrics=kl.metrics(new, yt),
        )
        print(f"{name:9s} n={len(yt):5d}  shipped {b_ok.mean() * 100:5.1f}%  selected {n_ok.mean() * 100:5.1f}% "
              f"(+{g}/-{lo}, p={p:.2g})  linear@{final_pool} {l_ok.mean() * 100:5.1f}% (+{gl}/-{ll}, p={pl:.2g})",
              flush=True)

    c = report["eval"]["confirm4"]
    c23_base = (report["eval"]["confirm2"]["shipped"] * report["eval"]["confirm2"]["n"]
                + report["eval"]["confirm3"]["shipped"] * report["eval"]["confirm3"]["n"])
    c23_new = (report["eval"]["confirm2"]["selected"] * report["eval"]["confirm2"]["n"]
               + report["eval"]["confirm3"]["selected"] * report["eval"]["confirm3"]["n"])
    ship = (c["selected"] - c["shipped"] >= 1.0 and c["selected_p"] < 0.01 and c23_new >= c23_base)
    report["ship_decision"] = dict(ship=ship, confirm4_gain=c["selected"] - c["shipped"], confirm4_p=c["selected_p"],
                                   confirm23_gain_tracks=round((c23_new - c23_base) / 100))
    print(f"\nSHIP RULE: {'PASS' if ship else 'FAIL'}  (confirm4 {c['selected'] - c['shipped']:+.2f} points, "
          f"p={c['selected_p']:.2g}; confirm2+3 {round((c23_new - c23_base) / 100):+d} tracks)", flush=True)
    a.out.write_text(json.dumps(report, indent=1))
    if a.export:
        def _ser(m):
            return [m[0]] + [np.asarray(x).tolist() for x in m[1:]]
        a.export.write_text(json.dumps(dict(cfg=cfg, views=list(VIEW_VARIANTS), models=[_ser(m) for m in final],
                                            n_train=len(y)), indent=None))
    print(f"done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
