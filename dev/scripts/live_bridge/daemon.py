"""Passive FableGear live-link daemon.

The daemon listens, records, and summarizes CDJ/Rekordbox link traffic. It does
not impersonate Rekordbox, answer CDJs, or write Rekordbox databases.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import socket
import sys
from pathlib import Path
from typing import Any

try:
    from .protocol import PROLINK_PORTS, build_record
    from .state import LiveBridgeState
except ImportError:  # pragma: no cover - direct script execution fallback
    from protocol import PROLINK_PORTS, build_record
    from state import LiveBridgeState


DEFAULT_RUNTIME_DIR = Path.home() / ".fablegear" / "live_bridge"


class JsonlWriter:
    def __init__(self, path: Path | None):
        self.path = path
        self._handle = None

    def __enter__(self):
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a", encoding="utf-8")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handle is not None:
            self._handle.close()

    def write(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, sort_keys=True)
        if self._handle is not None:
            self._handle.write(line + "\n")
            self._handle.flush()
        else:
            print(line, flush=True)


class LiveBridgeProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        *,
        state: LiveBridgeState,
        writer: JsonlWriter,
        label: str | None,
        destination: str,
        destination_port: int,
    ):
        self.state = state
        self.writer = writer
        self.label = label
        self.destination = destination
        self.destination_port = destination_port

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[override]
        source = str(addr[0]) if addr else "unknown"
        source_port = int(addr[1]) if addr and len(addr) > 1 else None
        record = build_record(
            data,
            source=source,
            source_port=source_port,
            destination=self.destination,
            destination_port=self.destination_port,
            label=self.label,
        )
        event = self.state.observe(record)
        self.writer.write({"kind": "packet", "packet": record.as_dict(), "event": event})

    def error_received(self, exc: Exception) -> None:  # type: ignore[override]
        self.writer.write({"kind": "socket_error", "error": str(exc)})


def _make_socket(port: int, bind: str) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except OSError:
        pass
    sock.setblocking(False)
    sock.bind((bind, port))
    return sock


async def _write_snapshots(state: LiveBridgeState, path: Path, interval: float, stop: asyncio.Event) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    while not stop.is_set():
        path.write_text(json.dumps(state.snapshot(), indent=2, sort_keys=True), encoding="utf-8")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
    path.write_text(json.dumps(state.snapshot(), indent=2, sort_keys=True), encoding="utf-8")


async def run_daemon(args: argparse.Namespace) -> int:
    ports = tuple(args.ports or PROLINK_PORTS)
    state = LiveBridgeState()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    transports: list[asyncio.DatagramTransport] = []
    with JsonlWriter(args.jsonl) as writer:
        writer.write({
            "kind": "daemon_started",
            "mode": "passive_udp",
            "bind": args.bind,
            "ports": list(ports),
            "safety": "passive_only_no_spoofing_no_db_writes",
        })

        for port in ports:
            try:
                sock = _make_socket(port, args.bind)
            except OSError as exc:
                writer.write({
                    "kind": "bind_failed",
                    "port": port,
                    "error": str(exc),
                    "hint": "If Rekordbox owns this port, use tcpdump capture mode/offline parsing instead of UDP binding.",
                })
                continue
            bound_host, bound_port = sock.getsockname()[:2]
            transport, _ = await loop.create_datagram_endpoint(
                lambda bound_host=bound_host, bound_port=bound_port: LiveBridgeProtocol(
                    state=state,
                    writer=writer,
                    label=args.label,
                    destination=str(bound_host),
                    destination_port=int(bound_port),
                ),
                sock=sock,
            )
            transports.append(transport)
            writer.write({"kind": "listening", "port": int(bound_port), "bind": str(bound_host)})

        if not transports:
            writer.write({"kind": "daemon_stopped", "reason": "no_ports_bound"})
            return 2

        snapshot_task = asyncio.create_task(_write_snapshots(state, args.state_file, args.snapshot_interval, stop))
        await stop.wait()
        snapshot_task.cancel()
        try:
            await snapshot_task
        except asyncio.CancelledError:
            args.state_file.write_text(json.dumps(state.snapshot(), indent=2, sort_keys=True), encoding="utf-8")

        for transport in transports:
            transport.close()

        writer.write({"kind": "daemon_stopped", "snapshot": state.snapshot()})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the passive FableGear CDJ/Rekordbox live-link daemon.")
    parser.add_argument("--bind", default="0.0.0.0", help="UDP bind address for passive listeners.")
    parser.add_argument("--port", dest="ports", action="append", type=int, help="Port to listen on; repeat for multiple ports.")
    parser.add_argument("--label", help="Optional label attached to observed packets in this session.")
    parser.add_argument(
        "--jsonl",
        type=Path,
        default=DEFAULT_RUNTIME_DIR / "events.jsonl",
        help="JSONL event output path. Use '-' to print to stdout only.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_RUNTIME_DIR / "state.json",
        help="Latest state snapshot path.",
    )
    parser.add_argument("--snapshot-interval", type=float, default=2.0, help="Seconds between state snapshot writes.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if str(args.jsonl) == "-":
        args.jsonl = None
    if args.snapshot_interval <= 0:
        parser.error("--snapshot-interval must be greater than zero")
    return asyncio.run(run_daemon(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
