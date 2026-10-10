"""Run FableGear's real audio_processor.process_file (BPM + key, forced) on copies; read tags back."""
from __future__ import annotations

import json
import shutil
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

BED = Path.home() / "FableGearTestbed"
REPO = Path.home() / "Documents/FableGear"


def one(args: tuple[str, dict]) -> dict:
    set_name, m = args
    sys.path.insert(0, str(REPO))
    import anvil
    import audio_processor as ap
    src = BED / set_name / "pristine" / m["file"]
    work = BED / "work" / f"pf_{set_name}_{m['file']}"
    shutil.copyfile(src, work)
    try:
        t0 = time.time()
        r = ap.process_file(work, detect_bpm=True, detect_key=True, normalise=False, force=True)
        try:
            back = anvil.read_fields(work)
        except Exception as e:  # e.g. a container Anvil refuses; record, don't crash the pool
            back = anvil.TrackFields()
            r.errors.append(f"[harness] read-back: {type(e).__name__}: {e}")
        return dict(set=set_name, file=m["file"], fmt=src.suffix, rb_bpm=m.get("bpm"), rb_key=m.get("key"),
                    bpm=r.bpm_detected, bpm_source=r.bpm_source, key=r.key_detected, key_source=r.key_source,
                    backend=r.tag_backend, errors=r.errors, secs=round(time.time() - t0, 2),
                    back_bpm=back.bpm, back_key=back.initial_key)
    finally:
        work.unlink(missing_ok=True)


def main() -> None:
    sys.path.insert(0, str(REPO))
    (BED / "work").mkdir(exist_ok=True)
    jobs = []
    for set_name, sub in (("rb200", "rb200/manifest.json"), ("", "manifest.json")):
        jobs += [(set_name or ".", m) for m in json.loads((BED / sub).read_text())]
    with ProcessPoolExecutor(6) as ex:
        rows = list(ex.map(one, jobs))
    (BED / "results" / "process_file_real.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    print("files", len(rows), "| backend", Counter(r["backend"] for r in rows),
          "| bpm_source", Counter(r["bpm_source"] for r in rows), "| key_source", Counter(r["key_source"] for r in rows))
    errs = [r for r in rows if r["errors"]]
    print("files with errors:", len(errs), Counter(e.split(":")[0] for r in errs for e in r["errors"]).most_common(6))
    tagged = [r for r in rows if r["bpm"] is not None]
    print("written BPM reads back:", sum(abs((r["back_bpm"] or 0) - r["bpm"]) < 0.01 for r in tagged), "/", len(tagged),
          "| key reads back:", sum(r["back_key"] == r["key"] for r in rows if r["key"]), "/", sum(1 for r in rows if r["key"]))
    gt = [r for r in tagged if r["rb_bpm"]]
    print("BPM vs Rekordbox, MIREX:", f"{sum(abs(r['bpm'] - r['rb_bpm']) / r['rb_bpm'] <= 0.04 for r in gt) / len(gt):.1%}", f"(n={len(gt)})",
          "| mean secs/file", round(sum(r["secs"] for r in rows) / len(rows), 2))
    for r in errs[:8]:
        print("  ", r["set"], r["file"], r["fmt"], r["errors"][:2])


if __name__ == "__main__":
    main()
