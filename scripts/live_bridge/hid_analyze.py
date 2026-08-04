#!/usr/bin/env python3
"""Analyze HID capture JSONL files to identify control mappings.

Reads captures produced by hid_capture.py and finds:
  - Which byte offsets change between reports (delta analysis)
  - Byte offset behavior profiles (continuous vs binary vs enum)
  - Correlations between labeled actions and byte changes

Workflow:
  1. Capture baseline (controller idle):
       python hid_capture.py --duration 5 --label idle
  2. Capture an action (e.g. move crossfader):
       python hid_capture.py --duration 5 --label crossfader_sweep
  3. Diff them:
       python hid_analyze.py diff idle.jsonl crossfader_sweep.jsonl
  4. Watch live deltas:
       python hid_analyze.py watch capture.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def iter_reports(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("kind") == "hid_report":
                yield obj


def hex_to_bytes(hex_str: str) -> bytes:
    return bytes.fromhex(hex_str.replace(" ", ""))


def profile_offsets(reports: list[bytes]) -> dict[int, dict[str, Any]]:
    """Profile each byte offset across all reports."""
    if not reports:
        return {}

    max_len = max(len(r) for r in reports)
    profiles: dict[int, dict[str, Any]] = {}

    for offset in range(max_len):
        values = [r[offset] for r in reports if offset < len(r)]
        if not values:
            continue

        unique = set(values)
        counter = Counter(values)
        min_val, max_val = min(values), max(values)

        deltas = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
        changes = sum(1 for d in deltas if d != 0)

        if len(unique) == 1:
            behavior = "constant"
        elif unique == {0, 1} or (len(unique) == 2 and 0 in unique):
            behavior = "binary"
        elif max_val - min_val > 100 and changes > len(values) * 0.3:
            behavior = "continuous"
        elif len(unique) <= 8:
            behavior = "enum"
        else:
            behavior = "variable"

        profiles[offset] = {
            "offset": offset,
            "behavior": behavior,
            "unique_values": len(unique),
            "min": min_val,
            "max": max_val,
            "range": max_val - min_val,
            "changes": changes,
            "change_rate": round(changes / max(len(values) - 1, 1), 3),
            "most_common": counter.most_common(5),
        }

    return profiles


def find_deltas(baseline_reports: list[bytes], action_reports: list[bytes]) -> list[dict[str, Any]]:
    """Find byte offsets that differ between baseline and action captures."""
    bp = profile_offsets(baseline_reports)
    ap = profile_offsets(action_reports)

    all_offsets = sorted(set(bp.keys()) | set(ap.keys()))
    deltas = []

    for offset in all_offsets:
        b = bp.get(offset)
        a = ap.get(offset)
        if not b or not a:
            continue

        b_vals = set(v for v, _ in b["most_common"])
        a_vals = set(v for v, _ in a["most_common"])

        range_changed = abs(a["range"] - b["range"]) > 10
        new_values = a_vals - b_vals
        rate_changed = abs(a["change_rate"] - b["change_rate"]) > 0.1
        behavior_changed = a["behavior"] != b["behavior"]

        if range_changed or new_values or rate_changed or behavior_changed:
            deltas.append({
                "offset": offset,
                "offset_hex": f"0x{offset:02X}",
                "baseline_behavior": b["behavior"],
                "action_behavior": a["behavior"],
                "baseline_range": [b["min"], b["max"]],
                "action_range": [a["min"], a["max"]],
                "baseline_change_rate": b["change_rate"],
                "action_change_rate": a["change_rate"],
                "new_values_in_action": sorted(new_values)[:10],
                "confidence": "high" if (range_changed and rate_changed) else "medium",
            })

    deltas.sort(key=lambda d: d["action_change_rate"], reverse=True)
    return deltas


def cmd_summary(args: argparse.Namespace) -> int:
    reports = [hex_to_bytes(r["hex"]) for r in iter_reports(args.capture)]
    if not reports:
        print("No reports found.")
        return 1

    profiles = profile_offsets(reports)
    interesting = {
        k: v for k, v in profiles.items()
        if v["behavior"] != "constant"
    }

    print(f"Capture: {args.capture}")
    print(f"Reports: {len(reports)}")
    print(f"Report size: {len(reports[0])} bytes")
    print(f"Total offsets: {len(profiles)}")
    print(f"Active offsets (non-constant): {len(interesting)}")
    print()

    for behavior in ("continuous", "binary", "enum", "variable"):
        group = [v for v in interesting.values() if v["behavior"] == behavior]
        if not group:
            continue
        print(f"  {behavior.upper()} ({len(group)} offsets):")
        for p in sorted(group, key=lambda x: x["change_rate"], reverse=True)[:15]:
            vals = ", ".join(f"{v}" for v, _ in p["most_common"][:5])
            print(
                f"    offset 0x{p['offset']:02X} ({p['offset']:>3d})  "
                f"range=[{p['min']}-{p['max']}]  "
                f"changes={p['change_rate']:.2f}  "
                f"values: {vals}"
            )
        print()

    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    baseline = [hex_to_bytes(r["hex"]) for r in iter_reports(args.baseline)]
    action = [hex_to_bytes(r["hex"]) for r in iter_reports(args.action)]

    if not baseline or not action:
        print("Need reports in both captures.")
        return 1

    deltas = find_deltas(baseline, action)

    print(f"Baseline: {args.baseline} ({len(baseline)} reports)")
    print(f"Action:   {args.action} ({len(action)} reports)")
    print(f"Changed offsets: {len(deltas)}")
    print()

    high = [d for d in deltas if d["confidence"] == "high"]
    medium = [d for d in deltas if d["confidence"] == "medium"]

    if high:
        print("HIGH CONFIDENCE (likely the control you moved):")
        for d in high:
            print(
                f"  offset {d['offset_hex']} ({d['offset']:>3d})  "
                f"{d['baseline_behavior']} -> {d['action_behavior']}  "
                f"range {d['baseline_range']} -> {d['action_range']}  "
                f"rate {d['baseline_change_rate']:.2f} -> {d['action_change_rate']:.2f}"
            )
        print()

    if medium:
        print("MEDIUM CONFIDENCE (secondary changes):")
        for d in medium[:10]:
            print(
                f"  offset {d['offset_hex']} ({d['offset']:>3d})  "
                f"{d['baseline_behavior']} -> {d['action_behavior']}  "
                f"range {d['baseline_range']} -> {d['action_range']}"
            )

    if args.json:
        print(json.dumps(deltas, indent=2))

    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """Print byte-level diffs between consecutive reports."""
    prev: bytes | None = None
    count = 0

    for report in iter_reports(args.capture):
        raw = hex_to_bytes(report["hex"])
        seq = report.get("seq", "?")
        elapsed = report.get("elapsed_ms", 0)

        if prev is not None:
            diffs = []
            for i in range(min(len(raw), len(prev))):
                if raw[i] != prev[i]:
                    diffs.append((i, prev[i], raw[i]))

            if diffs:
                count += 1
                parts = [f"0x{off:02X}:{old:02X}->{new:02X}" for off, old, new in diffs[:12]]
                extra = f" (+{len(diffs) - 12} more)" if len(diffs) > 12 else ""
                print(f"[{seq:>6}] {elapsed:>10.1f}ms  Δ{len(diffs):>3}  {' '.join(parts)}{extra}")

        prev = raw

    print(f"\n{count} reports with changes from previous.")
    return 0


def cmd_hexdump(args: argparse.Namespace) -> int:
    """Print hex dump of report N (or first report)."""
    for report in iter_reports(args.capture):
        seq = report.get("seq", 0)
        if seq != args.report_num:
            continue

        raw = hex_to_bytes(report["hex"])
        print(f"Report #{seq}, {len(raw)} bytes:")
        for row_start in range(0, len(raw), 16):
            chunk = raw[row_start:row_start + 16]
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            print(f"  {row_start:04X}  {hex_part:<48s}  {ascii_part}")
        return 0

    print(f"Report #{args.report_num} not found.")
    return 1


def cmd_scan(args: argparse.Namespace) -> int:
    """Find all byte offsets that change within a capture, across the full frame."""
    reports = [hex_to_bytes(r["hex"]) for r in iter_reports(args.capture)]
    if not reports:
        print("No reports found.")
        return 1

    max_len = max(len(r) for r in reports)
    print(f"Capture: {args.capture}")
    print(f"Reports: {len(reports)}, Frame size: {max_len} bytes")
    print()

    # For each byte offset, track: unique values, change count, min, max
    results = []
    for offset in range(max_len):
        values = [r[offset] for r in reports if offset < len(r)]
        unique = set(values)
        if len(unique) <= 1:
            continue
        changes = sum(1 for i in range(1, len(values)) if values[i] != values[i - 1])
        min_val, max_val = min(values), max(values)
        results.append({
            "offset": offset,
            "unique": len(unique),
            "changes": changes,
            "change_rate": round(changes / max(len(values) - 1, 1), 3),
            "min": min_val,
            "max": max_val,
            "range": max_val - min_val,
            "sample_values": sorted(unique)[:20],
        })

    results.sort(key=lambda r: r["change_rate"], reverse=True)

    if not results:
        print("No byte offsets changed across the entire capture.")
        return 0

    print(f"ACTIVE BYTE OFFSETS ({len(results)} of {max_len} changed):")
    print(f"{'Offset':<12} {'Changes':<10} {'Rate':<8} {'Range':<12} {'Unique':<8} {'Values (sample)'}")
    print("-" * 90)

    limit = args.top if hasattr(args, "top") and args.top else 40
    for r in results[:limit]:
        vals_str = ", ".join(f"0x{v:02X}" for v in r["sample_values"][:8])
        if len(r["sample_values"]) > 8:
            vals_str += f" (+{len(r['sample_values']) - 8} more)"
        print(
            f"0x{r['offset']:02X} ({r['offset']:>3d})  "
            f"{r['changes']:<10} {r['change_rate']:<8.3f} "
            f"[{r['min']:>3d}-{r['max']:>3d}]   "
            f"{r['unique']:<8} {vals_str}"
        )

    # Highlight likely analog controls (high range, high change rate)
    analog = [r for r in results if r["range"] > 100 and r["change_rate"] > 0.05]
    if analog:
        print(f"\n  LIKELY ANALOG CONTROLS ({len(analog)} offsets with range>100, rate>0.05):")
        for r in analog[:10]:
            print(f"    0x{r['offset']:02X} ({r['offset']:>3d}): range {r['range']}, rate {r['change_rate']:.3f}")

    return 0


def cmd_slice(args: argparse.Namespace) -> int:
    """Print values of specific byte offsets over time (track a control)."""
    offsets = []
    for part in args.offsets.split(","):
        part = part.strip()
        if part.startswith("0x") or part.startswith("0X"):
            offsets.append(int(part, 16))
        else:
            offsets.append(int(part))

    if not offsets:
        print("Specify at least one offset.")
        return 1

    prev_values: list[int] | None = None
    count = 0
    shown = 0

    header_parts = ["   Seq", "  Elapsed"]
    for off in offsets:
        header_parts.append(f"0x{off:02X}")
    print("  ".join(header_parts))
    print("-" * (20 + 8 * len(offsets)))

    for report in iter_reports(args.capture):
        raw = hex_to_bytes(report["hex"])
        seq = report.get("seq", "?")
        elapsed = report.get("elapsed_ms", 0)
        count += 1

        current = [raw[o] if o < len(raw) else 0 for o in offsets]

        if prev_values is None or current != prev_values:
            parts = [f"{seq:>6}", f"{elapsed:>10.1f}ms"]
            for i, val in enumerate(current):
                changed = prev_values is not None and val != prev_values[i]
                marker = "*" if changed else " "
                parts.append(f"{marker}{val:>3d}(0x{val:02X})")
            print("  ".join(parts))
            shown += 1

            if hasattr(args, "max_lines") and args.max_lines and shown >= args.max_lines:
                print(f"  ... (stopped at {args.max_lines} lines, {count} reports read)")
                break

        prev_values = current

    print(f"\n{shown} state changes shown from {count} reports.")
    return 0


def cmd_transitions(args: argparse.Namespace) -> int:
    """Show the first N reports where a specific offset changes value."""
    offset = int(args.offset, 0) if args.offset.startswith("0x") or args.offset.startswith("0X") else int(args.offset)
    prev_val: int | None = None
    shown = 0
    limit = args.count

    print(f"Transitions at offset 0x{offset:02X} ({offset}):")
    print(f"{'Seq':>8}  {'Elapsed':>12}  {'From':>8}  {'To':>8}  {'Delta':>8}")
    print("-" * 55)

    for report in iter_reports(args.capture):
        raw = hex_to_bytes(report["hex"])
        if offset >= len(raw):
            continue
        val = raw[offset]
        if prev_val is not None and val != prev_val:
            seq = report.get("seq", "?")
            elapsed = report.get("elapsed_ms", 0)
            delta = val - prev_val
            print(f"{seq:>8}  {elapsed:>10.1f}ms  0x{prev_val:02X}({prev_val:>3d})  0x{val:02X}({val:>3d})  {delta:>+4d}")
            shown += 1
            if shown >= limit:
                break
        prev_val = val

    print(f"\n{shown} transitions found.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Analyze HID capture files from hid_capture.py.")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("summary", help="Profile all byte offsets in a capture.")
    s.add_argument("capture", type=Path)

    d = sub.add_parser("diff", help="Find byte offsets that changed between two captures.")
    d.add_argument("baseline", type=Path, help="Idle/baseline capture.")
    d.add_argument("action", type=Path, help="Action capture (e.g. moving one control).")
    d.add_argument("--json", action="store_true", help="Also dump raw JSON deltas.")

    w = sub.add_parser("watch", help="Print byte-level diffs between consecutive reports.")
    w.add_argument("capture", type=Path)

    h = sub.add_parser("hexdump", help="Hex dump a specific report.")
    h.add_argument("capture", type=Path)
    h.add_argument("report_num", type=int, nargs="?", default=1, help="Report sequence number.")

    sc = sub.add_parser("scan", help="Find all byte offsets that change within a capture (full frame).")
    sc.add_argument("capture", type=Path)
    sc.add_argument("--top", type=int, default=40, help="Show top N most-active offsets.")

    sl = sub.add_parser("slice", help="Track specific byte offsets over time.")
    sl.add_argument("capture", type=Path)
    sl.add_argument("offsets", help="Comma-separated byte offsets (decimal or 0xHex). E.g. '2,5,0x10'.")
    sl.add_argument("--max-lines", type=int, default=200, help="Max state-change lines to print.")

    tr = sub.add_parser("transitions", help="Show value transitions at a single byte offset.")
    tr.add_argument("capture", type=Path)
    tr.add_argument("offset", help="Byte offset (decimal or 0xHex).")
    tr.add_argument("--count", type=int, default=100, help="Max transitions to show.")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cmd = {
        "summary": cmd_summary,
        "diff": cmd_diff,
        "watch": cmd_watch,
        "hexdump": cmd_hexdump,
        "scan": cmd_scan,
        "slice": cmd_slice,
        "transitions": cmd_transitions,
    }
    return cmd[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
