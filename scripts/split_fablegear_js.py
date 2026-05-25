#!/usr/bin/env python3
"""
One-shot splitter: slice static/fablegear.js into per-room/per-shared files.

Preserves exact execution order so global function hoisting, top-level
`let`/`const` initialization, and immediate calls (e.g. brewCheckStatus())
fire identically to the monolith.

Run from repo root:
    python scripts/split_fablegear_js.py

Leaves the original fablegear.js in place as fablegear.js.legacy for
rollback. The new files live under static/shared/, static/record_room/,
and static/chop_shop/.
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC  = ROOT / "static" / "fablegear.js"

# 1-based, inclusive line ranges per output file, in load order.
MANIFEST: list[tuple[str, int, int]] = [
    ("shared/state.js",              1,    38),
    ("shared/file_browser.js",       39,   234),
    ("shared/modals.js",             235,  398),
    ("shared/settings.js",           399,  595),
    ("shared/audit.js",              596,  698),
    ("shared/updates.js",            699,  1204),
    ("shared/health.js",             1205, 1289),
    ("shared/drives.js",             1290, 1388),
    ("record_room/library_mode.js",  1389, 1686),
    ("shared/utility.js",            1687, 1834),
    ("shared/scan_bar.js",           1835, 2132),
    ("chop_shop/runners.js",         2133, 2425),
    ("chop_shop/pipeline.js",        2426, 3512),
    ("chop_shop/dedupe.js",          3513, 4242),
    ("shared/info.js",               4243, 4517),
    ("shared/dnd.js",                4518, 5068),
    ("chop_shop/db_rail.js",         5069, 5116),
    ("record_room/library_editor.js",5117, 5214),
    ("chop_shop/tool_modal.js",      5215, 6059),
    ("record_room/usb_export.js",    6060, 6286),
    ("chop_shop/normalize_preview.js",6287,6458),
    ("shared/state_tracker.js",      6459, 6610),
    ("shared/boot.js",               6611, 6652),
]

HEADER = (
    "/* ════════════════════════════════════════════════════════════════════════\n"
    "   FableGear — {label}\n"
    "   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py\n"
    "   Loaded as a classic script; shares one global scope with the other slices.\n"
    "   Original source lines: {start}-{end}\n"
    "   ──────────────────────────────────────────────────────────────────────── */\n\n"
)


def main() -> None:
    if not SRC.exists():
        legacy = SRC.with_suffix(".js.legacy")
        if legacy.exists():
            print(f"static/fablegear.js is already split. Source preserved at {legacy.relative_to(ROOT)}.")
            print("To re-split: copy the legacy file back to static/fablegear.js, then re-run this script.")
            return
        raise SystemExit(f"Missing source: {SRC}")

    src_lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)
    total = len(src_lines)

    # Sanity check coverage
    covered: list[bool] = [False] * (total + 1)  # 1-based
    for rel, start, end in MANIFEST:
        if start < 1 or end > total or start > end:
            raise SystemExit(f"Bad range for {rel}: {start}-{end} (file has {total} lines)")
        for i in range(start, end + 1):
            if covered[i]:
                raise SystemExit(f"Overlap at line {i} ({rel})")
            covered[i] = True
    missing = [i for i in range(1, total + 1) if not covered[i]]
    if missing:
        # Show first few missing line ranges
        runs: list[tuple[int, int]] = []
        cur_start = missing[0]
        prev = missing[0]
        for ln in missing[1:]:
            if ln == prev + 1:
                prev = ln
                continue
            runs.append((cur_start, prev))
            cur_start = ln
            prev = ln
        runs.append((cur_start, prev))
        raise SystemExit(f"Uncovered line ranges: {runs}")

    # Backup the monolith
    legacy = SRC.with_suffix(".js.legacy")
    if not legacy.exists():
        shutil.copy2(SRC, legacy)

    out_root = ROOT / "static"
    for rel, start, end in MANIFEST:
        out_path = out_root / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        body = "".join(src_lines[start - 1 : end])
        label = rel.replace("/", " / ").replace(".js", "")
        out_path.write_text(HEADER.format(label=label, start=start, end=end) + body, encoding="utf-8")
        print(f"wrote {rel}  ({end - start + 1} lines)")

    print(f"\nTotal: {len(MANIFEST)} files, {total} source lines.")
    print(f"Original preserved at: {legacy.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
