"""
Watch the live Rekordbox 7 library for tracks it newly analyses, and collect them as a benchmark set.

Reads ONLY from snapshot copies of master.db (+ -wal + -shm) taken into live/snap/; never opens the
live file. Copies audio from the source into live/pristine/ (read-only on the source). Writes
live/manifest.json (list of {file, path, bpm, key, rb_id}) after every cycle, so progress survives a crash.

A track counts when, between two snapshots, it goes from not-qualifying to qualifying, where qualifying
means BPM set, key set, and Analysed != 0. Tracks already qualifying in the first (baseline) snapshot
never count.

    venv/bin/python live_collect.py [--target 300] [--hours 6] [--interval 180]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

logging.disable(logging.WARNING)

REKORDBOX = Path.home() / "Library/Pioneer/rekordbox"
BED = Path.home() / "FableGearTestbed"
LIVE = BED / "live"
SNAP = LIVE / "snap"
PRISTINE = LIVE / "pristine"
MANIFEST = LIVE / "manifest.json"
LOG = LIVE / "collect.log"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG.open("a") as fh:
        fh.write(line + "\n")


def snapshot() -> Path:
    """Copy the DB, WAL and SHM. Never touches the live file beyond reading it."""
    SNAP.mkdir(parents=True, exist_ok=True)
    for name in ("master.db", "master.db-wal", "master.db-shm"):
        src = REKORDBOX / name
        if src.exists():
            shutil.copy2(src, SNAP / name)
    return SNAP / "master.db"


def qualifying(db_path: Path) -> dict[str, dict]:
    from pyrekordbox import Rekordbox6Database

    db = Rekordbox6Database(str(db_path), unlock=True)
    out: dict[str, dict] = {}
    for r in db.get_content().all():
        key = getattr(r.Key, "ScaleName", None)
        if not (r.BPM and key and r.Analysed and r.FolderPath):
            continue
        out[str(r.ID)] = {
            "bpm": r.BPM / 100,
            "key": key,
            "path": r.FolderPath,
            "length": r.Length,
        }
    return out


def load_manifest() -> list[dict]:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return []


def save_manifest(rows: list[dict]) -> None:
    tmp = MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    os.replace(tmp, MANIFEST)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=300)
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--interval", type=int, default=180)
    a = ap.parse_args()

    LIVE.mkdir(parents=True, exist_ok=True)
    PRISTINE.mkdir(parents=True, exist_ok=True)
    rows = load_manifest()
    seen_files = {(os.path.getsize(PRISTINE / r["file"]), None) for r in rows if (PRISTINE / r["file"]).exists()}
    seen_ids = {r["rb_id"] for r in rows}
    started = time.time()
    deadline = started + a.hours * 3600
    baseline: set[str] | None = None
    log(f"start: target={a.target} hours={a.hours} interval={a.interval}s existing={len(rows)}")

    while True:
        cycle_start = time.time()
        try:
            db_path = snapshot()
            now = qualifying(db_path)
            if baseline is None:
                baseline = set(now)
                log(f"baseline: {len(baseline)} tracks already qualifying (not counted)")
            else:
                new_ids = sorted(set(now) - baseline - seen_ids)
                added = 0
                for rb_id in new_ids:
                    if len(rows) >= a.target:
                        break
                    info = now[rb_id]
                    src = Path(info["path"])
                    if not src.exists():
                        log(f"skip {rb_id}: source missing {src}")
                        seen_ids.add(rb_id)
                        continue
                    size = os.path.getsize(src)
                    if (size, None) in seen_files:
                        # Same file already collected under another content id (duplicate copy).
                        log(f"skip {rb_id}: duplicate size {size} of an existing track")
                        seen_ids.add(rb_id)
                        continue
                    name = f"{len(rows):03d}{src.suffix.lower()}"
                    shutil.copy2(src, PRISTINE / name)
                    rows.append({"file": name, "path": info["path"], "bpm": info["bpm"],
                                 "key": info["key"], "rb_id": rb_id})
                    seen_files.add((size, None))
                    seen_ids.add(rb_id)
                    added += 1
                if added:
                    save_manifest(rows)
                log(f"cycle: qualifying={len(now)} new={len(new_ids)} added={added} total={len(rows)}")
        except Exception as exc:  # keep watching through transient DB/copy races
            log(f"cycle error (continuing): {type(exc).__name__}: {exc}")

        if len(rows) >= a.target:
            log(f"target reached: {len(rows)} tracks")
            break
        if time.time() >= deadline:
            log(f"time limit reached: {len(rows)} tracks")
            break
        sleep_for = max(0.0, a.interval - (time.time() - cycle_start))
        time.sleep(sleep_for)

    save_manifest(rows)
    elapsed = (time.time() - started) / 60
    log(f"done: {len(rows)} tracks in {elapsed:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
