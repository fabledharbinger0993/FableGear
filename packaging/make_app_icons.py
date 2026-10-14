#!/usr/bin/env python3
"""Build FableGear's icon files from static/fablegear.png.

Writes static/fablegear-icon-1024.png (square master), static/FableGear.iconset/ (the ten
sizes Apple's iconutil expects), static/FableGear.icns and static/FableGear.ico.

Run it on macOS (it calls Apple's `iconutil`) and needs Pillow, which is NOT a FableGear
runtime dependency: use any throwaway venv
(`python -m venv /tmp/v && /tmp/v/bin/python -m pip install pillow`), then commit the output.

The art is wide and not square, so it is trimmed to its visible pixels, centred on Apple's
macOS icon grid (an 824 px rounded tile on a 1024 px canvas, 100 px margin) and drawn over a
dark tile, so it reads like other Dock icons instead of a tiny floating logo.
"""
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "static" / "fablegear.png"
MASTER = ROOT / "static" / "fablegear-icon-1024.png"
ICONSET = ROOT / "static" / "FableGear.iconset"
ICNS = ROOT / "static" / "FableGear.icns"
ICO = ROOT / "static" / "FableGear.ico"

CANVAS, TILE, RADIUS = 1024, 824, 185
ART_FRACTION = 0.92  # of the tile width


def build_master() -> Image.Image:
    art = Image.open(SRC).convert("RGBA")
    # Trim to visible pixels; the faint outer glow (alpha < 10) would only add dead margin.
    bbox = art.getchannel("A").point(lambda a: 255 if a >= 10 else 0).getbbox()
    art = art.crop(bbox)
    scale = TILE * ART_FRACTION / max(art.size)
    art = art.resize((round(art.width * scale), round(art.height * scale)), Image.LANCZOS)

    # 4x supersampled tile mask for clean rounded corners
    mask = Image.new("L", (CANVAS * 4, CANVAS * 4), 0)
    m = (CANVAS - TILE) // 2 * 4
    ImageDraw.Draw(mask).rounded_rectangle((m, m, CANVAS * 4 - m, CANVAS * 4 - m), RADIUS * 4, fill=255)
    mask = mask.resize((CANVAS, CANVAS), Image.LANCZOS)

    tile = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    top, bottom = (22, 14, 38), (6, 6, 14)
    grad = Image.new("RGBA", (1, CANVAS))
    for y in range(CANVAS):
        t = y / (CANVAS - 1)
        grad.putpixel((0, y), (*(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)), 255))
    tile.paste(grad.resize((CANVAS, CANVAS)), (0, 0), mask)
    tile.alpha_composite(art, ((CANVAS - art.width) // 2, (CANVAS - art.height) // 2))
    out = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    out.paste(tile, (0, 0), mask)  # keep the art inside the tile
    return out


def main() -> int:
    master = build_master()
    master.save(MASTER, optimize=True)

    if ICONSET.exists():
        shutil.rmtree(ICONSET)
    ICONSET.mkdir()
    for size in (16, 32, 128, 256, 512):
        master.resize((size, size), Image.LANCZOS).save(ICONSET / f"icon_{size}x{size}.png")
        master.resize((size * 2, size * 2), Image.LANCZOS).save(ICONSET / f"icon_{size}x{size}@2x.png")

    subprocess.run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)], check=True)
    master.save(ICO, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("wrote", MASTER.name, ICONSET.name, ICNS.name, ICO.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
