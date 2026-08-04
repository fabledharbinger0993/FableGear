"""
fablegear / scripts / live_bridge / sandbox_prolink_sniffer.py

PATH A: PASSIVE SNIFFER (Reconnaissance)
This script passively listens to the Pioneer Pro DJ Link broadcast ports
without sending any traffic. It is 100% safe to run on a live CDJ network.
Use this to decode the hex structures of current proprietary Pioneer firmware.

Ports:
  50000 (UDP) - Device Discovery / Keep-Alive
  50001 (UDP) - Player Status / Beat Grid / BPM
  50002 (UDP/TCP) - Metadata Exchange
"""

import asyncio
import logging
import socket
import time
from binascii import hexlify

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ProlinkSniffer")

PROLINK_DISCOVERY_PORT = 50000
PROLINK_STATUS_PORT = 50001
PROLINK_METADATA_PORT = 50002

# Known Pro DJ Link packet signatures seen across Rekordbox/CDJ generations.
# CDJ-3000 traffic may not start with the older "Mac " prefix, so we log probes
# instead of silently dropping non-matches during reconnaissance.
KNOWN_SIGNATURES = (
    b"Mac ",
    b"Qspt1WmJOL",
)

class ProLinkProtocol(asyncio.DatagramProtocol):
    def __init__(self, port_name: str, *, log_everything: bool = False):
        self.port_name = port_name
        self.log_everything = log_everything
        self.packet_count = 0
        self.last_probe_log = 0.0

    def connection_made(self, transport):
        self.transport = transport
        sockname = transport.get_extra_info('sockname')
        log.info("Passive Sniffer bound to %s at %s", self.port_name, sockname)

    def datagram_received(self, data, addr):
        self.packet_count += 1
        packet_type = data[0x0A] if len(data) > 0x0A else 0x00
        packet_len = len(data)
        hex_head = hexlify(data[:48]).decode()
        ascii_head = "".join(chr(byte) if 32 <= byte <= 126 else "." for byte in data[:48])
        has_known_signature = any(signature in data[:32] for signature in KNOWN_SIGNATURES)

        if has_known_signature or self.log_everything:
            log.info(
                "[%s] #%d from %s | Type: 0x%02x | Size: %d | Hex: %s | ASCII: %s",
                self.port_name,
                self.packet_count,
                addr,
                packet_type,
                packet_len,
                hex_head,
                ascii_head,
            )
            return

        now = time.monotonic()
        if now - self.last_probe_log >= 5.0:
            self.last_probe_log = now
            log.info(
                "[%s] traffic present but unknown signature | latest from %s | Size: %d | Hex: %s",
                self.port_name,
                addr,
                packet_len,
                hex_head,
            )

async def main():
    log.info("Starting FableGear Pro DJ Link Passive Sniffer...")
    log.info("Host interfaces: %s", _interface_summary())
    loop = asyncio.get_running_loop()
    transport_disc = None
    transport_stat = None
    transport_meta = None

    # We use reuse_port=True so it doesn't block Rekordbox if it happens to be running locally,
    # allowing side-by-side passive inspection.
    try:
        transport_disc, _ = await loop.create_datagram_endpoint(
            lambda: ProLinkProtocol("DISCOVERY_50000", log_everything=True),
            local_addr=('0.0.0.0', PROLINK_DISCOVERY_PORT),
            reuse_port=True
        )

        transport_stat, _ = await loop.create_datagram_endpoint(
            lambda: ProLinkProtocol("STATUS_50001", log_everything=True),
            local_addr=('0.0.0.0', PROLINK_STATUS_PORT),
            reuse_port=True
        )

        transport_meta, _ = await loop.create_datagram_endpoint(
            lambda: ProLinkProtocol("METADATA_50002", log_everything=True),
            local_addr=('0.0.0.0', PROLINK_METADATA_PORT),
            reuse_port=True
        )

        log.info("Sniffing active. Press Ctrl+C to stop.")
        while True:
            await asyncio.sleep(3600)

    except asyncio.CancelledError:
        log.info("Shutting down sniffer.")
    finally:
        if transport_disc is not None:
            transport_disc.close()
        if transport_stat is not None:
            transport_stat.close()
        if transport_meta is not None:
            transport_meta.close()


def _interface_summary() -> str:
    host_name = socket.gethostname()
    summaries = []
    try:
        for info in socket.getaddrinfo(host_name, None, family=socket.AF_INET):
            address = info[4][0]
            if address not in summaries:
                summaries.append(address)
    except socket.gaierror:
        return "unavailable"
    return ", ".join(summaries) or "none"

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
