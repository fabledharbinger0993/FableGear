"""
Capture and summarize Pro DJ Link packets for CDJ control decoding.

This helper wraps tcpdump so an experiment is not lost to terminal scrollback.
It writes three files under scripts/live_bridge/captures/:
  - .pcap: raw capture
  - .txt: tcpdump -XX text dump
  - .csv: one-row-per-packet summary for quick diffing

It is observational only. It does not bind Pioneer ports or send packets.
tcpdump may prompt for sudo because macOS protects BPF capture devices.
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CAPTURE_DIR = Path(__file__).resolve().parent / "captures"
DEFAULT_FILTER = "udp and (port 50001 or port 50002)"
PACKET_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} [^ ]+) IP "
    r"(?P<src>[0-9.]+)\.(?P<src_port>\d+) > (?P<dst>[0-9.]+)\.(?P<dst_port>\d+): .* length (?P<length>\d+)"
)
HEX_RE = re.compile(r"^\s+0x[0-9a-f]+:\s+((?:[0-9a-f]{4}\s*)+)", re.IGNORECASE)


@dataclass
class PacketSummary:
    timestamp: str
    src: str
    src_port: int
    dst: str
    dst_port: int
    length: int
    packet_type: str
    hex_head: str
    ascii_head: str
    payload_hex_head: str
    payload_ascii_head: str


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True)


def authorize_sudo() -> None:
    print("macOS may ask for your password now so tcpdump can access en0.", flush=True)
    print("Type it into this terminal when you see 'Password:'; characters will not echo.", flush=True)
    subprocess.run(["sudo", "-v"], check=True)


def dump_pcap_to_text(pcap_path: Path, text_path: Path) -> None:
    with text_path.open("w", encoding="utf-8") as handle:
        subprocess.run(
            ["/usr/sbin/tcpdump", "-n", "-tttt", "-XX", "-r", str(pcap_path)],
            check=True,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
        )


def parse_text_dump(text_path: Path) -> list[PacketSummary]:
    packets: list[PacketSummary] = []
    current: dict[str, object] | None = None
    hex_chunks: list[str] = []

    def flush() -> None:
        nonlocal current, hex_chunks
        if current is None:
            return
        hex_bytes = "".join(hex_chunks)
        byte_values = bytes.fromhex(hex_bytes) if hex_bytes else b""
        payload = udp_payload(byte_values)
        ascii_head = render_ascii(byte_values[:64])
        payload_ascii_head = render_ascii(payload[:64])
        packet_type = f"0x{payload[10]:02x}" if len(payload) > 10 else ""
        packets.append(
            PacketSummary(
                timestamp=str(current["timestamp"]),
                src=str(current["src"]),
                src_port=int(current["src_port"]),
                dst=str(current["dst"]),
                dst_port=int(current["dst_port"]),
                length=int(current["length"]),
                packet_type=packet_type,
                hex_head=byte_values[:64].hex(),
                ascii_head=ascii_head,
                payload_hex_head=payload[:64].hex(),
                payload_ascii_head=payload_ascii_head,
            )
        )
        current = None
        hex_chunks = []

    for line in text_path.read_text(encoding="utf-8", errors="replace").splitlines():
        packet_match = PACKET_RE.match(line)
        if packet_match:
            flush()
            current = packet_match.groupdict()
            continue
        hex_match = HEX_RE.match(line)
        if hex_match and current is not None:
            hex_chunks.append(hex_match.group(1).replace(" ", ""))

    flush()
    return packets


def udp_payload(frame: bytes) -> bytes:
    """Return UDP payload from a tcpdump -XX Ethernet/IPv4 frame."""
    ethernet_header_len = 14
    if len(frame) < ethernet_header_len + 20:
        return b""
    ip_start = ethernet_header_len
    version_ihl = frame[ip_start]
    version = version_ihl >> 4
    if version != 4:
        return b""
    ip_header_len = (version_ihl & 0x0F) * 4
    udp_start = ip_start + ip_header_len
    payload_start = udp_start + 8
    if len(frame) < payload_start:
        return b""
    return frame[payload_start:]


def render_ascii(data: bytes) -> str:
    return "".join(chr(byte) if 32 <= byte <= 126 else "." for byte in data)


def write_csv(packets: list[PacketSummary], csv_path: Path) -> None:
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "timestamp",
                "src",
                "src_port",
                "dst",
                "dst_port",
                "length",
                "packet_type",
                "hex_head",
                "ascii_head",
                "payload_hex_head",
                "payload_ascii_head",
            ),
        )
        writer.writeheader()
        for packet in packets:
            writer.writerow(packet.__dict__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture and summarize CDJ Pro DJ Link packets.")
    parser.add_argument("--interface", default="en0", help="Network interface connected to CDJ.")
    parser.add_argument("--count", type=int, default=180, help="Packet count to capture.")
    parser.add_argument("--label", default="controls", help="Filename label for this capture.")
    parser.add_argument("--filter", default=DEFAULT_FILTER, help="tcpdump capture filter.")
    parser.add_argument("--from-pcap", type=Path, help="Parse an existing pcap instead of starting a new capture.")
    args = parser.parse_args()

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = CAPTURE_DIR / f"{stamp}_{args.label}"
    pcap_path = base.with_suffix(".pcap")
    text_path = base.with_suffix(".txt")
    csv_path = base.with_suffix(".csv")

    if args.from_pcap:
        pcap_path = args.from_pcap
        base = pcap_path.with_suffix("")
        text_path = base.with_suffix(".txt")
        csv_path = base.with_suffix(".csv")
    else:
        authorize_sudo()
        print("Start CDJ control sequence while capture runs.")
        run([
            "sudo",
            "-n",
            "/usr/sbin/tcpdump",
            "-i",
            args.interface,
            "-n",
            "-w",
            str(pcap_path),
            "-c",
            str(args.count),
            args.filter,
        ])
    dump_pcap_to_text(pcap_path, text_path)
    packets = parse_text_dump(text_path)
    write_csv(packets, csv_path)

    print(f"pcap: {pcap_path}")
    print(f"text: {text_path}")
    print(f"csv : {csv_path}")
    print(f"packets summarized: {len(packets)}")


if __name__ == "__main__":
    main()