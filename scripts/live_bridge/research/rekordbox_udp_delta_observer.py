"""
Observe Rekordbox Pro DJ Link UDP socket counters while CDJ controls are used.

This is intentionally non-invasive: it does not bind Pioneer ports and does not
need packet capture privileges. It samples macOS netstat counters for Rekordbox
and reports byte deltas per known Pro DJ Link port.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import time
from datetime import datetime

PORTS = (50000, 50001, 50002, 50004, 50111, 2049, 33531)
NETSTAT_RE = re.compile(r"\.([0-9]+)\s+\*\.\*\s+(\d+)\s+(\d+).*rekordbox:(\d+)")


def sample() -> dict[int, tuple[int, int]]:
    output = subprocess.check_output(["netstat", "-anv"], text=True, errors="replace")
    counters: dict[int, tuple[int, int]] = {}
    for line in output.splitlines():
        match = NETSTAT_RE.search(line)
        if not match:
            continue
        port = int(match.group(1))
        if port in PORTS:
            counters[port] = (int(match.group(2)), int(match.group(3)))
    return counters


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch Rekordbox Pro DJ Link UDP byte deltas.")
    parser.add_argument("--seconds", type=float, default=18.0, help="Observation duration.")
    parser.add_argument("--interval", type=float, default=0.75, help="Sampling interval.")
    parser.add_argument("--spike", type=int, default=4500, help="recv_delta threshold flagged as a likely control burst.")
    args = parser.parse_args()

    previous = sample()
    deadline = time.monotonic() + args.seconds

    print("Watching Rekordbox UDP deltas. Move CDJ controls now.")
    print("time             port      recv_delta  send_delta   recv_total   send_total  note")
    while time.monotonic() < deadline:
        time.sleep(args.interval)
        current = sample()
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        for port in PORTS:
            old_recv, old_send = previous.get(port, (0, 0))
            new_recv, new_send = current.get(port, (0, 0))
            delta_recv = new_recv - old_recv
            delta_send = new_send - old_send
            if delta_recv or delta_send:
                note = "SPIKE" if delta_recv >= args.spike or delta_send >= args.spike else ""
                print(
                    f"{timestamp:<16} {port:<9} {delta_recv:>10} {delta_send:>10} "
                    f"{new_recv:>12} {new_send:>11}  {note}",
                    flush=True,
                )
        previous = current


if __name__ == "__main__":
    main()