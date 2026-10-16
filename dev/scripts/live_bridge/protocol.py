"""Protocol primitives for passive CDJ/Rekordbox link observation.

This module intentionally avoids active protocol behavior. It gives the daemon
stable packet records and conservative hints that can be refined as labeled
captures teach us more about the CDJ-3000/Rekordbox traffic.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

PROLINK_PORTS: tuple[int, ...] = (50000, 50001, 50002, 50004, 50111, 2049, 33531)
KNOWN_SIGNATURES: tuple[bytes, ...] = (b"Mac ", b"Qspt1WmJOL")

_PRINTABLE = set(range(32, 127))
_HEX_BYTE_RE = re.compile(r"\b[0-9a-fA-F]{2}\b")


@dataclass(slots=True)
class PacketRecord:
    """One observed packet with raw bytes and conservative decoder hints."""

    timestamp: str
    source: str
    source_port: int | None
    destination: str
    destination_port: int | None
    length: int
    payload_hex: str
    payload_ascii: str
    signatures: list[str] = field(default_factory=list)
    packet_family: str = "unknown"
    packet_type: int | None = None
    device_id_hint: int | None = None
    label: str | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def bytes_to_ascii(payload: bytes, *, limit: int = 160) -> str:
    """Return printable ASCII with dots for control bytes."""
    shown = payload[:limit]
    return "".join(chr(byte) if byte in _PRINTABLE else "." for byte in shown)


def signatures_in(payload: bytes) -> list[str]:
    found: list[str] = []
    for signature in KNOWN_SIGNATURES:
        if signature in payload:
            found.append(signature.decode("ascii", errors="replace"))
    return found


def classify_packet(payload: bytes, destination_port: int | None) -> tuple[str, int | None, int | None, list[str]]:
    """Return packet family, packet type hint, device id hint, and notes.

    The offsets are intentionally labeled as hints. They are useful for grouping
    and diffing captures, not yet as authoritative Pro DJ Link semantics.
    """
    notes: list[str] = []
    packet_type = payload[0] if payload else None
    device_id = None
    family = "unknown"

    found = signatures_in(payload)
    if "Qspt1WmJOL" in found:
        family = "rekordbox_or_coldplay_link"
        notes.append("contains observed CDJ/Rekordbox signature Qspt1WmJOL")
    elif "Mac " in found:
        family = "pro_dj_link_mac_announce"
        notes.append("contains Mac signature")
    elif destination_port in (50000, 50001, 50002):
        family = "pro_dj_link_candidate"

    if destination_port == 50000:
        notes.append("port 50000 candidate discovery/status traffic")
    elif destination_port == 50001:
        notes.append("port 50001 candidate beat/status traffic")
    elif destination_port == 50002:
        notes.append("port 50002 candidate control/status traffic")

    for offset in (0x21, 0x24, 0x25, 0x2B, 0x2C):
        if len(payload) > offset:
            value = payload[offset]
            if 1 <= value <= 8:
                device_id = value
                notes.append(f"possible device id {value} at offset 0x{offset:02x}")
                break

    return family, packet_type, device_id, notes


def build_record(
    payload: bytes,
    *,
    source: str,
    destination: str,
    source_port: int | None = None,
    destination_port: int | None = None,
    timestamp: str | None = None,
    label: str | None = None,
) -> PacketRecord:
    family, packet_type, device_id, notes = classify_packet(payload, destination_port)
    return PacketRecord(
        timestamp=timestamp or utc_now_iso(),
        source=source,
        source_port=source_port,
        destination=destination,
        destination_port=destination_port,
        length=len(payload),
        payload_hex=payload.hex(" "),
        payload_ascii=bytes_to_ascii(payload),
        signatures=signatures_in(payload),
        packet_family=family,
        packet_type=packet_type,
        device_id_hint=device_id,
        label=label,
        notes=notes,
    )


def bytes_from_hex_text(text: str) -> bytes:
    """Extract bytes from tcpdump-style hex text."""
    return bytes(int(match.group(0), 16) for match in _HEX_BYTE_RE.finditer(text))
