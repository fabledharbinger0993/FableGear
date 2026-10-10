#!/usr/bin/env python3
"""
Repeatable BPM + key benchmark: Iron vs Essentia, scored against Rekordbox as ground truth.

Every engine sees the same audio files and is scored against the same manifest. Rekordbox's
own BPM and key (exported to the manifest) are the reference, so each tuning change can be
measured against the goal stated in docs/IRON_RESEARCH.md: beat Essentia, match or beat
Rekordbox. Rekordbox is the reference, not a competitor: it is never run here.

Manifest format (JSON list), the same one scripts/build_rb_set-style testbed sets write:

    [{"file": "000.mp3", "path": "/original/path/if/any.mp3", "bpm": 95.0, "key": "Cm"}, ...]

Audio is resolved in this order: --audio-dir/<file>, then `path` if it exists, then
<manifest dir>/pristine/<file>. `key` is Rekordbox notation ("Cm", "F#m", "Bb"); `bpm` may be
null, in which case the track is skipped for BPM scoring only.

Engines:
  iron      iron.analyze(path, want=("bpm", "initial_key")) with its production defaults.
  essentia  RhythmExtractor2013(method="multifeature") for BPM (the same call as
            audio_processor._detect_bpm_essentia) and KeyExtractor(profileType="edma") for
            key. The key path is NOT a production path in this codebase; it's measured so the
            comparison exists. Audio is decoded by ffmpeg to 44.1 kHz mono float32.

Scoring:
  BPM   exact = |det - ref| <= 0.6 BPM; within1 = <= 1%; MIREX = <= 4%. Misses are classified
        as double / half / 3:2 / 2:3 / other, so octave and compound-meter errors are visible
        instead of hidden in one accuracy number.
  Key   Camelot-position relation, weighted MIREX-style as in docs/IRON_RESEARCH.md §18.1:
        exact 1.0, fifth 0.5, relative 0.3, parallel 0.2, other 0. "exact_of_total" counts an
        engine that declined a track as a miss; "exact_of_detected" does not.

Results stream to --out as JSONL (one row per track and engine, resumable: rows already
present are skipped). --summary-out writes the aggregate as JSON.

Usage:
    python3 scripts/benchmark_key_bpm_vs_rekordbox.py --manifest set/manifest.json \
        --engines iron,essentia --workers 6 --out results/bench.jsonl --summary-out results/bench_summary.json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
_ESSENTIA_SR = 44100
_KEY_WEIGHTS = {"exact": 1.0, "fifth": 0.5, "relative": 0.3, "parallel": 0.2, "other": 0.0}
_ENGINES = ("iron", "essentia")


# --------------------------------------------------------------------------- key notation


def _load_key_tables(repo: Path) -> tuple[dict[str, str], tuple[str, ...]]:
    """(CAMELOT, NOTES) from iron.key, so the harness can't drift from the detector's table."""
    sys.path.insert(0, str(repo))
    from iron import key

    return key.CAMELOT, key.NOTES


_CAMELOT: dict[str, str] = {}
_NOTES: tuple[str, ...] = ()
_ENHARMONIC = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}
_CAMELOT_TO_NAME: dict[str, str] = {}


def _init_tables(repo: Path) -> None:
    global _CAMELOT, _NOTES, _CAMELOT_TO_NAME
    _CAMELOT, _NOTES = _load_key_tables(repo)
    _CAMELOT_TO_NAME = {v: k for k, v in _CAMELOT.items()}


def key_to_camelot(raw: str | None, scale: str | None = None) -> str | None:
    """Any key spelling -> Camelot ("8A"). Accepts Rekordbox ("F#m", "Bb"), iron.key names
    ("Amin", "F#maj"), and essentia's note + scale pair ("A", "minor")."""
    if not raw:
        return None
    raw = raw.strip()
    if re.fullmatch(r"\d{1,2}[AB]", raw):
        return raw
    m = re.fullmatch(r"([A-Ga-g][#b]?)\s*(min|minor|m|maj|major)?", raw)
    if not m:
        return None
    note, mode = m.group(1), m.group(2)
    note = note[0].upper() + note[1:]
    if mode is None and scale:
        mode = scale
    minor = bool(mode) and mode.lower().startswith("m") and not mode.lower().startswith("maj")
    note = _ENHARMONIC.get(note, note)
    if note not in _NOTES:
        return None
    return _CAMELOT.get(note + ("min" if minor else "maj"))


def _pitch_mode(camelot: str) -> tuple[int, str]:
    """Camelot -> (pitch class 0-11, "min" | "maj") from the detector's own table."""
    name = _CAMELOT_TO_NAME[camelot]
    return _NOTES.index(name[:-3]), name[-3:]


