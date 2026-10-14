"""Key-detection lab, stage 3: candidate mechanisms, selected by cross-validation on train only.

Families (declared before any held-out number was looked at):
  whiten    the cached whitening variants (envelope width, threshold, compression), profiles relearned
  ensemble  mean score over several whitening variants
  bass      full-band profile score + beta * score of a bass-band chroma (own learned profile)
  mixture   k sub-profiles per mode (k-means on tonic-rotated chroma); score = best sub-profile
  bassfix   second stage for fifth-related top-2 pairs: pick the candidate with more bass energy

Selection: 5-fold CV on `train` (folds = index % 5; profiles refit inside each fold). The shipped
detector (`baseline`) is scored the same way. Held-out sets (rb200, mixed150, rbfresh) are scored
only for the CV-selected configuration of each family, plus the baseline.

Usage:
  PYTHONPATH=<worktree> venv/bin/python scripts/experiments/key_lab_exp.py --lab D --out D/exp.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import key_lab as kl

from iron import key as iron_key

SHIPPED_BIAS = iron_key._MINOR_BIAS
FOLDS = 5


def norm_rot(c: np.ndarray, truth: np.ndarray) -> np.ndarray:
    z = c / (c.sum(1, keepdims=True) + 1e-12)
    return np.stack([np.roll(v, -t) for v, (t, _) in zip(z, truth, strict=True)])


def kmeans(x: np.ndarray, k: int, iters: int = 30) -> np.ndarray:
    """Deterministic k-means (farthest-point init from the mean); returns (k, d) centres."""
    centres = [x.mean(0)]
    for _ in range(k - 1):
        d = np.min([((x - c) ** 2).sum(1) for c in centres], axis=0)
        centres.append(x[d.argmax()])
    cen = np.stack(centres)
    for _ in range(iters):
        lab = ((x[:, None, :] - cen[None]) ** 2).sum(2).argmin(1)
        new = np.stack([x[lab == j].mean(0) if np.any(lab == j) else cen[j] for j in range(k)])
        if np.allclose(new, cen):
            break
        cen = new
    return cen


class Model:
    """fit(feats, truth) / predict(feats) over a dict of (n, 12) chroma arrays."""

    def fit(self, f: dict[str, np.ndarray], truth: np.ndarray) -> Model:
        raise NotImplementedError

    def predict(self, f: dict[str, np.ndarray]) -> np.ndarray:
        raise NotImplementedError


class Profile(Model):
    """Pearson against learned profiles on one chroma key (the shipped method when key='mid')."""

    def __init__(self, key: str, bias: float):
        self.key, self.bias = key, bias

    def fit(self, f, truth):
        self.maj, self.mnr = kl.learn_profiles(f[self.key], truth)
        return self

    def scores(self, f):
        return kl.scores(f[self.key], self.maj, self.mnr)

    def predict(self, f):
        return kl.decide(self.scores(f), self.bias)


class Blend(Model):
    """Weighted sum of per-chroma profile scores: sum_k beta_k * pearson_k."""

    def __init__(self, keys: tuple[str, ...], betas: tuple[float, ...], bias: float):
        self.parts = [Profile(k, 0.0) for k in keys]
        self.betas, self.bias = betas, bias

    def fit(self, f, truth):
        for p in self.parts:
            p.fit(f, truth)
        return self

    def scores(self, f):
        return sum(b * p.scores(f) for b, p in zip(self.betas, self.parts, strict=True))

    def predict(self, f):
        return kl.decide(self.scores(f), self.bias * sum(self.betas))


class Mixture(Model):
    """k sub-profiles per mode; the score of a (tonic, mode) is the best sub-profile correlation."""

    def __init__(self, key: str, k: int, bias: float):
        self.key, self.k, self.bias = key, k, bias

    def fit(self, f, truth):
        r = norm_rot(f[self.key], truth)
        self.subs = []
        for mode in (0, 1):
            rows = r[truth[:, 1] == mode]
            kk = min(self.k, max(1, len(rows) // 25))
            cen = kmeans(rows, kk)
            self.subs.append([c / c[0] for c in cen])
        return self

    def scores(self, f):
        per = []
        for mode in (0, 1):
            best = None
            for prof in self.subs[mode]:
                s = kl.scores(f[self.key], prof, prof)[:, 12 * mode:12 * mode + 12]
                best = s if best is None else np.maximum(best, s)
            per.append(best)
        return np.concatenate(per, axis=1)

    def predict(self, f):
        return kl.decide(self.scores(f), self.bias)


class BassFix(Model):
    """Shipped decision, then for a top-2 pair whose tonics are a fifth apart in the same mode
    and whose scores are within `margin`, take the candidate whose tonic has more bass energy."""

    def __init__(self, bias: float, margin: float, bass_key: str = "bass"):
        self.base, self.margin, self.bass_key = Profile("mid", bias), margin, bass_key

    def fit(self, f, truth):
        self.base.fit(f, truth)
        return self

    def predict(self, f):
        sc = self.base.scores(f).copy()
        sc[:, 12:] += self.base.bias
        pred = kl.decide(self.base.scores(f), self.base.bias)
        order = np.argsort(-sc, axis=1)
        bass = f[self.bass_key]
        out = pred.copy()
        for i in range(len(sc)):
            a, b = order[i, 0], order[i, 1]
            ta, ma, tb, mb = a % 12, a // 12, b % 12, b // 12
            if ma == mb and (tb - ta) % 12 in (5, 7) and sc[i, a] - sc[i, b] < self.margin:
                pick = a if bass[i, ta] >= bass[i, tb] else b
                out[i] = (pick % 12, pick // 12)
        return out


def build_chromas(sets: dict[str, kl.Cache], cfg: dict) -> dict[str, dict[str, np.ndarray]]:
    """name -> {'mid': full-band chroma of cfg's variant, 'bass': bass chroma} for every set."""
    out = {}
    for name, cache in sets.items():
        spec = cache.spec(cfg.get("variant", "w1"))
        f = {"mid": kl.chroma(spec, **cfg.get("chroma", {}))}
        if "bass" in cfg:
            f["bass"] = kl.chroma(spec, harmonics=cfg["bass"].get("harmonics", 1), fmin=cfg["bass"]["fmin"],
                                  fmax=cfg["bass"]["fmax"], tuning=True)
        out[name] = f
    return out


def cv_eval(f: dict[str, np.ndarray], truth: np.ndarray, make) -> dict[str, float]:
    idx = np.arange(len(truth)) % FOLDS
    pred = np.zeros_like(truth)
    for k in range(FOLDS):
        tr, te = idx != k, idx == k
        model = make().fit({a: b[tr] for a, b in f.items()}, truth[tr])
        pred[te] = model.predict({a: b[te] for a, b in f.items()})
    return kl.metrics(pred, truth)


def run_family(name: str, configs: list[tuple[str, dict, Callable[[], Model]]], sets, log: dict) -> None:
    """configs: (label, chroma cfg, model factory). CV all, select best by (exact, mirex), score held-out."""
    rows = []
    cache: dict[str, dict] = {}
    for label, cfg, make in configs:
        ck = json.dumps(cfg, sort_keys=True)
        if ck not in cache:
            cache[ck] = build_chromas(sets, cfg)
        ch = cache[ck]
        m = cv_eval(ch["train"], sets["train"].truth, make)
        rows.append((m["exact_pct"], m["mirex"], label, ck, make, m))
        print(f"  [{name}] {label:46s} cv exact {m['exact_pct']:.1f}%  fifth {m['fifth_pct']:.1f}  mirex {m['mirex']:.3f}"
              f"  pred-minor {m['pred_minor_pct']:.0f}%")
    rows.sort(key=lambda r: (-r[0], -r[1]))
    best = rows[0]
    ch = cache[best[3]]
    print(f"  [{name}] SELECTED by train-CV: {best[2]}")
    model = best[4]().fit(ch["train"], sets["train"].truth)
    held = {}
    for s in kl.SETS:
        held[s] = kl.metrics(model.predict(ch[s]), sets[s].truth)
        print(f"      {s:9s} {kl.fmt(held[s])}")
    log[name] = {"selected": best[2], "cv": {r[2]: r[5] for r in rows}, "held_out": held}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--families", default="baseline,whiten,ensemble,bass,mixture,bassfix")
    a = ap.parse_args()
    sets = kl.load_sets(a.lab)
    log: dict = {}
    fams = a.families.split(",")
    biases = (0.0, 0.04, 0.08, 0.12)

    if "baseline" in fams:
        run_family("baseline", [("shipped recipe, bias 0.08", {}, lambda: Profile("mid", SHIPPED_BIAS))], sets, log)
    if "whiten" in fams:
        cfgs = [(f"{v} bias {b}", {"variant": v}, lambda b=b: Profile("mid", b))
                for v in sets["train"].variants for b in biases]
        run_family("whiten", cfgs, sets, log)
    if "bass" in fams:
        cfgs = []
        for (lo, hi), beta, b in itertools.product(((55, 165), (55, 260), (55, 400)), (0.25, 0.5, 1.0), (0.04, 0.08)):
            cfgs.append((f"bass {lo}-{hi} beta {beta} bias {b}", {"bass": {"fmin": lo, "fmax": hi}},
                         lambda beta=beta, b=b: Blend(("mid", "bass"), (1.0, beta), b)))
        run_family("bass", cfgs, sets, log)
    if "mixture" in fams:
        cfgs = [(f"k={k} bias {b}", {}, lambda k=k, b=b: Mixture("mid", k, b)) for k in (1, 2, 3, 4) for b in (0.04, 0.08)]
        run_family("mixture", cfgs, sets, log)
    if "bassfix" in fams:
        cfgs = [(f"margin {m} bass {lo}-{hi}", {"bass": {"fmin": lo, "fmax": hi}}, lambda m=m: BassFix(SHIPPED_BIAS, m))
                for m in (0.02, 0.05, 0.1, 0.2) for lo, hi in ((55, 165), (55, 260))]
        run_family("bassfix", cfgs, sets, log)
    a.out.write_text(json.dumps(log, indent=1))


if __name__ == "__main__":
    main()
