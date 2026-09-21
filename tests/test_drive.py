import asyncio, json, math, os, tempfile, time, unittest
from types import SimpleNamespace
from robot_link.bone_daemon import BoneDaemon
from robot_link.daemon import PiLink
from robot_link.drive import parse_drive
from robot_link.protocol import Flags, MessageType, Packet
from robot_link.session import Session

class FakeSession:
    ready=True
    def __init__(self): self.sent=[]
    async def send(self,packet): self.sent.append(packet)

class FakeBalanceBot:
    """Stands in for balance_bot's IPC socket: records lines, spews telemetry."""
    def __init__(self,path): self.path=path; self.lines=[]; self.got=asyncio.Event(); self.writers=[]
    async def start(self): self.server=await asyncio.start_unix_server(self.client,self.path)
    async def client(self,reader,writer):
        self.writers.append(writer)
        async def spew():
            while True: writer.write(b'{"telemetry":1}\n'*50); await asyncio.sleep(.005)
        task=asyncio.create_task(spew())
        try:
            while line:=await reader.readline(): self.lines.append(json.loads(line)); self.got.set()
        finally:
            task.cancel(); await asyncio.gather(task,return_exceptions=True)
            writer.transport.abort()
    async def close(self):
        for w in self.writers: w.transport.abort()
        self.server.close(); await self.server.wait_closed(); await asyncio.sleep(.01)

def pi_args(sock): return SimpleNamespace(bone_host="x",local_socket=sock,local_group="")
def bone_args(balance): return SimpleNamespace(balance_socket=balance)

class ParseDriveTest(unittest.TestCase):
    def test_clamps_and_defaults(self):
        self.assertEqual(parse_drive({"x":2,"y":-3}),{"x":1.0,"y":-1.0,"ttl_ms":300})
        self.assertEqual(parse_drive({"x":0,"y":0,"ttl_ms":5000})["ttl_ms"],1000)
        self.assertEqual(parse_drive({"x":0,"y":0,"ttl_ms":1})["ttl_ms"],50)
    def test_rejects_garbage(self):
        for bad in ({"x":0},{"x":"a","y":0},{"x":math.nan,"y":0},{"x":0,"y":math.inf}):
            with self.assertRaises(ValueError): parse_drive(bad)

class PiLocalApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.sock=os.path.join(self.tmp.name,"pi.sock")
        self.link=PiLink(pi_args(self.sock)); self.server=await self.link.start_local()
    async def asyncTearDown(self): self.server.close(); await self.server.wait_closed(); self.tmp.cleanup()
    async def test_drive_forwarded_as_unacked_event(self):
        self.link.session=FakeSession()
        r,w=await asyncio.open_unix_connection(self.sock)
        w.write(b'{"op":"drive","x":0.25,"y":-0.5,"ttl_ms":300}\n'); await w.drain()
        w.write(b'{"op":"status"}\n'); await w.drain()
        status=json.loads(await asyncio.wait_for(r.readline(),2)); w.close()
        (packet,)=self.link.session.sent
        self.assertEqual(packet.message_type,MessageType.DRIVE_COMMAND)
        self.assertEqual(packet.flags,Flags.EVENT); self.assertEqual(packet.sequence,0)
        self.assertEqual(json.loads(packet.payload),{"x":0.25,"y":-0.5,"ttl_ms":300})
        self.assertEqual(status["drive"]["sent"],1)
    async def test_drive_without_bone_is_dropped_quietly(self):
        r,w=await asyncio.open_unix_connection(self.sock)
        w.write(b'{"op":"drive","x":0,"y":0}\nnot json\n{"op":"drive","x":"bad"}\n{"op":"status"}\n'); await w.drain()
        # "not json" is an unknown op and is answered; drive lines never are.
        self.assertFalse(json.loads(await asyncio.wait_for(r.readline(),2))["ok"])
        status=json.loads(await asyncio.wait_for(r.readline(),2)); w.close()
        self.assertFalse(status["connected"]); self.assertEqual(status["drive"]["dropped"],1)

class BoneRelayTest(unittest.IsolatedAsyncioTestCase):
    async def test_forwards_to_balance_bot_and_survives_its_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=os.path.join(tmp,"bb.sock"); daemon=BoneDaemon(bone_args(path))
            daemon.balance.retry_s=0
            packet=Packet(MessageType.DRIVE_COMMAND,b'{"x":0.1,"y":0.2,"ttl_ms":250}',Flags.EVENT)
            start=time.monotonic(); await daemon.received(packet,None)    # nobody listening
            self.assertLess(time.monotonic()-start,1); self.assertEqual(daemon.drive_dropped,1)
            bb=FakeBalanceBot(path); await bb.start()
            try:
                await daemon.received(packet,None)
                await asyncio.wait_for(bb.got.wait(),2)
                self.assertEqual(bb.lines[0],{"type":"drive","x":0.1,"y":0.2,"ttl_ms":250})
                self.assertEqual(daemon.drive_forwarded,1)
            finally:
                await daemon.balance.close(); await bb.close()

class EndToEndTest(unittest.IsolatedAsyncioTestCase):
    async def test_pi_socket_to_balance_bot(self):
        with tempfile.TemporaryDirectory() as tmp:
            bb=FakeBalanceBot(os.path.join(tmp,"bb.sock")); await bb.start()
            bone=BoneDaemon(bone_args(bb.path))
            server=await asyncio.start_server(bone.connected,"127.0.0.1",0)
            port=server.sockets[0].getsockname()[1]
            link=PiLink(pi_args(os.path.join(tmp,"pi.sock"))); local=await link.start_local()
            r,w=await asyncio.open_connection("127.0.0.1",port)
            async def nothing(packet,session): pass
            session=Session(r,w,2,nothing); link.session=session
            run=asyncio.create_task(session.run())
            try:
                for _ in range(100):
                    if session.ready and bone.session and bone.session.ready: break
                    await asyncio.sleep(.01)
                cr,cw=await asyncio.open_unix_connection(link.args.local_socket)
                cw.write(b'{"op":"drive","x":-0.3,"y":0.4,"ttl_ms":300}\n'); await cw.drain()
                await asyncio.wait_for(bb.got.wait(),2); cw.close()
                self.assertEqual(bb.lines[0],{"type":"drive","x":-0.3,"y":0.4,"ttl_ms":300})
            finally:
                run.cancel(); await asyncio.gather(run,return_exceptions=True)
                local.close(); server.close(); await bone.balance.close(); await bb.close()
