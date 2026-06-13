"""
fablegear / scripts / live_bridge / sandbox_prolink_spoofer.py

PATH B: ACTIVE SPOOFER ("Device 5")
This script actively injects itself into the Pioneer Pro DJ Link network.
It broadcasts keep-alives announcing itself as a Rekordbox instance (ID 5).
WARNING: Do not run this on a live gig network without testing against a
single CDJ first. If the keep-alive loop stalls, the switch may fault.

Player IDs:
  1-4 : Hardware CDJs / XDJ
  5-6 : Rekordbox (Desktop instances)
"""

import asyncio
import logging
import struct
from binascii import unhexlify

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ProlinkSpoofer")

PROLINK_DISCOVERY_PORT = 50000
PIONEER_MAGIC = b'Mac '

class ProLinkActiveProtocol(asyncio.DatagramProtocol):
    def __init__(self, device_name: str, device_id: int):
        self.device_name = device_name.encode('utf-8').ljust(20, b'\x00')
        self.device_id = device_id
        self.transport = None
        self.mac_address = unhexlify("010203040506") # Mock MAC

    def connection_made(self, transport):
        self.transport = transport
        # Enable UDP Broadcasts
        sock = transport.get_extra_info('socket')
        if sock:
            import socket
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        log.info("Active Spoofer ready. Emulating Device ID %d.", self.device_id)

    def build_keep_alive(self) -> bytes:
        """
        Builds the 0x06 (Device Announce) Pioneer Keep-Alive Packet.
        This tells the CDJs 'I am a Rekordbox instance, keep me in the link'.
        """
        header = PIONEER_MAGIC + self.mac_address
        packet_type = b'\x06' # Keep Alive
        packet_length = struct.pack(">H", 54) # Typical length
        
        # 0x01 (Rekordbox type identifier), 0x02 (Sub-identifier), assigned ID
        device_sig = b'\x01\x02' + struct.pack("B", self.device_id)
        
        # Combine structural bytes (simplified POC)
        packet = header + packet_type + b'\x00\x00' + packet_length + device_sig + b'\x00'*4 + self.device_name
        return packet.ljust(54, b'\x00')

    async def broadcast_loop(self):
        """
        Pioneer devices expect a keep-alive every 1.5 seconds.
        If we miss 3 beats (4.5s), we get dropped from the hardware UI.
        """
        while self.transport is not None:
            packet = self.build_keep_alive()
            # Broadcast to the subnet
            self.transport.sendto(packet, ('255.255.255.255', PROLINK_DISCOVERY_PORT))
            log.debug("Broadcasted keep-alive for Device %d", self.device_id)
            await asyncio.sleep(1.5)

async def main():
    log.warning("Starting FableGear Pro DJ Link ACTIVE Spoofer (Spoofing ID 5)...")
    loop = asyncio.get_running_loop()

    protocol = ProLinkActiveProtocol(device_name="FableGear Live", device_id=5)
    
    transport, prot = await loop.create_datagram_endpoint(
        lambda: protocol,
        local_addr=('0.0.0.0', 0), # Ephemeral port for sending
        allow_broadcast=True
    )

    try:
        # Start the heartbeat
        await protocol.broadcast_loop()
    except asyncio.CancelledError:
        log.info("Shutting down spoofer.")
    finally:
        transport.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
