#!/usr/bin/env python3
"""
Benchmark Iron's beat-grid detector (downbeat_offset, time_signature) against real
ground truth, using beat_this (CPJKU, MIT-licensed code AND published weights -- see
docs/ANVIL_IRON_STATUS.md and iron/README.md for why that license status specifically is
what makes it usable here at all, unlike madmom's NC-licensed models) as the ground-truth
oracle.

This is deliberately NOT a proposal to depend on beat_this at runtime -- iron/beats.py
stays pure numpy, no ML dependency, same reasoning as the rest of Iron. This script is
dev/validation tooling only: run it once against a real sample of FableGear's own library
to get an actual number for "does the kick-band-emphasis downbeat picker (iron/beats.py)
work on real music," the same role scripts/benchmark_iron_tempo.py plays for bpm itself.
Rekordbox has no convenient downbeat/meter ground-truth field the way DjmdContent.BPM
exists for tempo (its beat grid lives in the binary ANLZ format) -- beat_this sidesteps
needing to parse that.

Not a pytest suite. Requires `pip install beat-this` (not in requirements*.txt -- dev-only,
same footing as essentia's already-optional status).

Usage:
    python3 scripts/benchmark_iron_beats.py --audio-dir /path/to/library
    python3 scripts/benchmark_iron_beats.py --rekordbox-db /path/to/master.db --sample 100
"""

from __future__ import annotations

import argparse
import random
import sys
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import iron

_AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".ogg"}


def _paths_from_audio_dir(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.suffix.lower() in _AUDIO_EXTENSIONS)


def _paths_from_rekordbox(db_path: Path | None) -> list[Path]:
    from pyrekordbox.db6 import tables

    import db_connection

    paths: list[Path] = []
    with db_connection.read_db(db_path) as db:
        for (folder_path,) in db.query(tables.DjmdContent).with_entities(tables.DjmdContent.FolderPath):
            if not folder_path:
                continue
            path = Path(folder_path)
            if path.exists():
                paths.append(path)
    return paths


def _ground_truth_beat_grid(file2beats, path: Path) -> tuple[list[float], list[float]] | None:
    """(beats, downbeats) in seconds from beat_this, or None if it found nothing usable."""
    beats, downbeats = file2beats(str(path))
    if len(downbeats) < 2:
        return None
    return list(beats), list(downbeats)


def _true_beats_per_bar(beats: list[float], downbeats: list[float]) -> int | None:
    """Mode of how many tracked beats fall in each inter-downbeat span -- ground truth for
    time_signature's numerator. None if the track doesn't have at least 2 full bars."""
    counts: list[int] = []
    for a, b in pairwise(downbeats):
        n = sum(1 for t in beats if a - 1e-6 <= t < b - 1e-6)
        if n > 0:
            counts.append(n)
    if not counts:
        return None
    return max(set(counts), key=counts.count)