def key_relation(det: str | None, ref: str | None) -> str | None:
    """Relation class of a detected Camelot key to the reference, or None if either is missing."""
    if not det or not ref:
        return None
    if det == ref:
        return "exact"
    dpc, dmode = _pitch_mode(det)
    rpc, rmode = _pitch_mode(ref)
    if dmode == rmode:
        if (dpc - rpc) % 12 in (5, 7):
            return "fifth"
        return "other"
    if dpc == rpc:
        return "parallel"
    if (dpc == (rpc + 3) % 12 and dmode == "maj") or (dpc == (rpc - 3) % 12 and dmode == "min"):
        return "relative"
    return "other"


# --------------------------------------------------------------------------- BPM classes


def bpm_relation(det: float | None, ref: float | None) -> str | None:
    """'exact' (0.6 BPM) / 'within1' / 'mirex' (4%) / octave-compound class, or 'miss'."""
    if not det or not ref:
        return None
    if abs(det - ref) <= 0.6:
        return "exact"
    if abs(det - ref) / ref <= 0.01:
        return "within1"
    if abs(det - ref) / ref <= 0.04:
        return "mirex"
    for factor, name in ((2.0, "double"), (0.5, "half"), (1.5, "3:2"), (2 / 3, "2:3")):
        if abs(det - ref * factor) / (ref * factor) <= 0.04:
            return name
    return "miss"


_BPM_CREDIT = {"exact": 1.0, "within1": 1.0, "mirex": 1.0}


# --------------------------------------------------------------------------- engines


def _decode_mono(path: Path) -> np.ndarray:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH")
    raw = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(_ESSENTIA_SR), "-f", "f32le", "-"],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def run_iron(path: Path) -> dict:
    import iron

    res = iron.analyze(path, want=("bpm", "initial_key"))
    return {
        "bpm": res.bpm,
        "key": res.initial_key,
        "key_camelot": key_to_camelot(res.initial_key),
        "errors": [str(e) for e in (res.errors or [])],
    }


def run_essentia(path: Path) -> dict:
    import essentia.standard as es

    audio = _decode_mono(path)
    bpm = float(es.RhythmExtractor2013(method="multifeature")(audio)[0])
    key, scale, _strength = es.KeyExtractor(profileType="edma", sampleRate=_ESSENTIA_SR)(audio)[:3]
    return {"bpm": bpm, "key": f"{key} {scale}", "key_camelot": key_to_camelot(key, scale), "errors": []}


_RUNNERS = {"iron": run_iron, "essentia": run_essentia}


# --------------------------------------------------------------------------- orchestration


def resolve_audio(entry: dict, manifest_dir: Path, audio_dir: Path | None) -> Path | None:
    name = entry.get("file")
    candidates = []
    if audio_dir and name:
        candidates.append(audio_dir / name)
    if entry.get("path"):
        candidates.append(Path(entry["path"]))
    if name:
        candidates.append(manifest_dir / "pristine" / name)
    return next((p for p in candidates if p.exists()), None)


def run_one(task: dict) -> dict:
    """Worker entry: one (track, engine) pair. Never raises; failures become row errors."""
    if task["repo"] not in sys.path:
        sys.path.insert(0, task["repo"])
    _init_tables(Path(task["repo"]))
    row = {
        "id": task["id"],
        "engine": task["engine"],
        "file": task["file"],
        "ref_bpm": task["ref_bpm"],
        "ref_key": task["ref_key"],
        "ref_camelot": key_to_camelot(task["ref_key"]),
    }
    t0 = time.time()
    try:
        out = _RUNNERS[task["engine"]](Path(task["audio"]))
        row.update(out)
    except Exception as e:  # one bad file must not kill a long run
        row.update(
            bpm=None, key=None, key_camelot=None, crash=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-800:]
        )
    row["elapsed_s"] = round(time.time() - t0, 2)
    row["bpm_class"] = bpm_relation(row.get("bpm"), task["ref_bpm"])
    row["key_class"] = key_relation(row["key_camelot"], row["ref_camelot"])
    return row


