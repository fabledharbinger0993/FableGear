"""Key-detection lab, stage 5: production check. Runs the real `iron.key` detectors on audio.

Three arms per track: the SS18 profile detector and the SS27 model (frozen weights from docs/iron/benchmarks)
on the tempo body window, and the current production path `iron.analyze` (whole-track key window, SS28).

Every arm is scored against the reference key with the same metrics, over the same rows, in the same run,
so before/after numbers are comparable. Writes per-track rows.

Usage:
  PYTHONPATH=<worktree> venv/bin/python scripts/experiments/key_lab_live.py \
      --manifest D/confirm.json --out D/live_confirm.json --workers 5
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import key_lab as kl

from iron import api
from iron import key as iron_key

_CAMELOT_TO_PAIR = {c: (iron_key.NOTES.index(k[:-3]), int(k.endswith("min")))
                    for k, c in iron_key.CAMELOT.items()}


_PREV = json.loads((Path(__file__).resolve().parents[2] / "docs/iron/benchmarks/key_model_ss27_weights.json").read_text())


def _one(row: dict) -> dict:
    out = dict(row)
    try:
        p = Path(row["audio"])
        start, span = api._pick_body_window(api._probe_duration(p))
        y, sr = api._decode(p, span, start=start)
        out["profile"] = iron_key.detect_key_profile(y, sr)  # SS18, body window
        with mock.patch.multiple(
            iron_key,
            _MODEL_W=np.array(_PREV["W"]).reshape(2, 4, 12),
            _MODEL_B=np.array(_PREV["b"]),
            _MINOR_SHIFT=_PREV["minor_shift"],
        ):
            out["prev"] = iron_key.detect_key(y, sr)  # SS27 model, body window
        res = api.analyze(p, want=("initial_key",))  # production: current model, whole track
        out["model"] = (res.initial_key, res.key_confidence) if res.initial_key else None
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _pairs(rows: list[dict], field: str) -> tuple[np.ndarray, np.ndarray]:
    ok = [r for r in rows if r.get(field) and kl.parse_key(r["key"])]
    pred = np.array([_CAMELOT_TO_PAIR[r[field][0]] for r in ok])
    truth = np.array([kl.parse_key(r["key"]) for r in ok])
    return pred, truth


def _correctness(rows: list[dict], field: str) -> np.ndarray:
    return np.array([
        bool(r.get(field))
        and _CAMELOT_TO_PAIR.get(r[field][0]) == kl.parse_key(r["key"])
        for r in rows
    ])


def _mcnemar(a_correct: np.ndarray, b_correct: np.ndarray) -> tuple[int, int, float]:
    from math import comb

    a_only = int((a_correct & ~b_correct).sum())
    b_only = int((b_correct & ~a_correct).sum())
    n = a_only + b_only
    if n == 0:
        return a_only, b_only, 1.0
    p = min(1.0, 2 * sum(comb(n, i) for i in range(min(a_only, b_only) + 1)) / 2**n)
    return a_only, b_only, p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=5)
    a = ap.parse_args()
    rows = json.loads(a.manifest.read_text())
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        res = list(ex.map(_one, rows, chunksize=4))
    errors = [r for r in res if "error" in r]
    print(f"{a.manifest.name}: {len(res)} tracks, {len(errors)} errors")
    summary = {}
    valid = [r for r in res if "error" not in r and kl.parse_key(r["key"])]  # same denominator for both detectors
    for field, label in (("profile", "SS18 profiles          "), ("prev", "SS27 model, body window"), ("model", "current iron.analyze    ")):
        pred, truth = _pairs(valid, field)
        m = kl.metrics(pred, truth)
        # A detector that returns no key is a miss, not a row to drop: report over every valid row too.
        m["n_valid"], m["undetected"] = len(valid), len(valid) - len(pred)
        m["exact_of_total_pct"] = 100.0 * m["exact"] / len(valid)
        m["mirex_of_total"] = m["mirex"] * len(pred) / len(valid)
        summary[field] = m
        print(f"  {label} {kl.fmt(m)}")
        print(f"  {'':22s} undetected {m['undetected']} of {len(valid)}; exact of all valid rows {m['exact_of_total_pct']:.1f}%, "
              f"MIREX of all valid rows {m['mirex_of_total']:.3f}")
    right_m, right_v = _correctness(valid, "model"), _correctness(valid, "prev")
    current_only, previous_only, p = _mcnemar(right_m, right_v)
    print(f"  paired current vs SS27: current right/SS27 wrong {current_only}, "
          f"SS27 right/current wrong {previous_only}, McNemar p={p:.4f} (n={len(valid)})")
    a.out.write_text(json.dumps(dict(summary=summary, rows=res), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
