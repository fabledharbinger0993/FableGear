#!/usr/bin/env python3
"""
Ablation: does iron/tempo.py's genre-band octave correction (Pass 2) actually help on
real, genre-diverse music, or is it hurting? Reconstructs a random sample of real
Rekordbox ground truth (same sourcing as live_compare_iron_essentia.py), decodes each
file once, and runs iron.tempo.detect_tempo() twice on the same decoded audio -- once
normally, once with the genre-band correction disabled by monkeypatching
tempo._in_genre_band to always return True (which makes Pass 2's "outside every band"
trigger condition always false, so it never fires -- no source changes, no risk to the
real module).

Written to test a specific hypothesis raised after a live Iron-vs-essentia comparison
showed Iron losing to librosa's OLD historical MIREX number (see
docs/IRON_HANDOVER_2026-08-24.md for the full writeup). Result on a 150-track sample,
seed=42: the hypothesis was WRONG -- disabling genre-band correction roughly HALVED
every accuracy metric (net 42 tracks helped vs 4 hurt). The real root cause is Pass 1's
raw autocorrelation+harmonic-sum pick systematically landing on HALF the true tempo on
real music; genre-band correction is a genuinely load-bearing partial fix for that, not
the problem. Keep this script around for re-testing after any Pass 1 changes.

Usage:
    python3 scripts/ablate_genre_bands.py
    python3 scripts/ablate_genre_bands.py --sample 300 --seed 7
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyrekordbox.db6 import tables

import db_connection
from iron import tempo as tempo_detect
from iron.api import _decode


def accuracy(pairs: list[tuple[float, float]]) -> dict[str, float]:
    if not pairs:
        return {"exact": 0.0, "within_1pct": 0.0, "mirex": 0.0}
    exact = sum(1 for d, t in pairs if abs(d - t) <= 0.6)
    within_1pct = sum(1 for d, t in pairs if abs(d - t) / t <= 0.01)
    mirex = sum(1 for d, t in pairs if abs(d - t) / t <= 0.04)
    n = len(pairs)
    return {"exact": exact / n, "within_1pct": within_1pct / n, "mirex": mirex / n}


def ratio_bucket(detected: float, true: float) -> str:
    """Label common octave/ratio relationships between a detected and true BPM, so a
    printed table shows AT A GLANCE whether a miss is a clean subharmonic/harmonic error
    (the pattern this script exists to look for) or genuinely unrelated."""
    r = detected / true
    for name, target in [("~0.5x", 0.5), ("~2x", 2.0), ("~1.5x", 1.5), ("~0.667x", 2 / 3),
                          ("~0.75x", 0.75), ("~1.333x", 4 / 3)]:
        if abs(r - target) / target < 0.03:
            return name
    return "close" if abs(r - 1.0) <= 0.04 else "unrelated"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sample", type=int, default=150, help="sample size (default: 150)")
    parser.add_argument("--seed", type=int, default=42, help="random seed (default: 42)")
    args = parser.parse_args(argv)

    print("Querying Rekordbox for candidate (path, bpm) rows...", flush=True)
    candidates = []
    with db_connection.read_db() as db:
        rows = db.query(tables.DjmdContent).with_entities(
            tables.DjmdContent.FolderPath, tables.DjmdContent.BPM
        )
        for folder_path, bpm in rows:
            if not folder_path or bpm is None or bpm <= 0:
                continue
            candidates.append((folder_path, bpm / 100.0))

    rng = random.Random(args.seed)
    rng.shuffle(candidates)

    items: list[tuple[Path, float]] = []
    for folder_path, true_bpm in candidates:
        p = Path(folder_path)
        if p.exists():
            items.append((p, true_bpm))
        if len(items) >= args.sample:
            break

    print(f"Sample: {len(items)} tracks (seed={args.seed})\n", flush=True)

    with_band: list[tuple[float, float]] = []
    without_band: list[tuple[float, float]] = []
    flipped_cases = []  # tracks where the two variants disagreed by more than half a BPM
    t_start = time.time()

    for i, (path, true_bpm) in enumerate(items, 1):
        try:
            y, sr = _decode(path)  # whole track -- matches iron.analyze()'s decode as of 2026-08-27
        except Exception as e:
            print(f"  [{i}/{len(items)}] decode failed: {path.name}: {e}", file=sys.stderr)
            continue

        try:
            on_result = tempo_detect.detect_tempo(y, sr)
        except Exception as e:
            on_result = None
            print(f"  [{i}/{len(items)}] detect_tempo (band ON) failed: {e}", file=sys.stderr)

        orig_in_band = tempo_detect._in_genre_band
        tempo_detect._in_genre_band = lambda bpm: True
        try:
            off_result = tempo_detect.detect_tempo(y, sr)
        except Exception as e:
            off_result = None
            print(f"  [{i}/{len(items)}] detect_tempo (band OFF) failed: {e}", file=sys.stderr)
        finally:
            tempo_detect._in_genre_band = orig_in_band

        if on_result is not None:
            with_band.append((on_result[0], true_bpm))
        if off_result is not None:
            without_band.append((off_result[0], true_bpm))

        if on_result is not None and off_result is not None and abs(on_result[0] - off_result[0]) > 0.5:
            on_ok = abs(on_result[0] - true_bpm) / true_bpm <= 0.04
            off_ok = abs(off_result[0] - true_bpm) / true_bpm <= 0.04
            flipped_cases.append({
                "name": path.name[:45], "true": true_bpm,
                "band_on": on_result[0], "band_off": off_result[0],
                "on_mirex_ok": on_ok, "off_mirex_ok": off_ok,
                "on_ratio": ratio_bucket(on_result[0], true_bpm),
                "off_ratio": ratio_bucket(off_result[0], true_bpm),
            })

        if i % 25 == 0:
            print(f"  ... {i}/{len(items)} ({time.time()-t_start:.0f}s elapsed)", file=sys.stderr)

    on_acc = accuracy(with_band)
    off_acc = accuracy(without_band)

    print("\n" + "=" * 70)
    print("GENRE-BAND CORRECTION ABLATION")
    print("=" * 70)
    print(f"{'':20s} {'band ON (current)':>20s} {'band OFF':>12s}")
    print(f"{'compared':20s} {len(with_band):>20d} {len(without_band):>12d}")
    print(f"{'exact (0.6 BPM)':20s} {on_acc['exact']:>19.1%} {off_acc['exact']:>11.1%}")
    print(f"{'within 1%':20s} {on_acc['within_1pct']:>19.1%} {off_acc['within_1pct']:>11.1%}")
    print(f"{'MIREX (4%)':20s} {on_acc['mirex']:>19.1%} {off_acc['mirex']:>11.1%}")

    print(f"\nTracks where the correction changed the answer by >0.5 BPM: {len(flipped_cases)}")
    helped = sum(1 for c in flipped_cases if c["on_mirex_ok"] and not c["off_mirex_ok"])
    hurt = sum(1 for c in flipped_cases if c["off_mirex_ok"] and not c["on_mirex_ok"])
    neutral = len(flipped_cases) - helped - hurt
    print(f"  correction HELPED (fixed a >4% miss):  {helped}")
    print(f"  correction HURT (broke a good answer): {hurt}")
    print(f"  neutral (both right or both wrong):    {neutral}")

    print(f"\n{'name':47s} {'true':>7s} {'ON':>8s} {'OFF':>8s} {'ON ratio':>10s} {'OFF ratio':>10s} {'verdict':>8s}")
    for c in flipped_cases:
        verdict = "HURT" if (c["off_mirex_ok"] and not c["on_mirex_ok"]) else (
            "HELPED" if (c["on_mirex_ok"] and not c["off_mirex_ok"]) else "-")
        print(f"{c['name']:47s} {c['true']:>7.2f} {c['band_on']:>8.2f} {c['band_off']:>8.2f} "
              f"{c['on_ratio']:>10s} {c['off_ratio']:>10s} {verdict:>8s}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