def _downbeat_offset_matches(
    iron_offset: float, bar_period: float, true_downbeats: list[float], *, tolerance: float
) -> bool:
    """
    Does iron_offset, extrapolated forward across the track by whole bar-lengths (the same
    "one anchor + period regenerates the whole grid" semantics downbeat_offset is defined
    with -- see iron/beats.py's docstring), land within `tolerance` seconds of any of
    beat_this's actual downbeat times? Not a naive direct-value comparison: iron_offset is
    folded into [0, bar_period), so it corresponds to whichever real downbeat is nearest to
    SOME multiple of bar_period past it, not necessarily the track's very first one.
    """
    if bar_period <= 0 or not true_downbeats:
        return False
    last = true_downbeats[-1]
    k = 0
    while iron_offset + k * bar_period <= last + bar_period:
        projected = iron_offset + k * bar_period
        if any(abs(projected - t) <= tolerance for t in true_downbeats):
            return True
        k += 1
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--audio-dir", type=Path, default=None,
                         help="directory to search recursively for audio files")
    parser.add_argument("--rekordbox-db", type=Path, default=None,
                         help="path to a Rekordbox master.db, as an alternative source of "
                              "file paths (default db_connection path if --audio-dir omitted)")
    parser.add_argument("--limit", type=int, default=None, help="stop after N tracks")
    parser.add_argument("--sample", type=int, default=None,
                         help="random sample of this size before --limit truncates it")
    parser.add_argument("--seed", type=int, default=42, help="random seed for --sample")
    parser.add_argument("--device", default="cpu",
                         help="beat_this inference device (default: cpu -- this is a "
                              "validation script, not assumed to run where a GPU is present)")
    parser.add_argument("--tolerance", type=float, default=0.07,
                         help="seconds of slack when matching Iron's downbeat_offset "
                              "against a real beat_this downbeat (default: 70ms, roughly "
                              "a sixteenth-note at 130 BPM)")
    args = parser.parse_args(argv)

    try:
        from beat_this.inference import File2Beats
    except ImportError:
        print("error: beat_this is not installed. `pip install beat-this` (dev-only -- "
              "not in requirements*.txt, same footing as essentia's optional status).",
              file=sys.stderr)
        return 2

    if args.audio_dir:
        paths = _paths_from_audio_dir(args.audio_dir)
        source = f"directory ({args.audio_dir})"
    else:
        try:
            paths = _paths_from_rekordbox(args.rekordbox_db)
        except Exception as exc:
            print(f"error: could not read a Rekordbox database ({exc}). "
                  f"Pass --audio-dir instead.", file=sys.stderr)
            return 2
        source = "Rekordbox database"

    if not paths:
        print("error: no audio files found.", file=sys.stderr)
        return 2

    if args.sample:
        rng = random.Random(args.seed)
        paths = rng.sample(paths, min(args.sample, len(paths)))
    if args.limit:
        paths = paths[: args.limit]

    print(f"Ground truth source: {len(paths)} tracks from {source}")
    print(f"Loading beat_this (device={args.device}) ...")
    file2beats = File2Beats(checkpoint_path="final0", device=args.device, dbn=False)
    print("Running beat_this + iron.analyze() on each track ... (this decodes real audio "
          "and runs a transformer forward pass per file, expect it to take a while)\n")

    total = 0
    iron_found_grid = 0
    downbeat_matches = 0
    meter_agrees = 0
    meter_compared = 0
    confident_correct = 0
    confident_total = 0
    _CONFIDENT = 0.4  # a beat_grid_confidence above this is "confident enough to trust"

    for i, path in enumerate(paths, 1):
        truth = None
        try:
            truth = _ground_truth_beat_grid(file2beats, path)
        except Exception as exc:
            print(f"  [{i}/{len(paths)}] beat_this failed on {path.name}: {exc}", file=sys.stderr)
        if truth is None:
            continue
        true_beats, true_downbeats = truth
        total += 1

        result = iron.analyze(path, want=("bpm", "downbeat_offset", "time_signature"))
        if result.downbeat_offset is None or result.bpm is None:
            continue
        iron_found_grid += 1

        beats_per_bar = int(result.time_signature.split("/")[0]) if result.time_signature else 4
        bar_period = (60.0 / result.bpm) * beats_per_bar
        matched = _downbeat_offset_matches(
            result.downbeat_offset, bar_period, true_downbeats, tolerance=args.tolerance
        )
        if matched:
            downbeat_matches += 1

        true_bpb = _true_beats_per_bar(true_beats, true_downbeats)
        if true_bpb is not None:
            meter_compared += 1
            if true_bpb == beats_per_bar:
                meter_agrees += 1

        if result.beat_grid_confidence is not None and result.beat_grid_confidence >= _CONFIDENT:
            confident_total += 1
            if matched:
                confident_correct += 1

        if i % 25 == 0:
            print(f"  ... {i}/{len(paths)}", file=sys.stderr)

    print("\n" + "=" * 62)
    print("IRON BEAT-GRID BENCHMARK (ground truth: beat_this)")
    print("=" * 62)
    print(f"Tracks with usable beat_this ground truth: {total}")
    print(f"Iron found a beat grid for:                {iron_found_grid}")
    print()
    if iron_found_grid:
        print(f"  downbeat_offset matches a real downbeat (±{args.tolerance*1000:.0f}ms): "
              f"{downbeat_matches}/{iron_found_grid} ({downbeat_matches/iron_found_grid:.1%})")
    if meter_compared:
        print(f"  time_signature agrees with beat_this:  "
              f"{meter_agrees}/{meter_compared} ({meter_agrees/meter_compared:.1%})")
    if confident_total:
        print(f"  accuracy when beat_grid_confidence >= {_CONFIDENT}: "
              f"{confident_correct}/{confident_total} ({confident_correct/confident_total:.1%})")
        print(f"  (vs {downbeat_matches/iron_found_grid:.1%} unconditional -- if this is "
              f"meaningfully higher, beat_grid_confidence is doing its job as a filter)")
    print()
    print("This is dev/validation tooling, not a shipped dependency -- beat_this never runs")
    print("in the FableGear app itself. See this script's own module docstring.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
