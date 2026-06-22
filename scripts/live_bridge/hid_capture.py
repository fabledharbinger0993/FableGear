#!/usr/bin/env python3
"""Capture raw HID reports from AlphaTheta DJ controllers (Omnis Duo, DDJ-FLX series, etc).

Reads vendor-defined HID input reports in real time and writes them as
timestamped JSONL — one line per report. This is passive sniffing only:
we never write output reports to the device.

Requires: pip install hidapi
Usage:    python hid_capture.py [--vid 0x2B73] [--pid 0x0048] [--duration 30]

The tool auto-detects AlphaTheta/Pioneer DJ controllers if no VID/PID is given.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import hid
except ImportError:
    sys.exit(
        "hidapi not installed. Run:  pip install hidapi\n"
        "On Linux you may also need:  sudo apt install libhidapi-hidraw0"
    )


# AlphaTheta (formerly Pioneer DJ) USB vendor IDs
ALPHATHETA_VID = 0x2B73
PIONEER_DJ_VID = 0x08E4

KNOWN_VIDS = {
    ALPHATHETA_VID: "AlphaTheta",
    PIONEER_DJ_VID: "Pioneer DJ",
}

KNOWN_DEVICES = {
    (ALPHATHETA_VID, 0x0048): "Omnis Duo",
    (ALPHATHETA_VID, 0x003C): "DDJ-FLX10",
    (ALPHATHETA_VID, 0x0034): "DDJ-FLX6",
    (ALPHATHETA_VID, 0x003E): "DDJ-FLX4",
    (ALPHATHETA_VID, 0x0040): "DDJ-REV7",
    (PIONEER_DJ_VID, 0x0163): "DDJ-1000",
    (PIONEER_DJ_VID, 0x0162): "DDJ-800",
}

DEFAULT_OUTPUT_DIR = Path.home() / ".fablegear" / "live_bridge" / "hid_captures"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def find_controllers() -> list[dict[str, Any]]:
    """Enumerate all connected AlphaTheta/Pioneer DJ HID devices."""
    found = []
    for info in hid.enumerate():
        vid = info.get("vendor_id", 0)
        if vid in KNOWN_VIDS:
            found.append(info)
    return found


def device_label(vid: int, pid: int) -> str:
    name = KNOWN_DEVICES.get((vid, pid))
    vendor = KNOWN_VIDS.get(vid, f"0x{vid:04X}")
    if name:
        return f"{vendor} {name} (0x{vid:04X}:0x{pid:04X})"
    return f"{vendor} Unknown (0x{vid:04X}:0x{pid:04X})"


def print_device_table(devices: list[dict[str, Any]]) -> None:
    print(f"\n{'#':<4} {'Device':<40} {'Interface':<6} {'Usage Page':<12} {'Path'}")
    print("-" * 100)
    for i, d in enumerate(devices):
        label = device_label(d["vendor_id"], d["product_id"])
        iface = d.get("interface_number", "?")
        usage = f"0x{d.get('usage_page', 0):04X}"
        path = d.get("path", b"").decode("utf-8", errors="replace")
        print(f"{i:<4} {label:<40} {iface:<6} {usage:<12} {path}")
    print()


def classify_report(data: bytes, elapsed_ms: float) -> dict[str, Any]:
    """Extract structure hints from a raw HID input report."""
    header = data[0] if data else None
    payload = data[1:] if len(data) > 1 else b""
    nonzero_count = sum(1 for b in payload if b != 0)
    nonzero_tail = 0
    for b in reversed(payload):
        if b != 0:
            break
        nonzero_tail += 1
    active_length = len(payload) - nonzero_tail

    return {
        "header": header,
        "total_length": len(data),
        "payload_length": len(payload),
        "active_bytes": active_length,
        "nonzero_count": nonzero_count,
        "density": round(nonzero_count / max(len(payload), 1), 3),
    }


def capture_loop(
    device: hid.device,
    *,
    output_path: Path | None,
    duration: float | None,
    max_reports: int | None,
    verbose: bool,
    label: str | None,
) -> dict[str, Any]:
    """Read HID input reports until stopped. Returns session summary."""

    stop = False

    def handle_signal(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    handle = None
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        handle = output_path.open("w", encoding="utf-8")

    report_count = 0
    byte_count = 0
    header_counts: dict[int, int] = {}
    length_counts: dict[int, int] = {}
    start = time.monotonic()
    first_ts: str | None = None
    last_ts: str | None = None

    def write_line(obj: dict[str, Any]) -> None:
        line = json.dumps(obj, sort_keys=True)
        if handle:
            handle.write(line + "\n")
            handle.flush()
        if verbose:
            print(line, flush=True)

    write_line({
        "kind": "capture_started",
        "timestamp": utc_now_iso(),
        "label": label,
        "duration_limit": duration,
        "report_limit": max_reports,
    })

    try:
        while not stop:
            if duration and (time.monotonic() - start) >= duration:
                break
            if max_reports and report_count >= max_reports:
                break

            data = device.read(1024, timeout_ms=100)
            if not data:
                continue

            raw = bytes(data)
            now = utc_now_iso()
            if first_ts is None:
                first_ts = now
            last_ts = now
            elapsed_ms = (time.monotonic() - start) * 1000

            report_count += 1
            byte_count += len(raw)

            hdr = raw[0] if raw else 0
            header_counts[hdr] = header_counts.get(hdr, 0) + 1
            length_counts[len(raw)] = length_counts.get(len(raw), 0) + 1

            classification = classify_report(raw, elapsed_ms)

            record = {
                "kind": "hid_report",
                "seq": report_count,
                "timestamp": now,
                "elapsed_ms": round(elapsed_ms, 1),
                "length": len(raw),
                "hex": raw.hex(" "),
                "classification": classification,
            }

            if verbose or not handle:
                active = classification["active_bytes"]
                density = classification["density"]
                preview = raw[:16].hex(" ")
                print(
                    f"[{report_count:>6}] {elapsed_ms:>10.1f}ms  "
                    f"len={len(raw):<4} hdr=0x{hdr:02X}  "
                    f"active={active:<4} density={density:.2f}  "
                    f"{preview}...",
                    flush=True,
                )

            if handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                if report_count % 100 == 0:
                    handle.flush()

    finally:
        if handle:
            handle.flush()

    elapsed = time.monotonic() - start
    summary = {
        "kind": "capture_finished",
        "timestamp": utc_now_iso(),
        "label": label,
        "report_count": report_count,
        "byte_count": byte_count,
        "elapsed_seconds": round(elapsed, 2),
        "reports_per_second": round(report_count / max(elapsed, 0.001), 1),
        "first_report": first_ts,
        "last_report": last_ts,
        "header_distribution": {f"0x{k:02X}": v for k, v in sorted(header_counts.items())},
        "length_distribution": {str(k): v for k, v in sorted(length_counts.items())},
        "output_file": str(output_path) if output_path else None,
    }

    write_line(summary)
    if handle:
        handle.close()

    return summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Capture raw HID input reports from AlphaTheta/Pioneer DJ controllers."
    )
    p.add_argument("--vid", type=lambda x: int(x, 0), default=None,
                    help="USB Vendor ID (hex). Auto-detects if omitted.")
    p.add_argument("--pid", type=lambda x: int(x, 0), default=None,
                    help="USB Product ID (hex). Auto-detects if omitted.")
    p.add_argument("--interface", type=int, default=None,
                    help="HID interface number to open (for multi-interface devices).")
    p.add_argument("--duration", type=float, default=None,
                    help="Capture duration in seconds. Runs until Ctrl-C if omitted.")
    p.add_argument("--max-reports", type=int, default=None,
                    help="Stop after N reports.")
    p.add_argument("--output", type=Path, default=None,
                    help="JSONL output path. Auto-generates timestamped filename if omitted.")
    p.add_argument("--label", default=None,
                    help="Session label (e.g. 'jog_left_scratch', 'fader_sweep').")
    p.add_argument("--list", action="store_true",
                    help="List detected controllers and exit.")
    p.add_argument("--verbose", "-v", action="store_true",
                    help="Print every report to stdout (in addition to JSONL file).")
    p.add_argument("--stdout-only", action="store_true",
                    help="Print to stdout only, don't write a file.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    controllers = find_controllers()

    if args.list:
        if not controllers:
            print("No AlphaTheta/Pioneer DJ HID devices found.")
            print("Is the controller plugged in and powered on?")
            return 1
        print_device_table(controllers)
        return 0

    if args.vid and args.pid:
        target_vid, target_pid = args.vid, args.pid
    elif controllers:
        # prefer vendor-defined usage pages (0xFF00+) — that's the raw data pipe
        vendor_ifaces = [
            d for d in controllers
            if d.get("usage_page", 0) >= 0xFF00
        ]
        pick = vendor_ifaces[0] if vendor_ifaces else controllers[0]
        target_vid = pick["vendor_id"]
        target_pid = pick["product_id"]
        print(f"Auto-detected: {device_label(target_vid, target_pid)}")
        if len(controllers) > 1:
            print(f"  ({len(controllers)} devices found — use --vid/--pid to select)")
    else:
        print("No AlphaTheta/Pioneer DJ controllers detected.")
        print("Use --list to check, or specify --vid and --pid manually.")
        return 1

    # find the right HID path (prefer vendor-defined usage page interface)
    candidates = [
        d for d in hid.enumerate()
        if d["vendor_id"] == target_vid and d["product_id"] == target_pid
    ]
    if args.interface is not None:
        candidates = [d for d in candidates if d.get("interface_number") == args.interface]

    vendor_candidates = [d for d in candidates if d.get("usage_page", 0) >= 0xFF00]
    chosen = vendor_candidates[0] if vendor_candidates else (candidates[0] if candidates else None)

    if not chosen:
        print(f"Could not find HID interface for 0x{target_vid:04X}:0x{target_pid:04X}")
        return 1

    usage_page = chosen.get("usage_page", 0)
    iface_num = chosen.get("interface_number", "?")
    hid_path = chosen["path"]
    print(f"Opening interface {iface_num}, usage page 0x{usage_page:04X}")
    print(f"  HID path: {hid_path.decode('utf-8', errors='replace')}")

    device = hid.device()
    try:
        device.open_path(hid_path)
    except OSError as exc:
        print(f"Failed to open device: {exc}")
        print("On Linux, try:  sudo python hid_capture.py ...")
        print("Or add a udev rule for your controller.")
        return 1

    device.set_nonblocking(True)

    product = device.get_product_string() or "unknown"
    manufacturer = device.get_manufacturer_string() or "unknown"
    print(f"Connected: {manufacturer} — {product}")

    if args.stdout_only:
        output_path = None
    elif args.output:
        output_path = args.output
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = (args.label or "capture").replace(" ", "_")
        output_path = DEFAULT_OUTPUT_DIR / f"{ts}_{slug}.jsonl"

    if output_path:
        print(f"Writing to: {output_path}")
    print(f"Capturing... (Ctrl-C to stop)\n")

    summary = capture_loop(
        device,
        output_path=output_path,
        duration=args.duration,
        max_reports=args.max_reports,
        verbose=args.verbose,
        label=args.label,
    )

    device.close()

    print(f"\n--- Capture Summary ---")
    print(f"Reports:   {summary['report_count']}")
    print(f"Bytes:     {summary['byte_count']}")
    print(f"Duration:  {summary['elapsed_seconds']}s")
    print(f"Rate:      {summary['reports_per_second']} reports/sec")
    print(f"Headers:   {summary['header_distribution']}")
    print(f"Lengths:   {summary['length_distribution']}")
    if output_path:
        print(f"Saved to:  {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
