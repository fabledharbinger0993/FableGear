#!/usr/bin/env python3
"""
Benchmark Iron's tempo and key detection against a genre-diverse sample drawn directly from
real audio files' own embedded tags -- no Rekordbox database involved at all.

Why not a Rekordbox master.db, like the other benchmark_iron_*.py scripts: a database's
FolderPath records go stale the moment a library gets reorganized or copied elsewhere, and
matching them back up to real files on a different layout is its own separate problem (see
docs/IRON_RESEARCH.md for the case that motivated this script -- a master.db's FolderPath
entries pointing at a folder structure that no longer existed, on a drive whose actual audio
was very much still present, just laid out differently). Reading BPM/key/genre straight from
each file's own tags (via anvil.read_fields() for bpm/initial_key, mutagen for genre) sidesteps
path-matching entirely: if the file exists and carries a tag, it's usable ground truth,
independent of what any particular DJ software's database currently says about it.

Sampling is stratified by BPM band, then genre, then track length -- not a flat random
draw -- because genre-band octave correction (iron.tempo._GENRE_BANDS) is genre/tempo-
sensitive by design: a random sample that happens to land mostly house (118-130 BPM) proves
nothing about whether DnB/trap (140-180 BPM) actually works, and that's exactly the
population a hardcoded fold range is most likely to get wrong. Track length is stratified
too because a short edit/loop/intro snippet gives Pass 4 (breakdown-duration bar-fit) very
different structure to work with than a full-length track. Round-robin nesting (BPM band
outer, genre middle, length bucket inner -- see `_round_robin`) means a BPM band with real
candidates always gets representation starting from the first few samples, regardless of how
many house tracks are sitting in the pool.

Not tied to any specific drive or mount point -- pass --root at any path this machine has
audio under.

Usage:
    python3 scripts/benchmark_iron_genre_diverse.py --root /Volumes/SOME_DRIVE/Music
    python3 scripts/benchmark_iron_genre_diverse.py --root /path/to/music --count 1000 \\
        --bpm-min 60 --bpm-max 180
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".ogg", ".opus"}
_ENHARMONIC = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}

# Disjoint BPM buckets for stratified sampling and genre-conditional reporting -- NOT the
# same thing as iron.tempo._GENRE_BANDS (those overlap by design, padded, used as an "is this
# BPM near any band" test). These need to partition the full range with no gaps or overlaps
# so every candidate lands in exactly one bucket. Boundaries chosen to separate the specific
# populations most likely to expose genre-band octave-correction problems from each other
# (house vs. DnB vs. hardcore, not lumped into one "everything else" bucket).
_BPM_BUCKETS: tuple[tuple[float, float, str], ...] = (
    (0.0, 85.0, "<85 (downtempo/slow hip-hop)"),
    (85.0, 100.0, "85-100 (hip-hop/trap)"),
    (100.0, 118.0, "100-118 (downtempo/halftime)"),
    (118.0, 130.0, "118-130 (house)"),
    (130.0, 140.0, "130-140 (techno)"),
    (140.0, 160.0, "140-160 (dubstep/halftime DnB)"),
    (160.0, 180.0, "160-180 (drum & bass)"),
    (180.0, float("inf"), "180+ (hardcore/gabber)"),
)


def _bpm_bucket_label(bpm: float) -> str:
    for lo, hi, label in _BPM_BUCKETS:
        if lo <= bpm < hi:
            return label
    return _BPM_BUCKETS[-1][2]


# Disjoint track-length buckets. A short edit/loop/intro snippet gives Pass 4's
# breakdown-duration bar-fit (iron/tempo.py) very different structure than a full track, and
# a DJ-mix-length file is exactly the case iron.analyze(verify_stability=True) exists for --
# worth knowing if any accuracy gap tracks length rather than (or in addition to) BPM/genre.
_LENGTH_BUCKETS: tuple[tuple[float, float, str], ...] = (
    (0.0, 90.0, "<90s (edit/loop/intro)"),
    (90.0, 240.0, "90-240s (typical track)"),
    (240.0, 420.0, "240-420s (extended mix)"),
    (420.0, float("inf"), "420s+ (DJ mix/compilation)"),
)


def _length_bucket_label(duration: float | None) -> str:
    if duration is None or duration <= 0:
        return "unknown length"
    for lo, hi, label in _LENGTH_BUCKETS:
        if lo <= duration < hi:
            return label
    return _LENGTH_BUCKETS[-1][2]


def _read_candidate(path: Path) -> tuple[Path, float, str | None, str, float | None] | None:
    """A single file -> (path, true_bpm, true_camelot_or_None, genre, duration_seconds), or
    None if it has no usable embedded BPM tag. genre is "Unknown" when untagged; duration is
    None when mutagen can't report one (rare -- an unusual/damaged container)."""
    import mutagen

    from anvil import read_fields
    from iron import key as iron_key

    try:
        fields = read_fields(path)
    except Exception:
        return None
    if fields.bpm is None or fields.bpm <= 0:
        return None

    true_camelot = None
    if fields.initial_key:
        raw = fields.initial_key.strip()
        if raw in iron_key.CAMELOT.values():
            true_camelot = raw  # already Camelot
        else:
            note, mode = (raw[:-1], "min") if raw.endswith("m") else (raw, "maj")
            note = _ENHARMONIC.get(note, note)
            true_camelot = iron_key.CAMELOT.get(note + mode)

    genre = "Unknown"
    duration = None
    try:
        tags = mutagen.File(path, easy=True)
        if tags is not None:
            if tags.tags and tags.tags.get("genre"):
                genre = tags.tags["genre"][0].strip() or "Unknown"
            if tags.info is not None and getattr(tags.info, "length", None):
                duration = float(tags.info.length)
    except Exception:
        pass

    return path, fields.bpm, true_camelot, genre, duration


