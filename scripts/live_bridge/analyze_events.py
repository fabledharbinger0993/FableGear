#!/usr/bin/env python3
"""Summarize and diff passive live-bridge JSONL event captures."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any


def _counter_dict(counter: Counter[Any]) -> dict[str, int]:
    return {str(key): count for key, count in counter.most_common()}


def iter_packets(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL: {exc}") from exc
            packet = payload.get("packet") if isinstance(payload, dict) else None
            if isinstance(packet, dict):
                yield packet


def summarize(path: Path) -> dict[str, Any]:
    ports: Counter[str] = Counter()
    families: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    signatures: Counter[str] = Counter()
    lengths: Counter[int] = Counter()
    packet_types: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    notes: Counter[str] = Counter()
    total = 0

    for packet in iter_packets(path):
        total += 1
        if packet.get("destination_port") is not None:
            ports[str(packet["destination_port"])] += 1
        families[str(packet.get("packet_family") or "unknown")] += 1
        sources[str(packet.get("source") or "unknown")] += 1
        lengths[int(packet.get("length") or 0)] += 1
        if packet.get("packet_type") is not None:
            packet_types[str(packet["packet_type"])] += 1
        if packet.get("label"):
            labels[str(packet["label"])] += 1
        for signature in packet.get("signatures") or []:
            signatures[str(signature)] += 1
        for note in packet.get("notes") or []:
            notes[str(note)] += 1

    return {
        "path": str(path),
        "packet_count": total,
        "ports": _counter_dict(ports),
        "families": _counter_dict(families),
        "sources": _counter_dict(sources),
        "signatures": _counter_dict(signatures),
        "lengths": _counter_dict(lengths),
        "packet_types": _counter_dict(packet_types),
        "labels": _counter_dict(labels),
        "notes": _counter_dict(notes),
    }


def diff_summary(baseline: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {
        "baseline": baseline["path"],
        "action": action["path"],
        "packet_delta": action["packet_count"] - baseline["packet_count"],
        "sections": {},
    }
    for section in ("ports", "families", "sources", "signatures", "lengths", "packet_types", "labels", "notes"):
        base = Counter({key: int(value) for key, value in baseline[section].items()})
        act = Counter({key: int(value) for key, value in action[section].items()})
        keys = sorted(set(base) | set(act))
        rows = [
            {
                "key": key,
                "baseline": base.get(key, 0),
                "action": act.get(key, 0),
                "delta": act.get(key, 0) - base.get(key, 0),
            }
            for key in keys
            if act.get(key, 0) != base.get(key, 0)
        ]
        rows.sort(key=lambda row: abs(row["delta"]), reverse=True)
        diff["sections"][section] = rows
    return diff


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize or diff FableGear live-bridge JSONL captures.")
    parser.add_argument("capture", type=Path, help="Capture JSONL file to summarize.")
    parser.add_argument("action_capture", type=Path, nargs="?", help="Optional action capture to diff against baseline.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    baseline = summarize(args.capture)
    payload = baseline
    if args.action_capture is not None:
        action = summarize(args.action_capture)
        payload = {"baseline_summary": baseline, "action_summary": action, "diff": diff_summary(baseline, action)}
    print(json.dumps(payload, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