def summarize(rows: list[dict], engines: list[str]) -> dict:
    """Aggregate per-engine accuracy over the same track set. Pure function: unit-tested."""
    out: dict[str, dict] = {}
    for eng in engines:
        mine = [r for r in rows if r["engine"] == eng]
        bpm_rows = [r for r in mine if r.get("ref_bpm")]
        key_rows = [r for r in mine if r.get("ref_camelot")]
        bpm_cls = Counter(r["bpm_class"] or "no_output" for r in bpm_rows)
        key_cls = Counter(r["key_class"] or "no_output" for r in key_rows)
        nb, nk = len(bpm_rows), len(key_rows)
        det_k = sum(1 for r in key_rows if r.get("key_camelot"))
        times = [r["elapsed_s"] for r in mine if "elapsed_s" in r]
        out[eng] = {
            "tracks": len(mine),
            "crashes": sum(1 for r in mine if "crash" in r),
            "bpm": {
                "n": nb,
                "exact_0.6": round(bpm_cls["exact"] / nb, 4) if nb else None,
                "within_1pct": round((bpm_cls["exact"] + bpm_cls["within1"]) / nb, 4) if nb else None,
                "mirex_4pct": round(sum(bpm_cls[c] for c in _BPM_CREDIT) / nb, 4) if nb else None,
                "classes": dict(bpm_cls),
            },
            "key": {
                "n": nk,
                "detected": det_k,
                "exact_of_total": round(key_cls["exact"] / nk, 4) if nk else None,
                "exact_of_detected": round(key_cls["exact"] / det_k, 4) if det_k else None,
                "mirex_weighted": round(sum(_KEY_WEIGHTS.get(c, 0.0) * n for c, n in key_cls.items()) / nk, 4)
                if nk
                else None,
                "classes": dict(key_cls),
            },
            "mean_s_per_track": round(sum(times) / len(times), 2) if times else None,
        }
    return out


def _load_done(path: Path) -> dict[tuple[str, str], dict]:
    done: dict[tuple[str, str], dict] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[(str(r["id"]), r["engine"])] = r
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--audio-dir", type=Path, default=None)
    ap.add_argument("--engines", default="iron,essentia")
    ap.add_argument("--limit", type=int, default=None, help="first N manifest entries with a BPM or key")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--repo", type=Path, default=_REPO, help="directory containing the iron/ package")
    ap.add_argument("--out", type=Path, default=Path("bench_results.jsonl"))
    ap.add_argument("--summary-out", type=Path, default=None)
    a = ap.parse_args(argv)

    engines = [e.strip() for e in a.engines.split(",") if e.strip()]
    bad = [e for e in engines if e not in _ENGINES]
    if bad:
        ap.error(f"unknown engine(s): {bad}; choose from {list(_ENGINES)}")

    manifest_dir = a.manifest.resolve().parent
    entries = json.loads(a.manifest.read_text())
    entries = [e for e in entries if e.get("bpm") or e.get("key")]
    if a.limit:
        entries = entries[: a.limit]

    _init_tables(a.repo.resolve())
    tasks, missing = [], []
    for e in entries:
        audio = resolve_audio(e, manifest_dir, a.audio_dir)
        if audio is None:
            missing.append(e.get("file") or e.get("path"))
            continue
        for eng in engines:
            tasks.append(
                {
                    "id": e.get("file") or str(audio),
                    "file": audio.name,
                    "audio": str(audio),
                    "engine": eng,
                    "repo": str(a.repo.resolve()),
                    "ref_bpm": e.get("bpm"),
                    "ref_key": e.get("key"),
                }
            )

    a.out.parent.mkdir(parents=True, exist_ok=True)
    done = _load_done(a.out)
    pending = [t for t in tasks if (str(t["id"]), t["engine"]) not in done]
    print(
        f"manifest tracks {len(entries)}  audio missing {len(missing)}  "
        f"engines {engines}  to run {len(pending)} (already done {len(tasks) - len(pending)})",
        flush=True,
    )
    if "iron" in engines:
        print("Iron: iron.analyze defaults (production bounds)", flush=True)
    if "essentia" in engines:
        print("Essentia: RhythmExtractor2013 defaults; KeyExtractor(edma)", flush=True)

    t0 = time.time()
    rows = list(done.values())
    with a.out.open("a") as fh, ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(run_one, t) for t in pending]
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            rows.append(r)
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            if i % 25 == 0:
                print(f"  {i}/{len(pending)}  {time.time() - t0:.0f}s", flush=True)
    print(f"wall {time.time() - t0:.0f}s")

    # Score only tracks where every requested engine produced a row, so engines are compared
    # on an identical track set.
    by_track: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        by_track[str(r["id"])].add(r["engine"])
    common = {tid for tid, engs in by_track.items() if engs >= set(engines)}
    scored = [r for r in rows if str(r["id"]) in common]
    summary = {
        "manifest": str(a.manifest),
        "tracks_common": len(common),
        "missing_audio": missing,
        "engines": summarize(scored, engines),
    }
    text = json.dumps(summary, indent=2, ensure_ascii=False)
    if a.summary_out:
        a.summary_out.parent.mkdir(parents=True, exist_ok=True)
        a.summary_out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