_MAX_FILES_PER_DIR = 150  # a handful of albums' worth -- caps how long any single worker
# task can run, so one oversized folder (a "Various Artists"/compilation mega-folder, common
# in real libraries) can't stall the whole scan behind it.


def _scan_one_dir(dir_str: str) -> list[tuple[str, float, str | None, str, float | None]]:
    """Worker for one top-level subdirectory -- walked and read in a separate process.
    Returns plain str paths (Path objects aren't needed across the process boundary and
    str is trivially picklable)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    out = []
    checked = 0
    for path in Path(dir_str).rglob("*"):
        if path.name.startswith(".") or path.suffix.lower() not in _AUDIO_EXTENSIONS:
            continue
        checked += 1
        candidate = _read_candidate(path)
        if candidate is not None:
            p, bpm, camelot, genre, duration = candidate
            out.append((str(p), bpm, camelot, genre, duration))
        if checked >= _MAX_FILES_PER_DIR:
            break
    return out


def _scan_candidates(
    root: Path, *, scan_limit: int, workers: int, seed: int
) -> list[tuple[Path, float, str | None, str, float | None]]:
    """
    Scan for audio files with a usable embedded BPM tag, stopping once `scan_limit`
    candidates have accumulated rather than walking the entire tree -- a library can be far
    too large (this script's motivating case: 10,000+ top-level artist folders) to fully
    tag-scan in reasonable time, and a randomized cross-section of top-level subdirectories
    gives the same genre diversity a full walk would, at a small fraction of the cost.

    Each top-level subdirectory of `root` is scanned as one parallel unit (folder-level
    parallelism, not file-level -- keeps worker overhead low relative to per-file tag-read
    cost). Subdirectories are shuffled first so an early stop doesn't systematically favor
    whatever sorts alphabetically first.
    """
    top_level = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
    rng = random.Random(seed)
    rng.shuffle(top_level)

    # Submitted in small batches (a couple rounds per worker), not all 10,000+ up front --
    # the old all-at-once submission needed `wait=False, cancel_futures=True` at shutdown to
    # avoid blocking on however many folders were still mid-scan when scan_limit hit, which
    # leaked multiprocessing semaphores on essentially every real run (scan_limit is reached
    # long before the full tree is scanned). Batching means nothing is ever "still running
    # but abandoned" when the loop decides to stop -- the in-flight batch is always let to
    # finish first -- so a plain, cheap `wait=True` at the end is always correct AND fast.
    batch_size = max(1, workers * 2)
    candidates: list[tuple[Path, float, str | None, str, float | None]] = []
    i = 0
    pool = ProcessPoolExecutor(max_workers=workers)
    try:
        for batch_start in range(0, len(top_level), batch_size):
            if len(candidates) >= scan_limit:
                print(f"  reached scan_limit={scan_limit} after {i}/{len(top_level)} "
                      f"folders -- stopping scan early", flush=True)
                break
            batch = top_level[batch_start:batch_start + batch_size]
            futures = {pool.submit(_scan_one_dir, str(d)): d for d in batch}
            for fut in as_completed(futures):
                i += 1
                try:
                    results = fut.result()
                except Exception as e:
                    print(f"  scan error in {futures[fut]}: {e}", file=sys.stderr)
                    continue
                for path_str, bpm, camelot, genre, duration in results:
                    candidates.append((Path(path_str), bpm, camelot, genre, duration))
                if i % 25 == 0:
                    print(f"  scanned {i}/{len(top_level)} folders, "
                          f"{len(candidates)} candidates so far", flush=True)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return candidates


def _round_robin(tree, rng: random.Random):
    """
    Yield items from a nested dict-of-dicts-of-list structure in round-robin order at every
    level, shuffling key order at each level with `rng` first. A key (bucket) whose sub-tree
    is exhausted drops out of rotation but doesn't block the others -- the same fairness
    property the old flat genre-only round robin had, generalized to arbitrary nesting depth
    (here: BPM bucket -> genre -> length bucket -> list of candidates).
    """
    if isinstance(tree, list):
        rng.shuffle(tree)
        yield from tree
        return
    keys = list(tree.keys())
    rng.shuffle(keys)
    subgens = {k: _round_robin(tree[k], rng) for k in keys}
    active = list(keys)
    i = 0
    while active:
        k = active[i % len(active)]
        try:
            item = next(subgens[k])
        except StopIteration:
            active.remove(k)
            continue
        yield item
        i += 1


def _stratified_sample(
    candidates: list[tuple[Path, float, str | None, str, float | None]], count: int, seed: int
) -> list[tuple[Path, float, str | None, str, float | None]]:
    """Stratify by BPM bucket (outer), then genre (middle), then track-length bucket (inner)
    -- see the module docstring for why BPM bucket is the outer/primary axis. Round-robin
    nesting means a BPM bucket with even one candidate still shows up in the first few
    samples, rather than being crowded out by however many house tracks happen to dominate
    the raw candidate pool."""
    tree: dict = {}
    for item in candidates:
        _p, bpm, _k, genre, duration = item
        b = _bpm_bucket_label(bpm)
        length_bucket = _length_bucket_label(duration)
        tree.setdefault(b, {}).setdefault(genre, {}).setdefault(length_bucket, []).append(item)

    rng = random.Random(seed)
    selected: list[tuple[Path, float, str | None, str, float | None]] = []
    for item in _round_robin(tree, rng):
        selected.append(item)
        if len(selected) >= count:
            break
    return selected


_OCTAVE_RATIOS: tuple[tuple[float, str], ...] = (
    (0.5, "half-time (~0.5x true)"),
    (2.0, "double-time (~2x true)"),
    (2.0 / 3.0, "2:3 compound-meter (~0.667x true)"),
    (1.5, "3:2 compound-meter (~1.5x true)"),
)
_OCTAVE_RATIO_TOL = 0.03  # relative tolerance for calling a ratio "clean"


def _classify_tempo_error(detected: float, true_bpm: float) -> str:
    """Distinguish a known octave/compound-meter failure mode (a specific, explainable, clean
    ratio to the true tempo -- see docs/IRON_RESEARCH.md SS2/SS3) from a genuinely wrong
    estimate that isn't a clean multiple/submultiple of anything. These need different fixes:
    an octave-fold miss points at the genre-band/cyclic-tempogram correction passes, while a
    genuinely-wrong estimate points at Pass 1's raw onset/harmonic-sum scoring itself.
    Collapsing both into one "wrong" bucket, as a plain accuracy percentage does, hides which
    one is actually driving a given accuracy gap."""
    ratio = detected / true_bpm
    for target, label in _OCTAVE_RATIOS:
        if abs(ratio - target) / target <= _OCTAVE_RATIO_TOL:
            return label
    return "genuinely wrong (no clean octave/compound-meter ratio)"


def _tempo_error_breakdown(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        d, t = r["detected_bpm"], r["true_bpm"]
        if d is None or t is None or t <= 0:
            continue
        if abs(d - t) / t <= 0.04:  # MIREX-correct -- not an error to classify
            continue
        counts[_classify_tempo_error(d, t)] += 1
    return dict(counts)


def _camelot_parts(c: str) -> tuple[int, str] | None:
    if len(c) < 2:
        return None
    try:
        num = int(c[:-1])
    except ValueError:
        return None
    letter = c[-1]
    if letter not in ("A", "B") or not (1 <= num <= 12):
        return None
    return num, letter


def _classify_key_error(detected: str, true_camelot: str) -> str:
    """Distinguish a near-miss a DJ could still beatmix around (relative major/minor -- same
    number, other letter; or an adjacent perfect-fifth neighbor -- same letter, number +-1)
    from a genuinely random wrong key. Both near-miss relationships share real harmonic
    content with the true key, which is exactly the kind of confusion a chroma-correlation
    detector (iron/key.py) is expected to make more often than an unrelated key -- worth
    knowing whether that's actually true on real data, or whether misses are scattered."""
    d = _camelot_parts(detected)
    t = _camelot_parts(true_camelot)
    if d is None or t is None:
        return "unparseable"
    dn, dl = d
    tn, tl = t
    if dn == tn and dl != tl:
        return "relative major/minor (same number, other letter)"
    if dl == tl and (dn - tn) % 12 in (1, 11):
        return "adjacent (perfect-fifth neighbor, same letter +-1)"
    return "random (no near-miss relationship)"


def _key_error_breakdown(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        d, t = r["detected_camelot"], r["true_camelot"]
        if d is None or t is None or d == t:
            continue
        counts[_classify_key_error(d, t)] += 1
    return dict(counts)


def _analyze_one(
    args: tuple[str, float, str | None, str, float | None, float, float],
) -> dict:
    path_str, true_bpm, true_camelot, genre, duration, bpm_min, bpm_max = args
    path = Path(path_str)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import iron

    result = {
        "path": path_str, "genre": genre, "true_bpm": true_bpm, "true_camelot": true_camelot,
        "duration": duration, "detected_bpm": None, "detected_camelot": None, "error": None,
    }
    try:
        out = iron.analyze(path, want=("bpm", "initial_key"), bpm_min=bpm_min, bpm_max=bpm_max)
        result["detected_bpm"] = out.bpm
        result["detected_camelot"] = out.initial_key
    except Exception as e:
        result["error"] = str(e)
    return result


def _tempo_accuracy(rows: list[dict]) -> dict[str, float]:
    pairs = [(r["detected_bpm"], r["true_bpm"]) for r in rows if r["detected_bpm"] is not None]
    if not pairs:
        return {"n": 0, "exact": 0.0, "within_1pct": 0.0, "mirex": 0.0}
    exact = sum(1 for d, t in pairs if abs(d - t) <= 0.6)
    within_1pct = sum(1 for d, t in pairs if abs(d - t) / t <= 0.01)
    mirex = sum(1 for d, t in pairs if abs(d - t) / t <= 0.04)
    n = len(pairs)
    return {"n": n, "exact": exact / n, "within_1pct": within_1pct / n, "mirex": mirex / n}


def _key_accuracy(rows: list[dict]) -> dict[str, float]:
    pairs = [
        (r["detected_camelot"], r["true_camelot"])
        for r in rows if r["detected_camelot"] is not None and r["true_camelot"] is not None
    ]
    if not pairs:
        return {"n": 0, "exact": 0.0}
    exact = sum(1 for d, t in pairs if d == t)
    return {"n": len(pairs), "exact": exact / len(pairs)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=None,
                         help="directory to scan for audio (required unless --replay-jsonl "
                              "is given)")
    parser.add_argument("--count", type=int, default=1000, help="target sample size")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--bpm-min", type=float, default=30.0)
    parser.add_argument("--bpm-max", type=float, default=300.0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--scan-limit", type=int, default=None,
                         help="stop scanning once this many candidates are found (default: "
                              "count * 6, generous headroom for genre-diverse round-robin "
                              "sampling without walking the whole tree)")
    parser.add_argument("--out", type=Path, default=None, help="write per-track JSONL results here")
    parser.add_argument("--replay-jsonl", type=Path, default=None,
                         help="reuse the exact (path, true_bpm, true_camelot, genre) sample "
                              "from a previous run's --out JSONL instead of rescanning -- "
                              "for a clean apples-to-apples before/after comparison on "
                              "identical tracks when only the detection code changed")
    args = parser.parse_args(argv)

    if args.replay_jsonl:
        sample = []
        with args.replay_jsonl.open() as f:
            for line in f:
                r = json.loads(line)
                sample.append((
                    Path(r["path"]), r["true_bpm"], r["true_camelot"], r["genre"],
                    r.get("duration"),  # older JSONL files predate this field
                ))
        by_genre = defaultdict(int)
        for _p, _b, _k, g, _d in sample:
            by_genre[g] += 1
        print(f"Replaying {len(sample)} tracks from {args.replay_jsonl} "
              f"({len(by_genre)} genres) -- no rescan", flush=True)
    else:
        if not args.root:
            parser.error("--root is required unless --replay-jsonl is given")
        scan_limit = args.scan_limit or max(args.count * 6, 6000)
        print(f"Scanning {args.root} for audio files with an embedded BPM tag "
              f"(scan_limit={scan_limit}, {args.workers} parallel folder workers)...", flush=True)
        t0 = time.time()
        candidates = _scan_candidates(
            args.root, scan_limit=scan_limit, workers=args.workers, seed=args.seed
        )
        print(f"Found {len(candidates)} candidates with valid BPM in {time.time() - t0:.0f}s",
              flush=True)

        by_genre = defaultdict(int)
        by_bpm_bucket = defaultdict(int)
        for _p, b, _k, g, _d in candidates:
            by_genre[g] += 1
            by_bpm_bucket[_bpm_bucket_label(b)] += 1
        print(f"Genres represented: {len(by_genre)}", flush=True)
        for g, n in sorted(by_genre.items(), key=lambda kv: -kv[1])[:20]:
            print(f"  {g:30s} {n:6d}", flush=True)
        print("BPM buckets in candidate pool (before stratified sampling):", flush=True)
        for _lo, _hi, label in _BPM_BUCKETS:
            print(f"  {label:35s} {by_bpm_bucket.get(label, 0):6d}", flush=True)

        sample = _stratified_sample(candidates, args.count, args.seed)
        print(f"\nSampled {len(sample)} tracks (stratified: BPM bucket -> genre -> length "
              f"bucket, round-robin at each level)", flush=True)

    true_bpms = [b for _p, b, _k, _g, _d in sample]
    below, above = sum(1 for b in true_bpms if b < 60), sum(1 for b in true_bpms if b > 180)
    print(f"Ground-truth BPM distribution: {below} tracks < 60 BPM, "
          f"{sum(1 for b in true_bpms if 60 <= b <= 180)} within [60,180], "
          f"{above} tracks > 180 BPM", flush=True)
    print("Sample BPM buckets:", flush=True)
    sample_bpm_buckets = defaultdict(int)
    for b in true_bpms:
        sample_bpm_buckets[_bpm_bucket_label(b)] += 1
    for _lo, _hi, label in _BPM_BUCKETS:
        print(f"  {label:35s} {sample_bpm_buckets.get(label, 0):6d}", flush=True)
    print("Sample length buckets:", flush=True)
    sample_length_buckets = defaultdict(int)
    for _p, _b, _k, _g, d in sample:
        sample_length_buckets[_length_bucket_label(d)] += 1
    for _lo, _hi, label in _LENGTH_BUCKETS:
        print(f"  {label:35s} {sample_length_buckets.get(label, 0):6d}", flush=True)
    print(flush=True)

    work = [
        (str(p), b, k, g, d, args.bpm_min, args.bpm_max) for p, b, k, g, d in sample
    ]
    rows: list[dict] = []
    t0 = time.time()
    out_f = args.out.open("w") if args.out else None
    # A single pathological real file (a truncated/corrupt encode, ffmpeg waiting on
    # something, a genuine infinite loop in an edge case) can hang one worker forever --
    # as_completed() would then block indefinitely waiting for it, even though the other
    # workers keep finishing their own share. An overall timeout bounds that: generous
    # per-track budget scaled by how many tracks share each worker, so this is a safety net
    # against a real hang, not a tight per-track deadline.
    # iron.analyze() decodes and analyzes the WHOLE track as of 2026-08-27 (docs/
    # IRON_RESEARCH.md SS9), not a bounded ~90s window -- per-track cost now scales with the
    # file's actual length, and a legitimate DJ-mix/compilation-length file (this script's own
    # "420s+" length bucket) can genuinely take minutes, not seconds. 120s/track keeps this a
    # real safety net against a genuine hang without false-flagging a long-but-working file.
    per_track_budget_s = 120.0
    overall_timeout = max(120.0, (len(work) / max(1, args.workers)) * per_track_budget_s + 120.0)
    pool = ProcessPoolExecutor(max_workers=args.workers)
    timed_out = False
    try:
        futures = {pool.submit(_analyze_one, w): w for w in work}
        i = 0
        try:
            for fut in as_completed(futures, timeout=overall_timeout):
                i += 1
                row = fut.result()
                rows.append(row)
                if out_f:
                    out_f.write(json.dumps(row) + "\n")
                    out_f.flush()
                if i % 25 == 0 or i == len(work):
                    print(f"  [{i}/{len(work)}] elapsed={time.time() - t0:.0f}s", flush=True)
        except TimeoutError:
            timed_out = True
            stuck = [w for fut, w in futures.items() if not fut.done()]
            print(f"\n  overall timeout ({overall_timeout:.0f}s) hit with {len(stuck)} track(s) "
                  f"still running -- treating as errors and moving on:", flush=True)
            for path_str, true_bpm, true_camelot, genre, duration, _bmin, _bmax in stuck:
                print(f"    stuck: {path_str}", file=sys.stderr)
                row = {
                    "path": path_str, "genre": genre, "true_bpm": true_bpm,
                    "true_camelot": true_camelot, "duration": duration, "detected_bpm": None,
                    "detected_camelot": None, "error": "timed out",
                }
                rows.append(row)
                if out_f:
                    out_f.write(json.dumps(row) + "\n")
                    out_f.flush()
    finally:
        if out_f:
            out_f.close()
        # wait=True on the normal-completion path: every future is already done at this
        # point, so this returns immediately while still properly joining worker processes
        # -- skipping that join (the old unconditional wait=False) leaked multiprocessing
        # semaphores on every single run, real or not (confirmed via a real run's "resource_
        # tracker: There appear to be N leaked semaphore objects" UserWarning, which was also
        # surfacing as a nonzero exit code despite the benchmark itself completing correctly).
        # Only fall back to wait=False when a worker is actually known-stuck (the TimeoutError
        # path above) -- there, waiting could block program exit indefinitely on a genuinely
        # hung ffmpeg/decode call, which is exactly what this timeout handling exists to avoid.
        pool.shutdown(wait=not timed_out, cancel_futures=True)

    print("\n" + "=" * 70)
    print(f"IRON GENRE-DIVERSE BENCHMARK -- {len(rows)} tracks, "
          f"bpm range=[{args.bpm_min}, {args.bpm_max}]")
    print("=" * 70)
    overall_tempo = _tempo_accuracy(rows)
    overall_key = _key_accuracy(rows)
    print(f"Overall tempo (n={overall_tempo['n']}): exact {overall_tempo['exact']:.1%}  "
          f"within-1% {overall_tempo['within_1pct']:.1%}  MIREX {overall_tempo['mirex']:.1%}")
    print(f"Overall key   (n={overall_key['n']}): exact Camelot match {overall_key['exact']:.1%}")

    print("\nPer-BPM-bucket tempo accuracy -- the genre-conditional answer to \"does it "
          "actually work outside house\" (all buckets shown, not just the well-populated "
          "ones, so a thin bucket's weakness isn't silently dropped):")
    by_bucket_rows = defaultdict(list)
    for r in rows:
        if r["true_bpm"] is not None:
            by_bucket_rows[_bpm_bucket_label(r["true_bpm"])].append(r)
    for _lo, _hi, label in _BPM_BUCKETS:
        b_rows = by_bucket_rows.get(label, [])
        acc = _tempo_accuracy(b_rows)
        if acc["n"] == 0:
            print(f"  {label:35s} n=   0  (no tracks sampled in this bucket)")
        else:
            print(f"  {label:35s} n={acc['n']:4d}  exact={acc['exact']:.1%}  "
                  f"within1%={acc['within_1pct']:.1%}  mirex={acc['mirex']:.1%}")

    print("\nPer-length-bucket tempo accuracy:")
    by_length_rows = defaultdict(list)
    for r in rows:
        by_length_rows[_length_bucket_label(r.get("duration"))].append(r)
    for _lo, _hi, label in _LENGTH_BUCKETS:
        l_rows = by_length_rows.get(label, [])
        acc = _tempo_accuracy(l_rows)
        if acc["n"] > 0:
            print(f"  {label:35s} n={acc['n']:4d}  exact={acc['exact']:.1%}  "
                  f"within1%={acc['within_1pct']:.1%}  mirex={acc['mirex']:.1%}")

    print("\nPer-genre tempo accuracy (genres with >= 10 compared tracks):")
    by_genre_rows = defaultdict(list)
    for r in rows:
        by_genre_rows[r["genre"]].append(r)
    for g, g_rows in sorted(by_genre_rows.items(), key=lambda kv: -len(kv[1])):
        acc = _tempo_accuracy(g_rows)
        if acc["n"] >= 10:
            print(f"  {g:30s} n={acc['n']:4d}  exact={acc['exact']:.1%}  "
                  f"within1%={acc['within_1pct']:.1%}  mirex={acc['mirex']:.1%}")

    print("\nTempo error breakdown (MIREX-wrong tracks only -- octave/compound-meter misses "
          "vs. genuinely wrong estimates; these need different fixes, see "
          "docs/IRON_RESEARCH.md SS2/SS3):")
    tempo_errors = _tempo_error_breakdown(rows)
    total_wrong = sum(tempo_errors.values())
    if total_wrong == 0:
        print("  (no MIREX-wrong tracks)")
    else:
        for label, n in sorted(tempo_errors.items(), key=lambda kv: -kv[1]):
            print(f"  {label:50s} {n:5d}  ({n / total_wrong:.1%} of wrong)")

    print("\nKey error breakdown (wrong-key tracks only -- near-miss a DJ could still "
          "beatmix around vs. a genuinely random miss):")
    key_errors = _key_error_breakdown(rows)
    total_key_wrong = sum(key_errors.values())
    if total_key_wrong == 0:
        print("  (no wrong-key tracks)")
    else:
        for label, n in sorted(key_errors.items(), key=lambda kv: -kv[1]):
            print(f"  {label:50s} {n:5d}  ({n / total_key_wrong:.1%} of wrong)")

    errors = [r for r in rows if r["error"]]
    if errors:
        print(f"\n{len(errors)} tracks errored (see --out JSONL for details)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
