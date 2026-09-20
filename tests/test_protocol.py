import unittest
from robot_link.protocol import Flags, MessageType, Packet, PacketParser, crc16_xmodem

class ProtocolTest(unittest.TestCase):
    def test_crc(self): self.assertEqual(crc16_xmodem(b"123456789"),0x31c3)
    def test_fragmentation(self):
        packet=Packet(MessageType.PLAY_SOUND,b'{"name":"alert.wav"}',Flags.ACK_REQUIRED,9)
        frame=packet.encode()
        for split in range(len(frame)+1):
            parser=PacketParser(); self.assertEqual(parser.feed(frame[:split])+parser.feed(frame[split:]),[packet])
    def test_corruption_recovery(self):
        bad=bytearray(Packet(MessageType.PING,b"bad").encode()); bad[-3]^=1
        good=Packet(MessageType.PONG,b"good"); parser=PacketParser()
        self.assertEqual(parser.feed(bytes(bad)+good.encode()),[good]); self.assertEqual(parser.crc_errors,1)
if __name__=="__main__": unittest.main()

