import asyncio, contextlib, logging, os, struct, time
from .protocol import Flags, MessageType, Packet, PacketParser, VERSION

LOG=logging.getLogger(__name__); HELLO=struct.Struct("!BBQ"); HEARTBEAT=struct.Struct("!Q")

class Session:
    def __init__(self,reader,writer,role,handler,timeout=3.5,interval=1.0):
        self.reader=reader; self.writer=writer; self.role=role; self.handler=handler
        self.timeout=timeout; self.interval=interval; self.parser=PacketParser(); self.ready=False
        self.last_rx=time.monotonic(); self.lock=asyncio.Lock()
    async def send(self,packet):
        # Bounded drain: a peer that stops reading (cable pulled, Pi lost power)
        # must not stall the sender, and above all must not stall the heartbeat.
        async with self.lock:
            self.writer.write(packet.encode())
            try: await asyncio.wait_for(self.writer.drain(),self.timeout)
            except asyncio.TimeoutError: raise TimeoutError("send stalled") from None
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
            await asyncio.sleep(self.interval)
            if time.monotonic()-self.last_rx>self.timeout: raise TimeoutError("heartbeat timeout")
            await self.send(Packet(MessageType.HEARTBEAT,HEARTBEAT.pack(time.monotonic_ns()),Flags.EVENT))
    async def _receive(self):
        while data:=await self.reader.read(8192):
            self.last_rx=time.monotonic()
            for packet in self.parser.feed(data):
                if not await self._protocol(packet):
                    if self.ready: await self.handler(packet,self)
                    else: LOG.warning("application packet before READY")
    async def run(self):
        # Receive and heartbeat run side by side; whichever finishes first ends
        # the session. Previously a heartbeat timeout was raised inside a task
        # nobody awaited while run() sat in reader.read(), so a silent peer held
        # the session open until TCP itself gave up (minutes).
        tasks=[]
        try:
            await self.send(Packet(MessageType.HELLO,HELLO.pack(VERSION,self.role,int.from_bytes(os.urandom(8),"big")),Flags.EVENT))
            tasks=[asyncio.create_task(self._receive()),asyncio.create_task(self._heartbeats())]
            done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            for task in done: task.result()
        finally:
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            self.writer.close()
            # close() flushes buffered data first; to a dead peer that never
            # finishes, so give it a moment and then drop the connection.
            try: await asyncio.wait_for(self.writer.wait_closed(),1)
            except (asyncio.TimeoutError,OSError): self.writer.transport.abort()

