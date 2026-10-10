"""
Iron vs Rekordbox on the testbed's ground-truth tracks, then Iron -> Anvil hand-off written to a copy.

    venv/bin/python ~/FableGearTestbed/harness/iron_real.py [--repo ~/Documents/FableGear]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import traceback
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

BED = Path.home() / "FableGearTestbed"
FLAT = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}


def rb_to_camelot(k: str | None, camelot: dict[str, str]) -> str | None:
    if not k:
        return None
    minor = k.endswith("m")
    root = k[:-1] if minor else k
    root = FLAT.get(root, root)
    return camelot.get(f"{root}{'min' if minor else 'maj'}")


def run_one(m: dict, repo: str) -> dict:
    sys.path.insert(0, repo)
    import anvil
    import iron
    from iron.key import CAMELOT
    src = Path(m["_set_dir"]) / "pristine" / m["file"]
    work = BED / "work" / ("iron_" + m["file"])
    row = dict(file=m["file"], genre=m.get("genre"), true_bpm=m["bpm"],
               rb_downbeat=m.get("rb_downbeat"), rb_variable_tempo=m.get("rb_variable_tempo"),
               true_camelot=rb_to_camelot(m.get("key"), CAMELOT), length=m.get("length"))
    try:
        shutil.copyfile(src, work)
        t0 = time.time()
        res = iron.analyze(work, want=("bpm", "initial_key", "downbeat_offset", "time_signature"))
        row["elapsed_s"] = round(time.time() - t0, 2)
        row.update(detected_bpm=res.bpm, bpm_conf=res.bpm_confidence, detected_camelot=res.initial_key,
                   key_conf=res.key_confidence, downbeat=res.downbeat_offset, meter=res.time_signature,
                   iron_errors=res.errors)
        # Hand-off: whatever Iron found must land in the file exactly.
        tf = res.to_track_fields()
        wr = anvil.write_fields(work, tf, force=True)
        back = anvil.read_fields(work)
        mism = {k: (v, getattr(back, k)) for k, v in tf.present().items()
                if (abs(float(v) - float(getattr(back, k) or -1)) > 0.005 if isinstance(v, float) else v != getattr(back, k))}
        row["handoff_ok"] = wr.verified and not mism
        if mism:
            row["handoff_mismatch"] = {k: [str(a), str(b)] for k, (a, b) in mism.items()}
    except Exception as e:
        row["crash"] = f"{type(e).__name__}: {e}"
        row["trace"] = traceback.format_exc()[-1200:]
    finally:
        work.unlink(missing_ok=True)
    return row


def _downbeat_report(rows: list[dict]) -> None:
    """Iron downbeat_offset vs Rekordbox's first bar-1 beat, only where Iron's tempo is MIREX-right
    (a phase comparison against the wrong period is meaningless)."""
    sc = [r for r in rows if r.get("rb_downbeat") is not None and r.get("detected_bpm")
          and abs(r["detected_bpm"] - r["true_bpm"]) / r["true_bpm"] <= 0.04]
    if not sc:
        return
    have = [r for r in sc if r.get("downbeat") is not None]
    beat_ok = bar_ok = 0
    for r in have:
        beat = 60.0 / r["true_bpm"]
        d = r["downbeat"] - r["rb_downbeat"]
        e_beat = (d + beat / 2) % beat - beat / 2
        e_bar = (d + 2 * beat) % (4 * beat) - 2 * beat
        beat_ok += abs(e_beat) <= 0.025
        bar_ok += abs(e_bar) <= 0.025
    n = len(have)
    print(f"DOWNBEAT (tempo-correct tracks n={len(sc)}, Iron gave an offset on {n}):"
          f" on-beat within 25ms {beat_ok / n:.1%}, on-downbeat within 25ms {bar_ok / n:.1%}" if n else "DOWNBEAT none")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(Path.home() / "Documents/FableGear"))
    ap.add_argument("--score-repo", default=str(Path.home() / "Documents/FableGear"),
                    help="repo whose scripts/benchmark_iron_genre_diverse.py supplies the scoring")
    ap.add_argument("--tag", default="", help="suffix for the results file, e.g. the Iron version tested")
    ap.add_argument("--set", default="", help="subdirectory of the testbed holding pristine/ + manifest.json")
    a = ap.parse_args()
    set_dir = BED / a.set
    sys.path.insert(0, str(Path(a.score_repo) / "scripts"))
    import benchmark_iron_genre_diverse as B
    (BED / "work").mkdir(exist_ok=True)
    gt = [dict(m, _set_dir=str(set_dir)) for m in json.loads((set_dir / "manifest.json").read_text())
          if m.get("source") == "rekordbox" and m.get("bpm")]
    with ProcessPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(run_one, gt, [a.repo] * len(gt)))
    (BED / "results").mkdir(exist_ok=True)
    (BED / "results" / f"iron_real{('_' + a.set) if a.set else ''}{('_' + a.tag) if a.tag else ''}.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    ok = [r for r in rows if "crash" not in r]
    print(f"tracks {len(rows)}  crashes {len(rows) - len(ok)}  handoff_ok {sum(bool(r.get('handoff_ok')) for r in ok)}")
    print("TEMPO overall", {k: round(v, 3) if isinstance(v, float) else v for k, v in B._tempo_accuracy(ok).items()})
    print("  errors:", B._tempo_error_breakdown(ok))
    bands = defaultdict(list)
    for r in ok:
        bands[B._bpm_bucket_label(r["true_bpm"])].append(r)
    for b in sorted(bands):
        acc = B._tempo_accuracy(bands[b])
        print(f"  {b:>14s} n={acc['total']:3d} exact={acc['exact']:.2f} 1%={acc['within_1pct']:.2f} mirex={acc['mirex']:.2f} undet={acc['undetected']}")
    print("KEY overall", {k: round(v, 3) if isinstance(v, float) else v for k, v in B._key_accuracy(ok).items()})
    print("  errors:", B._key_error_breakdown(ok))
    _downbeat_report(ok)
    el = [r["elapsed_s"] for r in ok if r.get("elapsed_s")]
    print(f"time: mean {sum(el) / len(el):.1f}s  max {max(el):.1f}s")
    for r in rows:
        if "crash" in r:
            print("CRASH", r["file"], r["crash"])


if __name__ == "__main__":
    main()
