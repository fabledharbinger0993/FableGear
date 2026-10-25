"""Fresh held-out tempo measurement of the CURRENT Iron (docs/IRON_RESEARCH.md SS31).

Rekordbox has analysed about 57k unique tracks; every earlier tempo benchmark drew from the 20,096 of
SS21. This draws a seeded sample from the rest, runs `iron.analyze(want=("bpm",))` on each file in
place (read only), and scores against Rekordbox 7's BPM the way SS19 asks: exact (within 0.6 BPM, SS20's
definition), within 1%, 2% and 4%, and the ratio class of every miss. It also writes the Iron-wrong tracks at 160-185 BPM (half-time and 2:3) as an
ear-check list for the owner (SS22.4). Nothing in `iron/` changes.

    PYTHONPATH=fablegear venv/bin/python dev/scripts/experiments/tempo_holdout.py sample \
        --pool ~/FableGearTestbed/refresh2/tracks.json --exclude ~/FableGearTestbed/refresh/tracks.json \
        --n 3000 --out ~/FableGearTestbed/results/tempo_holdout_manifest.json
    ... tempo_holdout.py run --manifest ...manifest.json --out ~/FableGearTestbed/results/tempo_holdout.jsonl
    ... tempo_holdout.py score --run ~/FableGearTestbed/results/tempo_holdout.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "fablegear"))

EXACT_BPM = 0.6  # SS20's "exact": within 0.6 BPM of Rekordbox
RATIOS = [(0.5, "half"), (2.0, "double"), (2 / 3, "2:3"), (1.5, "3:2"), (0.75, "3:4"), (4 / 3, "4:3")]


def cmd_sample(pool: Path, exclude: Path, n: int, out: Path, seed: int) -> None:
    used = {t["path"] for t in json.loads(exclude.read_text())}
    used_sizes = {t["size"] for t in json.loads(exclude.read_text())}
    cand = [t for t in json.loads(pool.read_text()) if t["path"] not in used and t["size"] not in used_sizes]
    cand.sort(key=lambda t: t["path"])
    random.Random(seed).shuffle(cand)
    rows = cand[:n]
    out.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    print(f"held-out pool {len(cand)}, sampled {len(rows)} -> {out}")


def _one(t: dict) -> dict:
    logging.disable(logging.WARNING)
    import iron

    row = dict(path=t["path"], rb_bpm=t["bpm"], genre=t.get("genre"))
    t0 = time.time()
    try:
        res = iron.analyze(Path(t["path"]), want=("bpm",))
        row.update(bpm=res.bpm, conf=res.bpm_confidence)
    except Exception as exc:
        row["crash"] = f"{type(exc).__name__}: {exc}"
    row["seconds"] = round(time.time() - t0, 2)
    return row


def cmd_run(manifest: Path, out: Path, workers: int) -> None:
    rows = json.loads(manifest.read_text())
    done = set()
    if out.exists():
        done = {json.loads(line)["path"] for line in out.read_text().splitlines() if line.strip()}
    todo = [t for t in rows if t["path"] not in done]
    print(f"{len(done)} done, {len(todo)} to go", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as ex, out.open("a") as fh:
        futs = [ex.submit(_one, t) for t in todo]
        for i, f in enumerate(as_completed(futs), 1):
            fh.write(json.dumps(f.result(), ensure_ascii=False) + "\n")
            fh.flush()
            if i % 250 == 0:
                print(f"{i}/{len(todo)}", flush=True)


def ratio_class(bpm: float, ref: float) -> str | None:
    r = bpm / ref
    for target, name in RATIOS:
        if abs(r - target) / target <= 0.04:
            return name
    return None


def cmd_score(run: Path, earcheck: Path | None) -> None:
    rows = [json.loads(line) for line in run.read_text().splitlines() if line.strip()]
    ok = [r for r in rows if "crash" not in r and r.get("bpm")]
    n = len(ok)

    def within(r, tol):
        return abs(r["bpm"] - r["rb_bpm"]) / r["rb_bpm"] <= tol

    exact = sum(abs(r["bpm"] - r["rb_bpm"]) <= EXACT_BPM for r in ok)
    print(f"tracks {len(rows)}  scored {n}  crashes {sum('crash' in r for r in rows)}  "
          f"no tempo {len(rows) - n - sum('crash' in r for r in rows)}")
    print(f"exact (<= {EXACT_BPM} BPM) {exact} ({100 * exact / n:.1f}%)  "
          + "  ".join(f"within {int(t * 100)}% {100 * sum(within(r, t) for r in ok) / n:.1f}%" for t in (0.01, 0.02, 0.04)))
    misses = [r for r in ok if not within(r, 0.02)]
    cls = Counter(ratio_class(r["bpm"], r["rb_bpm"]) or "other" for r in misses)
    print(f"misses beyond 2%: {len(misses)}  by class: {dict(cls.most_common())}")
    band = Counter()
    for r in misses:
        band[int(r["rb_bpm"] // 20 * 20)] += 1
    print("misses by Rekordbox BPM band:", dict(sorted(band.items())))
    if earcheck is not None:
        picks = [dict(path=r["path"], rekordbox_bpm=r["rb_bpm"], iron_bpm=r["bpm"],
                      cls=ratio_class(r["bpm"], r["rb_bpm"]), owner_label=None)
                 for r in misses if 160 <= r["rb_bpm"] <= 185 and ratio_class(r["bpm"], r["rb_bpm"]) in ("half", "2:3")]
        earcheck.write_text(json.dumps(picks, indent=1, ensure_ascii=False))
        print(f"ear-check list: {len(picks)} tracks -> {earcheck}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--pool", type=Path, required=True)
    s.add_argument("--exclude", type=Path, required=True)
    s.add_argument("--n", type=int, default=3000)
    s.add_argument("--seed", type=int, default=20261020)
    s.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("run")
    r.add_argument("--manifest", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--workers", type=int, default=6)
    c = sub.add_parser("score")
    c.add_argument("--run", type=Path, required=True)
    c.add_argument("--earcheck", type=Path)
    a = ap.parse_args()
    if a.cmd == "sample":
        cmd_sample(a.pool, a.exclude, a.n, a.out, a.seed)
    elif a.cmd == "run":
        cmd_run(a.manifest, a.out, a.workers)
    else:
        cmd_score(a.run, a.earcheck)
