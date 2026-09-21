import argparse, asyncio, contextlib, json, logging, os, time
from pathlib import Path
from .drive import parse_drive
from .protocol import Flags, MessageType, Packet
from .session import Session

LOG=logging.getLogger("robot-link-boned")

class BalanceBotClient:
    """Relays Pi drive commands to balance_bot's IPC socket.

    balance_bot decides whether to use them (Pi-drive gate open, SBUS stick
    centred) and expires each one after its ttl_ms, so this never has to be
    reliable: a failed or slow write drops that command, the next one replaces
    it, and reconnect attempts are rate limited. It must never stall the link.

    balance_bot streams telemetry to every IPC client, so a reader task drains
    and discards it; otherwise balance_bot would see us as a slow client.
    """
    def __init__(self,path,retry_s=1.0,write_timeout_s=.2):
        self.path=path; self.retry_s=retry_s; self.write_timeout_s=write_timeout_s
        self.writer=None; self.discard=None; self.next_try=0.0
    @property
    def connected(self):
        return self.writer is not None and not self.writer.is_closing() and not (self.discard and self.discard.done())
    async def _connect(self):
        now=time.monotonic()
        if now<self.next_try: return False
        self.next_try=now+self.retry_s
        try: reader,writer=await asyncio.wait_for(asyncio.open_unix_connection(self.path),.5)
        except (OSError,asyncio.TimeoutError) as exc:
            LOG.debug("balance_bot socket %s unavailable: %s",self.path,exc); return False
        self.writer=writer; self.discard=asyncio.create_task(self._discard(reader))
        LOG.info("connected to balance_bot at %s",self.path); return True
    @staticmethod
    async def _discard(reader):
        with contextlib.suppress(OSError):
            while await reader.read(65536): pass
    async def send(self,obj):
        if not self.connected:
            await self.close()
            if not await self._connect(): return False
        try:
            self.writer.write(json.dumps(obj,separators=(",",":")).encode()+b"\n")
            await asyncio.wait_for(self.writer.drain(),self.write_timeout_s)
            return True
        except (OSError,asyncio.TimeoutError) as exc:
            LOG.warning("balance_bot write failed: %s",exc or type(exc).__name__)
            await self.close(); return False
    async def close(self):
        if self.discard:
            self.discard.cancel(); await asyncio.gather(self.discard,return_exceptions=True)
        if self.writer: self.writer.transport.abort()
        self.writer=None; self.discard=None

