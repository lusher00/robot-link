import argparse, asyncio, contextlib, json, logging, os, time
from pathlib import Path
from .protocol import Flags, MessageType, Packet
from .session import Session

LOG=logging.getLogger("robot-link-boned")

class BoneDaemon:
    def __init__(self,args):
        self.args=args; self.session=None; self.sequence=1; self.low_samples=0; self.shutdown_sent=False
    def next_sequence(self):
        n=self.sequence; self.sequence=1 if n==0xffff else n+1; return n
    async def received(self,packet,session):
        if packet.message_type in (MessageType.ACK,MessageType.NACK):
            LOG.info("Pi response seq=%d: %s",packet.sequence,packet.payload.decode(errors="replace"))
    async def connected(self,reader,writer):
        if self.session:
            LOG.warning("rejecting second Pi connection"); writer.close(); await writer.wait_closed(); return
        session=Session(reader,writer,1,self.received); self.session=session; self.shutdown_sent=False
        LOG.info("Pi connected from %s",writer.get_extra_info("peername"))
        try: await session.run()
        except Exception as exc: LOG.warning("Pi session ended: %s",exc)
        finally: self.session=None
    async def send_json(self,msg_type,data,priority=False):
        if not self.session or not self.session.ready: raise ConnectionError("Pi link is not ready")
        flags=Flags.ACK_REQUIRED|(Flags.HIGH_PRIORITY if priority else Flags(0))
        seq=self.next_sequence(); await self.session.send(Packet(msg_type,json.dumps(data,separators=(",",":")).encode(),flags,seq))
        return seq
    def read_voltage(self):
        path=Path(self.args.battery_file)
        if not path.is_file() or time.time()-path.stat().st_mtime>self.args.battery_max_age: return None
        data=json.loads(path.read_text())
        for key in ("voltage","voltage_v","v","batt_voltage"):
            if key in data: return float(data[key])
        return None
    async def battery_loop(self):
        while True:
            await asyncio.sleep(self.args.battery_interval)
            try: voltage=self.read_voltage()
            except Exception as exc: LOG.warning("battery status unreadable: %s",exc); voltage=None
            if voltage is None or voltage>self.args.shutdown_voltage+self.args.hysteresis:
                self.low_samples=0; continue
            if voltage<=self.args.shutdown_voltage: self.low_samples+=1
            if self.low_samples>=self.args.low_samples and not self.shutdown_sent:
                try:
                    await self.send_json(MessageType.SHUTDOWN_REQUEST,{"reason":f"battery low: {voltage:.3f} V","delay":self.args.pi_shutdown_delay},True)
                    self.shutdown_sent=True; LOG.error("Pi shutdown requested at %.3f V",voltage)
                except ConnectionError: LOG.warning("battery low at %.3f V but Pi is not connected",voltage)
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
    p.add_argument("--shutdown-voltage",type=float,default=float(os.getenv("ROBOT_LINK_SHUTDOWN_VOLTAGE","9.6")))
    p.add_argument("--hysteresis",type=float,default=float(os.getenv("ROBOT_LINK_BATTERY_HYSTERESIS","0.4")))
    p.add_argument("--low-samples",type=int,default=int(os.getenv("ROBOT_LINK_LOW_SAMPLES","5")))
    p.add_argument("--battery-interval",type=float,default=float(os.getenv("ROBOT_LINK_BATTERY_INTERVAL","1")))
    p.add_argument("--battery-max-age",type=float,default=float(os.getenv("ROBOT_LINK_BATTERY_MAX_AGE","5")))
    p.add_argument("--pi-shutdown-delay",type=float,default=float(os.getenv("ROBOT_LINK_PI_SHUTDOWN_DELAY","5")))
    p.add_argument("-v","--verbose",action="store_true"); args=p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    asyncio.run(BoneDaemon(args).run())
if __name__=="__main__": main()

