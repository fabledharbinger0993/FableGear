"""
fablegear / anvil / dryrun.py

Read-only survey of a music folder. Reports what Anvil sees and what it WOULD
do, and never writes a byte.

    python3 -m anvil.dryrun "/Volumes/DRIVE/Some Folder"
    python3 -m anvil.dryrun "/path" --json report.json --limit 500

This exists because a synthesized fixture cannot tell you what a real library
looks like. The suite proves Anvil is correct on files Anvil made up; this
tells you how it behaves on twenty years of accumulated rips, downloads,
re-tags, and whatever the last five tools left behind.

Three questions it answers:

  Coverage.   Anvil handles container family A (MP3/WAV/AIFF) today. What
              share of a real library is that? The answer decides whether
              families B-D are urgent or theoretical.

  Condition.  Which files carry a tag at all, which ID3 version, what text
              encodings, how much BPM and key data already exists, and what
              other tools have written into TXXX frames.

  Blast radius. For every file, what a write would change under the default
              merge rule versus what it would keep. A dry run that cannot
              answer this is just an inventory.

Nothing here opens a file for writing. The only filesystem calls are stat and
read.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from anvil import api, containers, id3
from anvil.errors import AnvilError, CorruptHeader, UnsupportedFormat

# Extensions FableGear considers audio (config.py::AUDIO_EXTENSIONS), split by
# whether Anvil can handle them today. Listing the unsupported group explicitly
# is the point -- "not supported yet" and "broken file" are different findings
# and must not be reported as the same number.
FAMILY_A = {".mp3", ".wav", ".aiff", ".aif", ".aifc"}          # ID3v2
FAMILY_B = {".flac", ".ogg", ".opus"}                          # Vorbis comments
FAMILY_C = {".m4a", ".m4p", ".mp4", ".m4v"}                    # MP4 atoms
# Raw ADTS AAC has no reliable tag container of its own, and WavPack was never
# in scope -- both stay genuinely unimplemented, not just untested.
NOT_YET = {".aac", ".wv"}
AUDIO_EXTENSIONS = FAMILY_A | FAMILY_B | FAMILY_C | NOT_YET

_ENCODING_NAMES = {
    id3.ENC_LATIN1: "latin-1",
    id3.ENC_UTF16_BOM: "utf-16",
    id3.ENC_UTF16_BE: "utf-16be",
    id3.ENC_UTF8: "utf-8",
}

# Descriptions Anvil owns. Anything else in a TXXX frame was put there by some
# other tool, and knowing which tools have been through the library is useful
# on its own.
_OWN_TXXX = {
    "MIXDESCRIPTOR", "TRACKROLE", "ENERGYLEVEL",
    "DOWNBEATOFFSET", "TIMESIGNATURE", "BPM_PRECISE",
}


@dataclass
class FileReport:
    path: str
    ext: str
    size: int = 0
    status: str = "ok"          # ok | unsupported_ext | unreadable | error
    detail: str = ""
    container: str = ""
    id3_version: int | None = None
    has_tag: bool = False
    frame_count: int = 0
    fields: dict[str, Any] = field(default_factory=dict)
    encodings: list[str] = field(default_factory=list)
    foreign_txxx: list[str] = field(default_factory=list)
    would_write: list[str] = field(default_factory=list)
    would_keep: list[str] = field(default_factory=list)


@dataclass
class Survey:
    root: str
    scanned: int = 0
    files: list[FileReport] = field(default_factory=list)

    def counter(self, attr: str) -> Counter:
        return Counter(getattr(f, attr) for f in self.files)


def _inspect(path: Path, candidates: set[str]) -> FileReport:
    """Read one file and report. Never writes; never decodes audio."""
    ext = path.suffix.lower()
    report = FileReport(path=str(path), ext=ext)

    try:
        report.size = path.stat().st_size
    except OSError as exc:
        report.status = "unreadable"
        report.detail = str(exc)
        return report

    if ext in NOT_YET:
        # Not a defect -- a container family Anvil has not built yet.
        report.status = "unsupported_ext"
        report.detail = "raw AAC / WavPack (not implemented)"
        return report

    try:
        if ext in FAMILY_A:
            # ID3 has detail worth reporting (version, per-frame encodings,
            # foreign TXXX frames) that Vorbis/MP4 have no equivalent of --
            # read_tag() exposes the parsed tag object so _inspect can get at it.
            kind, tag, _data = api.read_tag(path)
            report.container = kind

            if tag is None:
                report.has_tag = False
                report.would_write = sorted(candidates)
                return report

            report.has_tag = True
            report.id3_version = tag.version
            report.frame_count = len(tag.frames)

            encodings = set()
            for frame in tag.frames:
                if frame.id.startswith("T") and frame.data:
                    encodings.add(_ENCODING_NAMES.get(frame.data[0], f"?{frame.data[0]}"))
            report.encodings = sorted(encodings)

            for frame in tag.get_all("TXXX"):
                description, _value = id3.decode_txxx(frame.data)
                if description and description.upper() not in _OWN_TXXX:
                    report.foreign_txxx.append(description)

            fields = api._fields_from_tag(tag)
        else:
            # Family B (FLAC/Ogg) and C (MP4): no ID3-specific concepts to
            # report, but read_fields() already dispatches to the right
            # container module, so coverage/blast-radius reporting is uniform.
            data = path.read_bytes()
            report.container = containers.sniff(data)
            fields = api.read_fields(path)
            report.has_tag = not fields.is_empty()

        report.fields = {k: v for k, v in asdict(fields).items() if v is not None}

        # The merge rule, simulated. A field already carrying a value would be
        # kept; an empty one would be written. No file is opened for writing to
        # work this out.
        for name in sorted(candidates):
            if getattr(fields, name, None) is None:
                report.would_write.append(name)
            else:
                report.would_keep.append(name)

    except UnsupportedFormat as exc:
        report.status = "unsupported_ext"
        report.detail = f"bytes do not match extension: {exc}"
    except CorruptHeader as exc:
        report.status = "error"
        report.detail = f"corrupt: {exc}"
    except AnvilError as exc:
        report.status = "error"
        report.detail = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        report.status = "error"
        report.detail = f"unexpected {type(exc).__name__}: {exc}"

    return report


def survey(
    root: Path,
    *,
    candidates: set[str] | None = None,
    limit: int | None = None,
    progress: bool = True,
) -> Survey:
    """Walk `root` and inspect every audio file found."""
    candidates = candidates or {"bpm", "initial_key"}
    result = Survey(root=str(root))

    for path in sorted(root.rglob("*")):
        if limit is not None and result.scanned >= limit:
            break
        if not path.is_file():
            continue
        if path.name.startswith("._"):
            continue        # macOS AppleDouble sidecars, not audio
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue

        result.files.append(_inspect(path, candidates))
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


def print_report(result: Survey, candidates: set[str]) -> None:
    total = result.scanned
    print("=" * 62)
    print("ANVIL DRY RUN -- read-only, nothing was written")
    print("=" * 62)
    print(f"Root:    {result.root}")
    print(f"Scanned: {total} audio files")

    if not total:
        print("\nNo audio files found under that path.")
        return

    ok = [f for f in result.files if f.status == "ok"]
    unsupported = [f for f in result.files if f.status == "unsupported_ext"]
    errors = [f for f in result.files if f.status in ("error", "unreadable")]

    _section("Coverage -- can Anvil handle it today?")
    for label, group in (
        ("handled (families A/B/C)", ok),
        ("not implemented (raw AAC/WavPack)", unsupported),
        ("failed to read", errors),
    ):
        n = len(group)
        print(f"  {label:<24} {n:>6}  {n / total:>5.1%}  {_bar(n, total)}")

    _section("By extension")
    for ext, n in result.counter("ext").most_common():
        marker = "" if ext not in NOT_YET else "   <- not implemented (raw AAC/WavPack)"
        print(f"  {ext or '(none)':<10} {n:>6}  {n / total:>5.1%}{marker}")

    if ok:
        _section("Tag condition (of files Anvil can read)")
        tagged = [f for f in ok if f.has_tag]
        print(f"  carries a tag           {len(tagged):>6}  {len(tagged) / len(ok):>5.1%}")
        print(f"  no tag block at all     {len(ok) - len(tagged):>6}  "
              f"{(len(ok) - len(tagged)) / len(ok):>5.1%}")

        # id3_version is only populated for family A (ID3); Vorbis/MP4 have
        # no version concept of their own, so they are excluded here rather
        # than printed as a misleading "ID3v2.None".
        versions = Counter(f.id3_version for f in tagged if f.id3_version is not None)
        for version, n in sorted(versions.items(), key=lambda kv: -kv[1]):
            print(f"  ID3v2.{version}                 {n:>6}  {n / len(ok):>5.1%}")

        encodings = Counter(e for f in tagged for e in f.encodings)
        if encodings:
            print("\n  text encodings in use:")
            for enc, n in encodings.most_common():
                print(f"    {enc:<10} {n:>6} files")

        _section("Field coverage (of files Anvil can read)")
        for name in ("title", "artist", "album", "bpm", "initial_key",
                     "mix_descriptor", "track_role", "energy_level"):
            n = sum(1 for f in ok if name in f.fields)
            print(f"  {name:<16} {n:>6}  {n / len(ok):>5.1%}  {_bar(n, len(ok))}")

        fractional = [
            f for f in ok
            if isinstance(f.fields.get("bpm"), float)
            and abs(f.fields["bpm"] - round(f.fields["bpm"])) > 1e-6
        ]
        if fractional:
            print(f"\n  {len(fractional)} file(s) carry a fractional BPM -- precision")
            print("  a plain round() would discard.")

        foreign = Counter(d for f in ok for d in f.foreign_txxx)
        if foreign:
            _section("TXXX frames written by other tools")
            print("  (a blanket remove('TXXX') would destroy these)")
            for description, n in foreign.most_common(15):
                print(f"    {description:<32} {n:>6} files")

        _section(f"What a write of {sorted(candidates)} would do")
        would_write = Counter(n for f in ok for n in f.would_write)
        would_keep = Counter(n for f in ok for n in f.would_keep)
        for name in sorted(candidates):
            w, k = would_write.get(name, 0), would_keep.get(name, 0)
            print(f"  {name}")
            print(f"    would write (field empty)   {w:>6}  {w / len(ok):>5.1%}")
            print(f"    would keep  (already set)   {k:>6}  {k / len(ok):>5.1%}")
        print("\n  'would keep' is the default merge rule protecting existing")
        print("  data. force={'bpm'} would overwrite those instead.")

    if errors:
        _section(f"Files Anvil could not read ({len(errors)})")
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
        prog="python3 -m anvil.dryrun",
        description="Read-only survey of what Anvil sees in a music folder.",
    )
    parser.add_argument("root", type=Path, help="folder to scan (recursive)")
    parser.add_argument("--json", type=Path, metavar="FILE",
                        help="write the full per-file report as JSON")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="stop after N audio files")
    parser.add_argument("--candidates", default="bpm,initial_key",
                        help="fields a hypothetical write would supply "
                             "(default: bpm,initial_key)")
    parser.add_argument("--quiet", action="store_true", help="no progress output")
    args = parser.parse_args(argv)

    if not args.root.exists():
        print(f"error: {args.root} does not exist", file=sys.stderr)
        return 2
    if not args.root.is_dir():
        print(f"error: {args.root} is not a directory", file=sys.stderr)
        return 2

    candidates = {c.strip() for c in args.candidates.split(",") if c.strip()}
    unknown = candidates - _known_fields()
    if unknown:
        print(f"error: unknown field(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    result = survey(args.root, candidates=candidates, limit=args.limit,
                    progress=not args.quiet)
    print_report(result, candidates)

    if args.json:
        payload = {
            "root": result.root,
            "scanned": result.scanned,
            "files": [asdict(f) for f in result.files],
        }
        args.json.write_text(json.dumps(payload, indent=2, default=str))
        print(f"\nFull per-file report written to {args.json}")

    return 0


def _known_fields() -> set[str]:
    from anvil.schema import TrackFields
    return set(TrackFields.field_names())


if __name__ == "__main__":
    raise SystemExit(main())
