"""
Run Iron over the first N tracks (by id) of the FableGear database. READ-ONLY: files are analysed in place,
nothing is copied or written back (no Anvil hand-off). The DB is read from a snapshot copy.

    ~/Documents/FableGear/venv/bin/python ~/FableGearTestbed/harness/iron_db.py [--n 2000] [--workers 6]

Results stream to results/iron_db_<n>.jsonl (resumable: rows already present are skipped) and a summary
is printed at the end. DB bpm/key are old tag values, NOT ground truth -- the summary reports agreement.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

BED = Path.home() / "FableGearTestbed"
DB = Path.home() / ".fablegear" / "fablegear.db"


def snapshot(dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / "fablegear_snapshot.db"
    for suf in ("", "-wal", "-shm"):
        src = Path(str(DB) + suf)
        if src.exists():
            shutil.copyfile(src, Path(str(out) + suf))
    return out


def run_one(row: dict, repo: str) -> dict:
    sys.path.insert(0, repo)
    import iron
    out = dict(id=row["id"], path=row["file_path"], fmt=row["format"], db_bpm=row["bpm"], db_key=row["key"],
               duration=row["duration"])
    try:
        t0 = time.time()
        res = iron.analyze(Path(row["file_path"]), want=("bpm", "initial_key", "downbeat_offset", "time_signature"))
        out.update(elapsed_s=round(time.time() - t0, 2), bpm=res.bpm, bpm_conf=res.bpm_confidence,
                   key=res.initial_key, key_conf=res.key_confidence, downbeat=res.downbeat_offset,
                   meter=res.time_signature, iron_errors=[str(e) for e in (res.errors or [])])
    except Exception as e:  # analyze() shouldn't raise on ordinary failures; record anything that does
        out["crash"] = f"{type(e).__name__}: {e}"
        out["trace"] = traceback.format_exc()[-800:]
    return out


# Camelot <-> traditional key names. The DB `key` column mixes both ("8A" and "Am"); Iron
# always returns Camelot, so DB keys are normalised to Camelot before comparing.
_CAMELOT = {
    "Abm": "1A", "G#m": "1A", "Ebm": "2A", "D#m": "2A", "Bbm": "3A", "A#m": "3A", "Fm": "4A",
    "Cm": "5A", "Gm": "6A", "Dm": "7A", "Am": "8A", "Em": "9A", "Bm": "10A", "F#m": "11A",
    "Gbm": "11A", "C#m": "12A", "Dbm": "12A", "B": "1B", "F#": "2B", "Gb": "2B", "Db": "3B",
    "C#": "3B", "Ab": "4B", "G#": "4B", "Eb": "5B", "D#": "5B", "Bb": "6B", "A#": "6B",
    "F": "7B", "C": "8B", "G": "9B", "D": "10B", "A": "11B", "E": "12B",
}


def to_camelot(raw) -> str | None:
    """DB key ('8A', 'Am', 'F#m', '') -> Camelot ('8A'), or None if unrecognised."""
    if not raw:
        return None
    k = str(raw).strip()
    if k.upper().endswith(("A", "B")) and k[:-1].isdigit():
        return k.upper()
    return _CAMELOT.get(k)


def bpm_match(det: float, ref: float) -> str:
    if not det or not ref:
        return "n/a"
    if abs(det - ref) / ref <= 0.04:
        return "match"
    for f, name in ((2, "double"), (0.5, "half"), (1.5, "3:2"), (2 / 3, "2:3")):
        if abs(det - ref * f) / (ref * f) <= 0.04:
            return name
    return "other"


def summarize(rows: list[dict]) -> None:
    ok = [r for r in rows if "crash" not in r]
    print(f"tracks {len(rows)}  crashes {len(rows) - len(ok)}")
    undet_b = sum(1 for r in ok if not r.get("bpm"))
    undet_k = sum(1 for r in ok if not r.get("key"))
    print(f"Iron gave no BPM: {undet_b}   no key: {undet_k}   with iron_errors: {sum(1 for r in ok if r.get('iron_errors'))}")
    ref = [r for r in ok if r.get("db_bpm") and r.get("bpm")]
    c = Counter(bpm_match(r["bpm"], r["db_bpm"]) for r in ref)
    print(f"BPM vs DB tag (n={len(ref)}):", {k: f"{v} ({v / len(ref):.1%})" for k, v in c.most_common()})
    kref = [r for r in ok if to_camelot(r.get("db_key")) and r.get("key")]
    ke = sum(1 for r in kref if r["key"].upper() == to_camelot(r["db_key"]))
    unmapped = sum(1 for r in ok if r.get("db_key") and not to_camelot(r["db_key"]))
    print(f"KEY vs DB tag (n={len(kref)}): exact {ke} ({ke / max(len(kref), 1):.1%})"
          f"   unmapped DB keys: {unmapped}")
    by = Counter()
    tot = Counter()
    for r in ref:
        tot[r["fmt"]] += 1
        by[r["fmt"]] += bpm_match(r["bpm"], r["db_bpm"]) == "match"
    print("BPM match by format:", {f: f"{by[f]}/{tot[f]}" for f in tot})
    el = [r["elapsed_s"] for r in ok if r.get("elapsed_s")]
    if el:
        print(f"per-track time: mean {sum(el) / len(el):.2f}s  max {max(el):.2f}s")
    errs = Counter(e for r in ok for e in r.get("iron_errors", []))
    if errs:
        print("iron_errors:", errs.most_common(5))
    for r in rows:
        if "crash" in r:
            print("CRASH", r["id"], r["path"], r["crash"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--repo", default=str(Path.home() / "Documents/FableGear"))
    a = ap.parse_args()
    snap = snapshot(BED / "work" / "dbsnap")
    con = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    todo = [dict(r) for r in con.execute(
        "select id,file_path,format,bpm,key,duration from fg_content order by id limit ?", (a.n,))]
    out_path = BED / "results" / f"iron_db_{a.n}.jsonl"
    done: dict[int, dict] = {}
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            r = json.loads(line)
            done[r["id"]] = r
    pending = [r for r in todo if r["id"] not in done]
    print(f"{len(todo)} tracks selected, {len(done)} already done, {len(pending)} to run", flush=True)
    t0 = time.time()
    with out_path.open("a") as fh, ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(run_one, r, a.repo) for r in pending]
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            done[r["id"]] = r
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            if i % 100 == 0:
                print(f"  {i}/{len(pending)}  {time.time() - t0:.0f}s", flush=True)
    print(f"wall {time.time() - t0:.0f}s")
    summarize([done[r["id"]] for r in todo])


if __name__ == "__main__":
    main()
