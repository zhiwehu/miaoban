"""Xiaozhi v3 AES-CTR datagrams. Legacy wire format has no authentication tag."""
import struct
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

HEADER = struct.Struct('!BBHIII')
MAX_OPUS = 2048


class UdpAudio:
    def __init__(self, key, ssrc, *, server=True):
        if len(key) != 16 or not 0 <= ssrc <= 0xffffffff:
            raise ValueError('invalid_udp_key_or_session')
        self.key, self.ssrc = key, ssrc
        self.send_flags = int(server)
        self.rx_sequence = self.tx_sequence = self.lost = 0

    @property
    def nonce(self):
        return HEADER.pack(1, 0, 0, self.ssrc, 0, 0)

    def _crypt(self, header, payload):
        cipher = Cipher(algorithms.AES(self.key), modes.CTR(header)).encryptor()
        return cipher.update(payload) + cipher.finalize()

    def decode(self, packet):
        if len(packet) < HEADER.size:
            raise ValueError('short_udp_packet')
        kind, flags, size, ssrc, timestamp, sequence = HEADER.unpack(packet[:16])
        if (kind, flags, ssrc) != (1, 1-self.send_flags, self.ssrc) or not 0 < size <= MAX_OPUS or len(packet) != size+16:
            raise ValueError('invalid_udp_packet')
        if sequence <= self.rx_sequence:
            raise ValueError('stale_udp_packet')
        payload = self._crypt(packet[:16], packet[16:])
        self.lost += sequence-self.rx_sequence-1
        self.rx_sequence = sequence
        return payload, timestamp

    def encode(self, payload, timestamp=0):
        if not 0 < len(payload) <= MAX_OPUS or self.tx_sequence == 0xffffffff:
            raise ValueError('udp_packet_or_sequence_limit')
        self.tx_sequence += 1
        # Different flags prevent CTR keystream reuse between client and server.
        header = HEADER.pack(1, self.send_flags, len(payload), self.ssrc, timestamp, self.tx_sequence)
        return header + self._crypt(header, payload)
