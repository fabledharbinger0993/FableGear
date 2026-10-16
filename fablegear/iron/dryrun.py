"""
fablegear / iron / dryrun.py

Read-only survey of a music folder. Reports what Iron detects, and what it WOULD hand to
Anvil, without ever writing a byte. Same shape as anvil/dryrun.py: this exists because a
synthesized fixture proves the algorithm is correct on files the algorithm's own tests made
up, not what it does across a real, messy library.

    python3 -m iron.dryrun "/Volumes/DRIVE/Some Folder"
    python3 -m iron.dryrun "/path" --json report.json --limit 500

Three questions it answers:

  Coverage.    What fraction of the folder decodes at all? Iron analyzes via ffmpeg rather
               than a container-specific reader, so coverage here is about corrupt/unreadable
               files, not container-family support the way Anvil's coverage question is.

  Condition.   BPM and key distributions, confidence spread, how many clips Iron found no
               reliable tempo or key in at all.

  Blast radius. Composes with anvil.read_fields() -- of files that already carry a bpm/
               initial_key tag, would Iron's candidate be *kept* under Anvil's merge rule, or
               *written* into a field that's currently empty? This is the real end-to-end
               question: what would running Iron + Anvil together actually do to this library.

Iron never writes a tag and never modifies a file. Decoding an audio stream via ffmpeg is the
only thing this does beyond stat/read -- no different from analyze() itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from iron.api import analyze
from iron.errors import IronError

# Anything ffmpeg can plausibly be asked to decode. Iron doesn't pre-filter by container the
# way Anvil's tag layer does -- a bad extension just becomes a decode failure, reported as
# such rather than skipped silently.
AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".aiff", ".aif", ".aifc", ".flac", ".m4a", ".m4p",
    ".mp4", ".m4v", ".ogg", ".opus", ".aac", ".wv",
}


@dataclass
class FileReport:
    path: str
    ext: str
    status: str = "ok"          # ok | unreadable | error
    detail: str = ""
    bpm: float | None = None
    bpm_confidence: float | None = None
    initial_key: str | None = None
    key_confidence: float | None = None
    existing_bpm: float | None = None
    existing_key: str | None = None
    would_write: list[str] = field(default_factory=list)
    would_keep: list[str] = field(default_factory=list)


@dataclass
class Survey:
    root: str
    scanned: int = 0
    files: list[FileReport] = field(default_factory=list)


def _existing_tag_fields(path: Path) -> tuple[float | None, str | None]:
    """
    (existing_bpm, existing_key) via Anvil, or (None, None) if Anvil can't read this
    container or the import isn't available -- the blast-radius section is best-effort
    enrichment, not a hard requirement of the survey.
    """
    try:
        import anvil
    except ImportError:
        return None, None
    try:
        fields = anvil.read_fields(path)
    except Exception:
        return None, None
    return fields.bpm, fields.initial_key


def _inspect(path: Path) -> FileReport:
    report = FileReport(path=str(path), ext=path.suffix.lower())

    result = analyze(path)
    if not result.ok:
        report.status = "error"
        report.detail = "; ".join(result.errors)
        return report

    report.bpm = result.bpm
    report.bpm_confidence = result.bpm_confidence
    report.initial_key = result.initial_key
    report.key_confidence = result.key_confidence

    existing_bpm, existing_key = _existing_tag_fields(path)
    report.existing_bpm = existing_bpm
    report.existing_key = existing_key

    if result.bpm is not None:
        (report.would_keep if existing_bpm is not None else report.would_write).append("bpm")
    if result.initial_key is not None:
        (report.would_keep if existing_key is not None else report.would_write).append(
            "initial_key"
        )

    return report


def survey(root: Path, *, limit: int | None = None, progress: bool = True) -> Survey:
    result = Survey(root=str(root))

    for path in sorted(root.rglob("*")):
        if limit is not None and result.scanned >= limit:
            break
        if not path.is_file():
            continue
        if path.name.startswith("._"):
            continue  # macOS AppleDouble sidecars, not audio
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue

        try:
            report = _inspect(path)
        except IronError as exc:
            report = FileReport(path=str(path), ext=path.suffix.lower(),
                                 status="error", detail=str(exc))

        result.files.append(report)
        result.scanned += 1

        if progress and result.scanned % 250 == 0:
            print(f"  ... {result.scanned} files", file=sys.stderr, flush=True)

    return result


# ─── Reporting ────────────────────────────────────────────────────────────────

def _bar(count: int, total: int, width: int = 28) -> str:
    if not total:
        return ""
    filled = round(width * count / total)
    return "#" * filled + "." * (width - filled)


def _section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def print_report(result: Survey) -> None:
    total = result.scanned
    print("=" * 62)
    print("IRON DRY RUN -- read-only, nothing was written")
    print("=" * 62)
    print(f"Root:    {result.root}")
    print(f"Scanned: {total} audio files")

    if not total:
        print("\nNo audio files found under that path.")
        return

    ok = [f for f in result.files if f.status == "ok"]
    errors = [f for f in result.files if f.status != "ok"]

    _section("Coverage -- can Iron analyze it?")
    for label, group in (("decoded", ok), ("failed to decode", errors)):
        n = len(group)
        print(f"  {label:<20} {n:>6}  {n / total:>5.1%}  {_bar(n, total)}")

    if ok:
        _section("Condition")
        bpm_found = [f for f in ok if f.bpm is not None]
        key_found = [f for f in ok if f.initial_key is not None]
        print(f"  tempo found          {len(bpm_found):>6}  {len(bpm_found) / len(ok):>5.1%}")
        print(f"  key found            {len(key_found):>6}  {len(key_found) / len(ok):>5.1%}")

        if bpm_found:
            confidences = [f.bpm_confidence for f in bpm_found if f.bpm_confidence is not None]
            if confidences:
                print(f"\n  tempo confidence: min {min(confidences):.2f}  "
                      f"mean {sum(confidences) / len(confidences):.2f}  max {max(confidences):.2f}")
            bpm_hist = Counter(int(f.bpm // 10 * 10) for f in bpm_found if f.bpm is not None)
            print("\n  tempo distribution (10 BPM buckets):")
            for bucket, n in sorted(bpm_hist.items()):
                print(f"    {bucket:>3}-{bucket + 9:<3} {n:>6}  {_bar(n, len(bpm_found))}")

        if key_found:
            key_hist = Counter(f.initial_key for f in key_found)
            print("\n  key distribution (top 12):")
            for k, n in key_hist.most_common(12):
                print(f"    {k:<4} {n:>6}  {n / len(key_found):>5.1%}")

        _section("Blast radius -- composing with Anvil's merge rule")
        would_write = Counter(n for f in ok for n in f.would_write)
        would_keep = Counter(n for f in ok for n in f.would_keep)
        for name in ("bpm", "initial_key"):
            w, k = would_write.get(name, 0), would_keep.get(name, 0)
            print(f"  {name}")
            print(f"    would write (field empty)   {w:>6}  {w / len(ok):>5.1%}")
            print(f"    would keep  (already set)   {k:>6}  {k / len(ok):>5.1%}")
        print("\n  'would keep' is Anvil's default merge rule protecting existing data.")

    if errors:
        _section(f"Files Iron could not analyze ({len(errors)})")
        for f in errors[:20]:
            print(f"  {Path(f.path).name}")
            print(f"      {f.detail}")
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more (see --json for the full list)")

    print("\n" + "=" * 62)
    print("No files were modified.")
    print("=" * 62)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m iron.dryrun",
        description="Read-only survey of what Iron detects in a music folder.",
    )
    parser.add_argument("root", type=Path, help="folder to scan (recursive)")
    parser.add_argument("--json", type=Path, metavar="FILE",
                        help="write the full per-file report as JSON")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="stop after N audio files")
    parser.add_argument("--quiet", action="store_true", help="no progress output")
    args = parser.parse_args(argv)

    if not args.root.exists():
        print(f"error: {args.root} does not exist", file=sys.stderr)
        return 2
    if not args.root.is_dir():
        print(f"error: {args.root} is not a directory", file=sys.stderr)
        return 2

    result = survey(args.root, limit=args.limit, progress=not args.quiet)
    print_report(result)

    if args.json:
        payload = {
            "root": result.root,
            "scanned": result.scanned,
            "files": [asdict(f) for f in result.files],
        }
        args.json.write_text(json.dumps(payload, indent=2, default=str))
        print(f"\nFull per-file report written to {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
