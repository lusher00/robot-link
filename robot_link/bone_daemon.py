import argparse, asyncio, contextlib, json, logging, os, time
from pathlib import Path
from .protocol import Flags, MessageType, Packet
from .session import Session

LOG=logging.getLogger("robot-link-boned")

class BoneDaemon:
    def __init__(self,args):
        self.args=args; self.session=None; self.sequence=1
        self.last_battery_sample=None; self.last_shutdown_event=None
    def next_sequence(self):
        n=self.sequence; self.sequence=1 if n==0xffff else n+1; return n
    async def received(self,packet,session):
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
                        writer.write(json.dumps({"ok":True,"connected":bool(self.session and self.session.ready),"voltage":self.read_voltage()}).encode()+b"\n"); await writer.drain(); continue
                    else: raise ValueError("unknown operation")
                    response={"ok":True,"sequence":seq}
                except Exception as exc: response={"ok":False,"error":str(exc)}
                writer.write(json.dumps(response).encode()+b"\n"); await writer.drain()
        finally: writer.close(); await writer.wait_closed()
    async def run(self):
        with contextlib.suppress(FileNotFoundError): os.unlink(self.args.socket)
        os.makedirs(os.path.dirname(self.args.socket) or ".",exist_ok=True)
        local=await asyncio.start_unix_server(self.local_client,self.args.socket); os.chmod(self.args.socket,0o660)
        tcp=await asyncio.start_server(self.connected,self.args.listen,self.args.port)
        battery=asyncio.create_task(self.battery_loop())
        LOG.info("listening for Pi on %s:%d",self.args.listen,self.args.port)
        try:
            async with local,tcp: await asyncio.gather(local.serve_forever(),tcp.serve_forever())
        finally:
            battery.cancel(); await asyncio.gather(battery,return_exceptions=True)
            with contextlib.suppress(FileNotFoundError): os.unlink(self.args.socket)

def main():
    p=argparse.ArgumentParser(description="Robot Link BeagleBone service")
    p.add_argument("--listen",default=os.getenv("ROBOT_LINK_LISTEN","0.0.0.0")); p.add_argument("--port",type=int,default=int(os.getenv("ROBOT_LINK_PORT","5555")))
    p.add_argument("--socket",default=os.getenv("ROBOT_LINK_BONE_SOCKET","/run/robot-link/bone.sock"))
    p.add_argument("--battery-file",default=os.getenv("ROBOT_LINK_BATTERY_FILE","/run/batt_status.json"))
    p.add_argument("--battery-interval",type=float,default=float(os.getenv("ROBOT_LINK_BATTERY_INTERVAL","1")))
    p.add_argument("--battery-max-age",type=float,default=float(os.getenv("ROBOT_LINK_BATTERY_MAX_AGE","120")))
    p.add_argument("--pi-shutdown-delay",type=float,default=float(os.getenv("ROBOT_LINK_PI_SHUTDOWN_DELAY","5")))
    p.add_argument("-v","--verbose",action="store_true"); args=p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    asyncio.run(BoneDaemon(args).run())
if __name__=="__main__": main()
