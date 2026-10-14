"""Key-detection lab, stage 5: production check. Runs the real `iron.key` detectors on audio.

For every track of a manifest (written by key_lab_extract.py `sets` / `extra`), decode the body
window exactly as `iron.api.analyze` does and run both `iron.key.detect_key` (SS27 model) and
`iron.key.detect_key_profile` (SS18 baseline). Both are scored against the reference key with the
same metrics, in the same run, so before/after numbers are comparable. Writes per-track rows.

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

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import key_lab as kl

from iron import api
from iron import key as iron_key

_CAMELOT_TO_PAIR = {c: (iron_key.NOTES.index(k[:-3]), int(k.endswith("min")))
                    for k, c in iron_key.CAMELOT.items()}


def _one(row: dict) -> dict:
    out = dict(row)
    try:
        p = Path(row["audio"])
        start, span = api._pick_body_window(api._probe_duration(p))
        y, sr = api._decode(p, span, start=start)
        out["model"] = iron_key.detect_key(y, sr)
        out["profile"] = iron_key.detect_key_profile(y, sr)
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _pairs(rows: list[dict], field: str) -> tuple[np.ndarray, np.ndarray]:
    ok = [r for r in rows if r.get(field) and kl.parse_key(r["key"])]
    pred = np.array([_CAMELOT_TO_PAIR[r[field][0]] for r in ok])
    truth = np.array([kl.parse_key(r["key"]) for r in ok])
    return pred, truth


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
    for field, label in (("profile", "SS18 profiles (before)"), ("model", "SS27 model (after) ")):
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
    both = [r for r in res if "error" not in r and r.get("model") and r.get("profile") and kl.parse_key(r["key"])]
    pm, tm = _pairs(both, "model")
    pp, _ = _pairs(both, "profile")
    right_m, right_p = (pm == tm).all(1), (pp == tm).all(1)
    print(f"  paired: model right/profiles wrong {int((right_m & ~right_p).sum())}, "
          f"profiles right/model wrong {int((right_p & ~right_m).sum())}")
    a.out.write_text(json.dumps(dict(summary=summary, rows=res), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
