import asyncio, logging, os, struct, time
from .protocol import Flags, MessageType, Packet, PacketParser, VERSION

LOG=logging.getLogger(__name__); HELLO=struct.Struct("!BBQ"); HEARTBEAT=struct.Struct("!Q")

class Session:
    def __init__(self,reader,writer,role,handler,timeout=3.5):
        self.reader=reader; self.writer=writer; self.role=role; self.handler=handler
        self.timeout=timeout; self.parser=PacketParser(); self.ready=False
        self.last_rx=time.monotonic(); self.lock=asyncio.Lock()
    async def send(self,packet):
        async with self.lock: self.writer.write(packet.encode()); await self.writer.drain()
    async def _protocol(self,p):
        if p.message_type==MessageType.HELLO:
            version,_,_=HELLO.unpack(p.payload)
            if version!=VERSION: raise ValueError("incompatible protocol")
            await self.send(Packet(MessageType.READY,bytes((VERSION,)),Flags.EVENT)); return True
        if p.message_type==MessageType.READY: self.ready=p.payload==bytes((VERSION,)); return True
        if p.message_type==MessageType.HEARTBEAT: return True
        if p.message_type==MessageType.PING:
            await self.send(Packet(MessageType.PONG,p.payload,Flags.RESPONSE,p.sequence)); return True
        return False
    async def _heartbeats(self):
        while True:
            await asyncio.sleep(1)
            if time.monotonic()-self.last_rx>self.timeout: raise TimeoutError("heartbeat timeout")
            await self.send(Packet(MessageType.HEARTBEAT,HEARTBEAT.pack(time.monotonic_ns()),Flags.EVENT))
    async def run(self):
        await self.send(Packet(MessageType.HELLO,HELLO.pack(VERSION,self.role,int.from_bytes(os.urandom(8),"big")),Flags.EVENT))
        heartbeat=asyncio.create_task(self._heartbeats())
        try:
            while data:=await self.reader.read(8192):
                self.last_rx=time.monotonic()
                for packet in self.parser.feed(data):
                    if not await self._protocol(packet):
                        if self.ready: await self.handler(packet,self)
                        else: LOG.warning("application packet before READY")
        finally:
            heartbeat.cancel(); await asyncio.gather(heartbeat,return_exceptions=True)
            self.writer.close(); await self.writer.wait_closed()

