import asyncio, json, tempfile, unittest
from robot_link.protocol import Flags, MessageType, Packet
from robot_link.services import PiServices

class FakeSession:
    def __init__(self): self.sent=[]
    async def send(self,p): self.sent.append(p)

class ServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.services=PiServices(True,self.tmp.name,"default","",0)
        await self.services.start(); self.session=FakeSession()
    async def asyncTearDown(self): await self.services.close(); self.tmp.cleanup()
    async def test_shutdown_ack(self):
        p=Packet(MessageType.SHUTDOWN_REQUEST,json.dumps({"delay":0,"reason":"test"}).encode(),Flags.ACK_REQUIRED,4)
        await self.services.handle(p,self.session); self.assertEqual(self.session.sent[0].message_type,MessageType.ACK)
        self.assertEqual(self.session.sent[0].sequence,4)
    async def test_sound_path_rejected(self):
        p=Packet(MessageType.PLAY_SOUND,b'{"name":"../bad.wav"}',Flags.ACK_REQUIRED,5)
        await self.services.handle(p,self.session)
        self.assertEqual(self.session.sent[0].message_type,MessageType.NACK)
if __name__=="__main__": unittest.main()
