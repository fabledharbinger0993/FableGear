"""
Build a testbed set from tracks the live Rekordbox has fully analyzed (read from a snapshot copy of its DB).
Copies audio off Passport (read-only on the source) and records Rekordbox's BPM, key and first downbeat.

    venv/bin/python build_rb_set.py <snapshot master.db> <set name> [--count 500]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
from pathlib import Path

logging.disable(logging.WARNING)
BED = Path.home() / "FableGearTestbed"
SHARE = Path.home() / "Library/Pioneer/rekordbox/share"


def main() -> None:
    from pyrekordbox import Rekordbox6Database
    from pyrekordbox.anlz import AnlzFile
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("name")
    ap.add_argument("--count", type=int, default=500)
    a = ap.parse_args()
    out = BED / a.name
    (out / "pristine").mkdir(parents=True, exist_ok=True)
    db = Rekordbox6Database(a.db, unlock=True)
    rows = sorted((r for r in db.get_content().all() if r.BPM and r.FolderPath
                   and r.FolderPath.startswith("/Volumes/Passport") and os.path.exists(r.FolderPath)),
                  key=lambda r: r.updated_at or r.created_at)
    seen, manifest, skipped = set(), [], {"duplicate": 0, "no_grid": 0}
    for r in rows:
        dup_key = (os.path.getsize(r.FolderPath), r.Length)
        if dup_key in seen:
            skipped["duplicate"] += 1
            continue
        downbeat = variable = None
        try:
            grid = AnlzFile.parse_file(SHARE / r.AnalysisDataPath.lstrip("/")).get("beat_grid")
            beats, bpms, times = grid
            firsts = [t for b, t in zip(beats, times) if b == 1]
            downbeat = float(firsts[0]) if firsts else None
            variable = bool(max(bpms) - min(bpms) > 0.05)
        except Exception:
            skipped["no_grid"] += 1
        seen.add(dup_key)
        i = len(manifest)
        name = f"{i:03d}{os.path.splitext(r.FolderPath)[1].lower()}"
        shutil.copy2(r.FolderPath, out / "pristine" / name)
        manifest.append(dict(file=name, path=r.FolderPath, source="rekordbox", bpm=r.BPM / 100,
                             key=getattr(r.Key, "ScaleName", None), genre=getattr(r.Genre, "Name", None),
                             length=r.Length, rb_id=str(r.ID), rb_downbeat=downbeat, rb_variable_tempo=variable))
        if len(manifest) >= a.count:
            break
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    print(f"{len(manifest)} tracks -> {out}  skipped {skipped}  variable-tempo grids "
          f"{sum(bool(m['rb_variable_tempo']) for m in manifest)}")


if __name__ == "__main__":
    main()
