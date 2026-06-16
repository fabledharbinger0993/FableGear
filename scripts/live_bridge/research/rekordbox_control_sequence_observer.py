"""
Run a labeled CDJ control observation sequence against Rekordbox UDP counters.

This is read-only and non-invasive. It does not bind Pioneer ports, send packets,
or require packet-capture privileges. The operator follows timed prompts on the
CDJ while this script records per-port byte deltas around each action window.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime

PORTS = (50000, 50001, 50002, 50004, 50111, 2049, 33531)
NETSTAT_RE = re.compile(r"\.([0-9]+)\s+\*\.\*\s+(\d+)\s+(\d+).*rekordbox:(\d+)")
DEFAULT_ACTIONS = ("play", "pause", "cue", "skip", "repeat 8 count", "set A return point")


@dataclass(frozen=True)
class Counter:
    recv: int = 0
    send: int = 0


def sample() -> dict[int, Counter]:
    output = subprocess.check_output(["netstat", "-anv"], text=True, errors="replace")
    counters: dict[int, Counter] = {}
    for line in output.splitlines():
        match = NETSTAT_RE.search(line)
        if not match:
            continue
        port = int(match.group(1))
        if port in PORTS:
            counters[port] = Counter(recv=int(match.group(2)), send=int(match.group(3)))
    return counters


def diff(before: dict[int, Counter], after: dict[int, Counter]) -> dict[int, Counter]:
    deltas: dict[int, Counter] = {}
    for port in PORTS:
        old = before.get(port, Counter())
        new = after.get(port, Counter())
        delta = Counter(recv=new.recv - old.recv, send=new.send - old.send)
        if delta.recv or delta.send:
            deltas[port] = delta
    return deltas


def observe_window(seconds: float, interval: float) -> tuple[dict[int, Counter], dict[int, Counter]]:
    before = sample()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(interval)
    return before, sample()


def print_deltas(label: str, deltas: dict[int, Counter]) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"\n[{timestamp}] {label}")
    print("port      recv_delta  send_delta")
    if not deltas:
        print("none               0          0")
        return
    for port in PORTS:
        delta = deltas.get(port)
        if delta is None:
            continue
        print(f"{port:<9} {delta.recv:>10} {delta.send:>10}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Observe labeled Rekordbox/CDJ control bursts.")
    parser.add_argument("--prep", type=float, default=2.0, help="Countdown seconds before each action.")
    parser.add_argument("--window", type=float, default=3.0, help="Observation seconds after each action prompt.")
    parser.add_argument("--interval", type=float, default=0.25, help="Sleep interval during each action window.")
    parser.add_argument(
        "--actions",
        nargs="*",
        default=DEFAULT_ACTIONS,
        help="Ordered labels to prompt and observe.",
    )
    args = parser.parse_args()

    print("FableGear CDJ control sequence observer")
    print("Follow each NOW prompt on the CDJ. This script only reads netstat counters.")
    print("sequence:", " -> ".join(args.actions))

    baseline = sample()
    if not baseline:
        print("No Rekordbox UDP counters found. Start Rekordbox and connect the CDJ first.")
        return

    for index, action in enumerate(args.actions, start=1):
        print(f"\nPrepare {index}/{len(args.actions)}: {action}")
        time.sleep(args.prep)
        print(f"NOW: {action}", flush=True)
        before, after = observe_window(args.window, args.interval)
        print_deltas(action, diff(before, after))

    print("\nSequence complete.")


if __name__ == "__main__":
    main()