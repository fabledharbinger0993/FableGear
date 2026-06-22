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
import sys
from collections import Counter, defaultdict
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

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cmd = {
        "summary": cmd_summary,
        "diff": cmd_diff,
        "watch": cmd_watch,
        "hexdump": cmd_hexdump,
    }
    return cmd[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
