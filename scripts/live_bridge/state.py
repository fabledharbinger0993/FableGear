"""State accumulator for passive CDJ/Rekordbox live-link packets."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from typing import Any

try:
    from .protocol import PacketRecord
except ImportError:  # pragma: no cover - direct script execution fallback
    from protocol import PacketRecord


@dataclass(slots=True)
class DeviceState:
    address: str
    packet_count: int = 0
    last_seen: str | None = None
    ports: dict[str, int] = field(default_factory=dict)
    signatures: dict[str, int] = field(default_factory=dict)
    device_id_hints: dict[str, int] = field(default_factory=dict)
    families: dict[str, int] = field(default_factory=dict)

    def observe(self, packet: PacketRecord) -> None:
        self.packet_count += 1
        self.last_seen = packet.timestamp
        if packet.destination_port is not None:
            key = str(packet.destination_port)
            self.ports[key] = self.ports.get(key, 0) + 1
        for signature in packet.signatures:
            self.signatures[signature] = self.signatures.get(signature, 0) + 1
        if packet.device_id_hint is not None:
            key = str(packet.device_id_hint)
            self.device_id_hints[key] = self.device_id_hints.get(key, 0) + 1
        self.families[packet.packet_family] = self.families.get(packet.packet_family, 0) + 1


@dataclass(slots=True)
class LiveBridgeState:
    packet_count: int = 0
    last_seen: str | None = None
    devices: dict[str, DeviceState] = field(default_factory=dict)
    families: Counter[str] = field(default_factory=Counter)
    ports: Counter[str] = field(default_factory=Counter)
    signatures: Counter[str] = field(default_factory=Counter)
    recent_packets: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=40))

    def observe(self, packet: PacketRecord) -> dict[str, Any]:
        self.packet_count += 1
        self.last_seen = packet.timestamp
        self.families[packet.packet_family] += 1
        if packet.destination_port is not None:
            self.ports[str(packet.destination_port)] += 1
        for signature in packet.signatures:
            self.signatures[signature] += 1

        device = self.devices.get(packet.source)
        if device is None:
            device = DeviceState(address=packet.source)
            self.devices[packet.source] = device
        device.observe(packet)

        event = {
            "event": "packet",
            "timestamp": packet.timestamp,
            "source": packet.source,
            "destination": packet.destination,
            "destination_port": packet.destination_port,
            "length": packet.length,
            "packet_family": packet.packet_family,
            "packet_type": packet.packet_type,
            "device_id_hint": packet.device_id_hint,
            "signatures": packet.signatures,
            "label": packet.label,
            "notes": packet.notes,
        }
        self.recent_packets.append(event)
        return event

    def snapshot(self) -> dict[str, Any]:
        return {
            "packet_count": self.packet_count,
            "last_seen": self.last_seen,
            "ports": dict(self.ports),
            "families": dict(self.families),
            "signatures": dict(self.signatures),
            "devices": {
                address: asdict(device)
                for address, device in sorted(self.devices.items())
            },
            "recent_packets": list(self.recent_packets),
        }