class BoneDaemon:
    def __init__(self,args):
        self.args=args; self.session=None; self.sequence=1
        self.last_battery_sample=None; self.last_shutdown_event=None
        self.balance=BalanceBotClient(getattr(args,"balance_socket","/tmp/balance_bot.sock"))
        self.last_drive=None; self.last_drive_at=None; self.drive_forwarded=0; self.drive_dropped=0
    def next_sequence(self):
        n=self.sequence; self.sequence=1 if n==0xffff else n+1; return n
    async def received(self,packet,session):
        if packet.message_type==MessageType.DRIVE_COMMAND:
            await self.drive_received(packet); return
        if packet.message_type in (MessageType.ACK,MessageType.NACK):
            LOG.info("Pi response seq=%d: %s",packet.sequence,packet.payload.decode(errors="replace"))
    async def connected(self,reader,writer):
        if self.session:
            LOG.warning("rejecting second Pi connection"); writer.close(); await writer.wait_closed(); return
        session=Session(reader,writer,1,self.received); self.session=session
        self.last_battery_sample=None
        LOG.info("Pi connected from %s",writer.get_extra_info("peername"))
        try: await session.run()
        except Exception as exc: LOG.warning("Pi session ended: %s",exc)
        finally: self.session=None
    async def drive_received(self,packet):
        try: cmd=parse_drive(json.loads(packet.payload))
        except ValueError as exc: LOG.warning("bad DRIVE_COMMAND from Pi: %s",exc); return
        self.last_drive=cmd; self.last_drive_at=time.monotonic()
        if await self.balance.send(dict(type="drive",**cmd)): self.drive_forwarded+=1
        else: self.drive_dropped+=1
    def drive_status(self):
        if self.last_drive is None: return None
        return dict(self.last_drive,age_s=round(time.monotonic()-self.last_drive_at,3),
                    forwarded=self.drive_forwarded,dropped=self.drive_dropped,
                    balance_bot_connected=self.balance.connected)
    async def send_json(self,msg_type,data,priority=False):
        if not self.session or not self.session.ready: raise ConnectionError("Pi link is not ready")
        flags=Flags.ACK_REQUIRED|(Flags.HIGH_PRIORITY if priority else Flags(0))
        seq=self.next_sequence(); await self.session.send(Packet(msg_type,json.dumps(data,separators=(",",":")).encode(),flags,seq))
        return seq
    def read_battery_sample(self):
        path=Path(self.args.battery_file)
        if not path.is_file() or time.time()-path.stat().st_mtime>self.args.battery_max_age: return None
        data=json.loads(path.read_text())
        for key in ("voltage","voltage_v","v","batt_voltage"):
            if key in data: return path.stat().st_mtime_ns,float(data[key]),data
        return None
    def read_voltage(self):
        sample=self.read_battery_sample()
        return None if sample is None else sample[1]
    async def battery_loop(self):
        # This loop is the only path that forwards a battery shutdown to the Pi,
        # and nothing awaits its task, so an escaped exception would stop it
        # silently. Every iteration is contained and logged instead.
        while True:
            await asyncio.sleep(self.args.battery_interval)
            try: await self._battery_step()
            except asyncio.CancelledError: raise
            except Exception: LOG.exception("battery loop iteration failed")
    async def _battery_step(self):
        try: sample=self.read_battery_sample()
        except Exception as exc:
            # e.g. a partially written /run/batt_status.json; retry next tick
            LOG.warning("battery status unreadable: %s",exc); return
        if sample is None: return
        sample_id,voltage,status=sample
        if sample_id==self.last_battery_sample: return
        self.last_battery_sample=sample_id
        if self.session and self.session.ready:
            try:
                await self.session.send(Packet(
                    MessageType.BATTERY_STATUS,
                    json.dumps({"voltage":voltage,"sample_ns":sample_id},
                               separators=(",",":")).encode(),
                    Flags.EVENT))
            except (ConnectionError, OSError):
                LOG.debug("battery update dropped while Pi disconnected")
        event=status.get("shutdown_event")
        requested=status.get("shutdown_requested") in (True,1,"1")
        if requested and event and event!=self.last_shutdown_event:
            try:
                await self.send_json(MessageType.SHUTDOWN_REQUEST,
                    {"reason":f"battery monitor shutdown: {voltage:.3f} V",
                     "delay":self.args.pi_shutdown_delay},True)
                self.last_shutdown_event=event
                LOG.error("forwarded battery shutdown event %s at %.3f V",event,voltage)
            except OSError:  # ConnectionError, or a stalled send (TimeoutError)
                LOG.warning("battery shutdown event %s pending; Pi is not connected",event)
    async def local_client(self,reader,writer):
        try:
            while line:=await reader.readline():
                try:
                    req=json.loads(line); op=req["op"]
                    if op=="sound": seq=await self.send_json(MessageType.PLAY_SOUND,{"name":str(req["name"])})
                    elif op=="speak": seq=await self.send_json(MessageType.SPEAK,{"text":str(req["text"])})
                    elif op=="shutdown": seq=await self.send_json(MessageType.SHUTDOWN_REQUEST,{"reason":str(req.get("reason","manual")),"delay":float(req.get("delay",5))},True)
                    elif op=="status":
                        writer.write(json.dumps({"ok":True,"connected":bool(self.session and self.session.ready),"voltage":self.read_voltage(),"drive":self.drive_status()}).encode()+b"\n"); await writer.drain(); continue
                    else: raise ValueError("unknown operation")
                    response={"ok":True,"sequence":seq}
                except Exception as exc: response={"ok":False,"error":str(exc)}
                writer.write(json.dumps(response).encode()+b"\n"); await writer.drain()
        finally: writer.close(); await writer.wait_closed()
    async def bind_tcp(self):
        # The listener binds to the USB gadget address only, so nothing on the
        # Bone's Wi-Fi can connect. That address does not exist until usb0 is
        # up, which can be after this service starts at boot. Wait for it here
        # rather than exiting: a fast crash loop would hit systemd's start
        # limit and leave the service failed.
        warned=False
        while True:
            try: tcp=await asyncio.start_server(self.connected,self.args.listen,self.args.port)
            except OSError as exc:
                if not warned: LOG.warning("cannot listen on %s:%d yet (%s); retrying",self.args.listen,self.args.port,exc)
                warned=True; await asyncio.sleep(1); continue
            LOG.info("listening for Pi on %s:%d",self.args.listen,self.args.port)
            return tcp
    async def run(self):
        with contextlib.suppress(FileNotFoundError): os.unlink(self.args.socket)
        os.makedirs(os.path.dirname(self.args.socket) or ".",exist_ok=True)
        local=await asyncio.start_unix_server(self.local_client,self.args.socket); os.chmod(self.args.socket,0o660)
        battery=asyncio.create_task(self.battery_loop())
        try:
            tcp=await self.bind_tcp()
            async with local,tcp: await asyncio.gather(local.serve_forever(),tcp.serve_forever())
        finally:
            battery.cancel(); await asyncio.gather(battery,return_exceptions=True)
            await self.balance.close()
            with contextlib.suppress(FileNotFoundError): os.unlink(self.args.socket)

def main():
    p=argparse.ArgumentParser(description="Robot Link BeagleBone service")
    p.add_argument("--listen",default=os.getenv("ROBOT_LINK_LISTEN","192.168.7.2")); p.add_argument("--port",type=int,default=int(os.getenv("ROBOT_LINK_PORT","5555")))
    p.add_argument("--socket",default=os.getenv("ROBOT_LINK_BONE_SOCKET","/run/robot-link/bone.sock"))
    p.add_argument("--balance-socket",default=os.getenv("ROBOT_LINK_BALANCE_SOCKET","/tmp/balance_bot.sock"))
    p.add_argument("--battery-file",default=os.getenv("ROBOT_LINK_BATTERY_FILE","/run/batt_status.json"))
    p.add_argument("--battery-interval",type=float,default=float(os.getenv("ROBOT_LINK_BATTERY_INTERVAL","1")))
    p.add_argument("--battery-max-age",type=float,default=float(os.getenv("ROBOT_LINK_BATTERY_MAX_AGE","120")))
    p.add_argument("--pi-shutdown-delay",type=float,default=float(os.getenv("ROBOT_LINK_PI_SHUTDOWN_DELAY","5")))
    p.add_argument("-v","--verbose",action="store_true"); args=p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    asyncio.run(BoneDaemon(args).run())
if __name__=="__main__": main()
