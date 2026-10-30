import struct
import unittest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from voicehub.mqtt_udp import UdpAudio


class UdpAudioTests(unittest.TestCase):
    def packet(self, sequence=1, payload=b'known opus payload', flags=0, length=None):
        header = struct.pack('!BBHIII', 1, flags, len(payload) if length is None else length,
                             0x12345678, 120, sequence)
        cipher = Cipher(algorithms.AES(bytes(range(16))), modes.CTR(header)).encryptor()
        return header + cipher.update(payload) + cipher.finalize()

    def codec(self):
        return UdpAudio(bytes(range(16)), 0x12345678)

    def test_device_packet_decrypts_and_carries_timestamp(self):
        self.assertEqual(self.codec().decode(self.packet()), (b'known opus payload', 120))

    def test_malformed_lengths_wrong_session_and_direction_are_rejected(self):
        for packet in [b'bad', self.packet(length=1), self.packet(flags=1),
                       self.packet(payload=b'x'*2049), self.packet()[:4]+b'xxxx'+self.packet()[8:]]:
            with self.subTest(packet_size=len(packet)), self.assertRaises(ValueError):
                self.codec().decode(packet)

    def test_duplicates_and_reordering_are_rejected_but_loss_can_recover(self):
        codec = self.codec()
        codec.decode(self.packet(1))
        for sequence in [1, 0]:
            with self.assertRaises(ValueError):codec.decode(self.packet(sequence))
        codec.decode(self.packet(3))
        self.assertEqual(codec.lost, 1)
        with self.assertRaises(ValueError):codec.decode(self.packet(2))
        self.assertEqual(codec.decode(self.packet(4))[0], b'known opus payload')

    def test_server_uses_distinct_direction_nonce_and_enforces_sequence_limit(self):
        codec = self.codec()
        packet = codec.encode(b'hello', timestamp=120)
        self.assertEqual(struct.unpack('!BBHIII',packet[:16]),(1,1,5,0x12345678,120,1))
        decryptor = Cipher(algorithms.AES(bytes(range(16))),modes.CTR(packet[:16])).decryptor()
        self.assertEqual(decryptor.update(packet[16:])+decryptor.finalize(), b'hello')
        self.assertNotEqual(packet[16:],self.packet(payload=b'hello')[16:])
        codec.tx_sequence = 0xffffffff
        with self.assertRaises(ValueError):codec.encode(b'x')

    def test_client_and_server_directions_interoperate(self):
        server=self.codec()
        client=UdpAudio(bytes(range(16)),0x12345678,server=False)
        self.assertEqual(server.decode(client.encode(b'up',120)),(b'up',120))
        self.assertEqual(client.decode(server.encode(b'down',180)),(b'down',180))
