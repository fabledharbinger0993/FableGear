#!/usr/bin/env python3
"""Trim the pop-out card art to its real extents and install it into the app.

The source art (Marshall's, in ~/Documents/ososdiscos as pop-<tool>.png) is a
rounded neon frame around a near-black interior: it becomes the skin of a tool's
pop-out card, 9-sliced so the painted corners stay crisp while the middle
stretches to whatever size the card is (see #tool-float-modal in fablegear.css).

A 9-slice needs the frame to sit at the SAME depth in every image, because one
`border-image-slice` serves them all. The sources don't: measured on the
mid-height row, the painted edge starts 18px in on most of them, 13px on
settings and 0px on rename. Left alone that renders rename's frame about 40%
thicker than its siblings. So each image is cropped to its alpha bounding box,
which puts the frame flush against the edge everywhere. The box is taken at
alpha > 0, not at a visible threshold, so the soft outward bloom survives the
crop — it's part of the look, and cropping into it would square off the glow.

Reads nothing from the repo and writes only into fablegear/static/chop_shop/pops/.
Re-run it if the source art is redrawn; the outputs are committed, so the app
never depends on ososdiscos being present.

    python3 dev/scripts/build_pop_art.py [--src DIR] [--dry-run]
"""
from __future__ import annotations

import argparse
import struct
import subprocess
import sys
import zlib
from pathlib import Path

# How far in the painted frame reaches. Measured on the mid-height row of all
# eight trimmed images: the band runs 0-27px in, so 30 clears it everywhere.
FRAME = 30

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "fablegear" / "static" / "chop_shop"
DEST = STATIC / "pops"
ICON_DEST = STATIC / "icons"
DEFAULT_SRC = Path.home() / "Documents" / "ososdiscos"

TOOL_NAMES = ("forge", "rename", "refactor", "reorganize", "dedupe", "novelty", "queue", "settings")

# Source stem -> installed name. The six that match existing tiles keep the tile
# name; queue and settings are new art for the Pipeline Wizard and Settings.
TOOLS = {f"pop-{name}": name for name in TOOL_NAMES}

# The dock set, from the same folder. These are the SAME objects drawn bright and
# saturated to be read at 44px, where the card art — painted dark so a card's
# worth of controls can sit on it — reads as a glowing smudge. Two sets because
# the two sizes genuinely want different art, which is why swapping the dock onto
# the card crops was tried on 2026-10-25 and reverted.
ICONS = {f"icon-{name}": name for name in TOOL_NAMES}


def decode_rgba(path: Path) -> tuple[int, int, bytearray]:
    """Minimal 8-bit RGBA PNG reader — there is no PIL in this venv, and adding
    one for a build script that runs when the art changes isn't worth it."""
    raw = path.read_bytes()
    pos, idat, width, height = 8, bytearray(), 0, 0
    while pos < len(raw):
        (length,) = struct.unpack(">I", raw[pos:pos + 4])
        kind = raw[pos + 4:pos + 8]
        chunk = raw[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", chunk[:10])
            if (depth, colour) != (8, 6):
                raise SystemExit(f"{path.name}: expected 8-bit RGBA, got depth={depth} colour={colour}")
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"IEND":
            break
        pos += 12 + length

    buf = zlib.decompress(bytes(idat))
    stride, bpp = width * 4, 4
    out, prev, p = bytearray(), bytearray(stride), 0
    for _ in range(height):
        filt = buf[p]
        line = bytearray(buf[p + 1:p + 1 + stride])
        p += 1 + stride
        if filt == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif filt == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filt == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filt == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                upleft = prev[i - bpp] if i >= bpp else 0
                up = prev[i]
                est = left + up - upleft
                da, db, dc = abs(est - left), abs(est - up), abs(est - upleft)
                line[i] = (line[i] + (left if da <= db and da <= dc else up if db <= dc else upleft)) & 0xFF
        elif filt != 0:
            raise SystemExit(f"{path.name}: unknown row filter {filt}")
        out += line
        prev = line
    return width, height, out


def alpha_box(width: int, height: int, buf: bytearray) -> tuple[int, int, int, int]:
    """Tightest box holding every pixel with any alpha at all."""
    left, right, top, bottom = width, -1, height, -1
    for y in range(height):
        row = buf[y * width * 4:(y + 1) * width * 4]
        xs = [x for x in range(width) if row[x * 4 + 3]]
        if not xs:
            continue
        top = min(top, y)
        bottom = y
        left = min(left, xs[0])
        right = max(right, xs[-1])
    if right < 0:
        raise SystemExit("image is fully transparent")
    return left, top, right - left + 1, bottom - top + 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC, help=f"source art folder (default: {DEFAULT_SRC})")
    ap.add_argument("--dry-run", action="store_true", help="measure and report, write nothing")
    args = ap.parse_args()

    if not args.src.is_dir():
        print(f"source folder not found: {args.src}", file=sys.stderr)
        return 1

    missing = [stem for stem in (*TOOLS, *ICONS) if not (args.src / f"{stem}.png").is_file()]
    if missing:
        print(f"missing source art: {', '.join(sorted(missing))}", file=sys.stderr)
        return 1

    if not args.dry_run:
        DEST.mkdir(parents=True, exist_ok=True)
        ICON_DEST.mkdir(parents=True, exist_ok=True)

    for stem, name in sorted(TOOLS.items(), key=lambda kv: kv[1]):
        src = args.src / f"{stem}.png"
        width, height, buf = decode_rgba(src)
        x, y, w, h = alpha_box(width, height, buf)
        trimmed = f"trim l{x} t{y} r{width - x - w} b{height - y - h}"
        print(f"{name:<11} {width}x{height} -> {w}x{h} + core {w - 2 * FRAME}x{h - 2 * FRAME}  ({trimmed})")
        if args.dry_run:
            continue
        out = DEST / f"{name}.png"
        # sips does the crop so this script stays a reader, not a PNG encoder.
        subprocess.run(
            ["sips", "-c", str(h), str(w), "--cropOffset", str(y), str(x),
             str(src), "--out", str(out)],
            check=True, stdout=subprocess.DEVNULL,
        )
        # ...and again, this time cutting the painted frame off, to leave the bare
        # lit object on its podium. Two assets per tool because the frame and the
        # interior have opposite jobs: the frame is the card's border and wants to
        # stay vivid, while the interior sits under the tool's controls and has to
        # keep quiet. One image at one opacity can't do both. The bare crop is
        # also what the dock buttons want — a 44px button showing a framed card
        # reads as a bright rectangle with a speck in it.
        core = DEST / f"{name}-core.png"
        subprocess.run(
            ["sips", "-c", str(h - 2 * FRAME), str(w - 2 * FRAME),
             "--cropOffset", str(y + FRAME), str(x + FRAME),
             str(src), "--out", str(core)],
            check=True, stdout=subprocess.DEVNULL,
        )

    print()
    for stem, name in sorted(ICONS.items(), key=lambda kv: kv[1]):
        src = args.src / f"{stem}.png"
        width, height, buf = decode_rgba(src)
        x, y, w, h = alpha_box(width, height, buf)
        print(f"{name:<11} icon {width}x{height} -> {w}x{h}")
        if args.dry_run:
            continue
        subprocess.run(
            ["sips", "-c", str(h), str(w), "--cropOffset", str(y), str(x),
             str(src), "--out", str(ICON_DEST / f"{name}.png")],
            check=True, stdout=subprocess.DEVNULL,
        )

    if not args.dry_run:
        print(f"\nwrote {2 * len(TOOLS)} files to {DEST.relative_to(REPO)}"
              f" and {len(ICONS)} to {ICON_DEST.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
