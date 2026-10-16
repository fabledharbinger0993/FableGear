"""
Refresh the Rekordbox benchmark: every track the live Rekordbox 7 has analysed now (BPM, key, Analysed set),
matched to its file by exact path, then current Iron run on each file IN PLACE (read-only: nothing copied,
nothing written to Passport or the DB).

Rekordbox is the reference. Its BPM and key come from the snapshot row for the exact path; the Passport
tag values are not used.

    venv/bin/python harness/refresh_rb_bench.py snap                 # copy master.db (+wal, +shm), list tracks
    venv/bin/python harness/refresh_rb_bench.py run [--workers 6]    # analyse in place, resumable jsonl
    venv/bin/python harness/refresh_rb_bench.py score                # accuracy + miss breakdown

The Iron code is taken from the repo checkout named by --repo (default ~/FableGear).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

logging.disable(logging.WARNING)

REKORDBOX = Path.home() / "Library/Pioneer/rekordbox"
BED = Path.home() / "FableGearTestbed"
REFRESH = BED / "refresh"
SNAP = REFRESH / "snap"
TRACKS = REFRESH / "tracks.json"
RESULTS = BED / "results"
DEFAULT_REPO = Path.home() / "FableGear"
FLAT = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}

# Ratio classes for remaining BPM misses (detected / true). Tolerance 4%, same as MIREX.
RATIO_CLASSES = [
    (0.5, "half (detected = true/2)"),
    (2.0, "double (detected = 2x true)"),
    (2 / 3, "2:3 (detected = 2/3 true)"),
    (1.5, "3:2 (detected = 3/2 true)"),
    (0.75, "3:4 (detected = 3/4 true)"),
    (4 / 3, "4:3 (detected = 4/3 true)"),
]
RATIO_TOL = 0.04


def snapshot() -> Path:
    """Copy the DB, WAL and SHM. Reads the live file only; never writes to it."""
    SNAP.mkdir(parents=True, exist_ok=True)
    for name in ("master.db", "master.db-wal", "master.db-shm"):
        src = REKORDBOX / name
        if src.exists():
            shutil.copy2(src, SNAP / name)
    return SNAP / "master.db"


def rb_to_camelot(k: str | None, camelot: dict[str, str]) -> str | None:
    if not k:
        return None
    minor = k.endswith("m")
    root = k[:-1] if minor else k
    root = FLAT.get(root, root)
    return camelot.get(f"{root}{'min' if minor else 'maj'}")


def ratio_class(detected: float, true_bpm: float) -> str | None:
    ratio = detected / true_bpm
    for target, label in RATIO_CLASSES:
        if abs(ratio - target) / target <= RATIO_TOL:
            return label
    return None


def cmd_snap(repo: Path) -> None:
    from pyrekordbox import Rekordbox6Database

    sys.path.insert(0, str(repo))
    from iron.key import CAMELOT

    db_path = snapshot()
    db = Rekordbox6Database(str(db_path), unlock=True)
    prior = {
        "live_300": {m["path"] for m in json.loads((BED / "live/manifest.json").read_text())},
        "rb200": {m["path"] for m in json.loads((BED / "rb200/manifest.json").read_text())},
        "iron_db_1993": {json.loads(line)["path"] for line in (RESULTS / "iron_db_2000.jsonl").read_text().splitlines() if line.strip()},
    }
    tracks, seen, dup = [], set(), 0
    stats = Counter()
    for r in db.get_content().all():
        key = getattr(r.Key, "ScaleName", None)
        stats["rows"] += 1
        if not (r.BPM and key and r.Analysed and r.FolderPath):
            stats["not_qualifying"] += 1
            continue
        if not os.path.exists(r.FolderPath):
            stats["qualifying_file_missing"] += 1
            continue
        size = os.path.getsize(r.FolderPath)
        if (size, r.Length) in seen:
            dup += 1  # same bytes under another content id, as in live_collect.py
            continue
        seen.add((size, r.Length))
        tracks.append(dict(
            rb_id=str(r.ID), path=r.FolderPath, bpm=r.BPM / 100, key=key,
            true_camelot=rb_to_camelot(key, CAMELOT), genre=getattr(r.Genre, "Name", None),
            length=r.Length, size=size,
            sets=[name for name, paths in prior.items() if r.FolderPath in paths],
        ))
    REFRESH.mkdir(parents=True, exist_ok=True)
    TRACKS.write_text(json.dumps(tracks, indent=1, ensure_ascii=False))
    vols = Counter(t["path"].split("/")[2] if t["path"].startswith("/Volumes/") else "internal" for t in tracks)
    print(f"DB rows {stats['rows']}  not qualifying {stats['not_qualifying']}  "
          f"qualifying but file missing {stats['qualifying_file_missing']}  duplicate bytes {dup}")
    print(f"tracks to benchmark: {len(tracks)}  unmapped key {sum(t['true_camelot'] is None for t in tracks)}")
    print("by volume:", dict(vols))
    for name in prior:
        print(f"  in prior set {name}: {sum(name in t['sets'] for t in tracks)}")


def run_one(t: dict, repo: str) -> dict:
    sys.path.insert(0, repo)
    import iron

    path = Path(t["path"])
    row = dict(rb_id=t["rb_id"], path=t["path"], genre=t["genre"], true_bpm=t["bpm"],
               true_camelot=t["true_camelot"], length=t["length"], sets=t["sets"])
    t0 = time.time()
    try:
        res = iron.analyze(path, want=("bpm", "initial_key"))
        row.update(detected_bpm=res.bpm, detected_camelot=res.initial_key,
                   bpm_conf=res.bpm_confidence, key_conf=res.key_confidence,
                   iron_errors=[str(e) for e in (res.errors or [])])
    except Exception as e:
        row["crash"] = f"{type(e).__name__}: {e}"
        row["trace"] = traceback.format_exc()[-800:]
    row["elapsed_s"] = round(time.time() - t0, 2)
    return row


def cmd_run(repo: Path, workers: int, out: Path) -> None:
    tracks = json.loads(TRACKS.read_text())
    done = set()
    if out.exists():
        done = {json.loads(line)["rb_id"] for line in out.read_text().splitlines() if line.strip()}
    todo = [t for t in tracks if t["rb_id"] not in done]
    print(f"{len(tracks)} tracks, {len(done)} already done, {len(todo)} to run, workers {workers}", flush=True)
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as ex, out.open("a") as fh:
        futs = {ex.submit(run_one, t, str(repo)): t for t in todo}
        for i, f in enumerate(as_completed(futs), 1):
            fh.write(json.dumps(f.result(), ensure_ascii=False) + "\n")
            fh.flush()
            if i % 100 == 0:
                print(f"  {i}/{len(todo)}  {(time.time() - t0) / 60:.1f} min", flush=True)
    print(f"done in {(time.time() - t0) / 60:.1f} min", flush=True)


def cmd_score(repo: Path, out: Path) -> None:
    sys.path.insert(0, str(repo / "scripts"))
    import benchmark_iron_genre_diverse as B

    rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    ok = [r for r in rows if "crash" not in r]
    print(f"rows {len(rows)}  crashes {len(rows) - len(ok)}")
    tempo = B._tempo_accuracy(ok)
    key = B._key_accuracy(ok)
    print("BPM  exact {:.1%}  within1% {:.1%}  within4% {:.1%}  undetected {}/{}".format(
        tempo["exact"], tempo["within_1pct"], tempo["mirex"], tempo["undetected"], tempo["total"]))
    print("KEY  exact {:.1%} (of detected)  exact_of_total {:.1%}  MIREX-weighted {:.1%}  undetected {}/{}".format(
        key["exact"], key["exact_of_total"], key["mirex_weighted"], key["undetected"], key["total"]))
    print("KEY error classes:", B._key_error_breakdown(ok))
    misses = [r for r in ok if r["detected_bpm"] is not None
              and abs(r["detected_bpm"] - r["true_bpm"]) / r["true_bpm"] > 0.04]
    by_ratio = Counter(ratio_class(r["detected_bpm"], r["true_bpm"]) or "other (no clean ratio)" for r in misses)
    print(f"BPM misses (>4%): {len(misses)}")
    for label, n in by_ratio.most_common():
        print(f"  {n:5d}  {label}")
    el = [r["elapsed_s"] for r in ok if r.get("elapsed_s")]
    if el:
        print(f"time: mean {sum(el) / len(el):.1f}s  max {max(el):.1f}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["snap", "run", "score"])
    ap.add_argument("--repo", default=str(DEFAULT_REPO))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default=str(RESULTS / "rb_refresh_iron.jsonl"))
    a = ap.parse_args()
    repo = Path(a.repo)
    if a.command == "snap":
        cmd_snap(repo)
    elif a.command == "run":
        cmd_run(repo, a.workers, Path(a.out))
    else:
        cmd_score(repo, Path(a.out))


if __name__ == "__main__":
    main()
