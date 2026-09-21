import argparse, asyncio, contextlib, grp, json, logging, os, random, time
from .drive import encode_drive, parse_drive
from .protocol import Flags, MessageType, Packet
from .services import PiServices
from .session import Session

LOG=logging.getLogger("robot-linkd")

def write_status(path,state):
    if not path: return
    os.makedirs(os.path.dirname(path) or ".",exist_ok=True)
    tmp=path+".tmp"; data=dict(state,updated=time.time())
    with open(tmp,"w") as f: json.dump(data,f,separators=(",",":"))
    os.replace(tmp,path)

class PiLink:
    """Owns the single Bone session and the local socket other Pi services use.

    Local API, one JSON object per line on --local-socket:
      {"op":"drive","x":..,"y":..,"ttl_ms":..}  forwarded as DRIVE_COMMAND;
                                                never answered, so a publisher
                                                that never reads cannot fill
                                                this socket and stall the link
      {"op":"status"}                           answered with one status line
    """
    def __init__(self,args):
        self.args=args; self.session=None
        self.state={"connected":False,"bone_host":args.bone_host,"voltage":None}
        self.last_drive=None; self.last_drive_at=None; self.drive_sent=0; self.drive_dropped=0
    @property
    def ready(self): return bool(self.session and self.session.ready)
    async def send_drive(self,cmd):
        self.last_drive=cmd; self.last_drive_at=time.monotonic()
        session=self.session
        if not session or not session.ready: self.drive_dropped+=1; return False
        try: await session.send(Packet(MessageType.DRIVE_COMMAND,encode_drive(cmd),Flags.EVENT))
        except OSError: self.drive_dropped+=1; return False
        self.drive_sent+=1; return True
    def status(self):
        drive=None
        if self.last_drive is not None:
            drive=dict(self.last_drive,age_s=round(time.monotonic()-self.last_drive_at,3),
                       sent=self.drive_sent,dropped=self.drive_dropped)
        return {"ok":True,"connected":self.ready,"voltage":self.state.get("voltage"),"drive":drive}
    async def local_client(self,reader,writer):
        warned=False
        try:
            while line:=await reader.readline():
                try: req=json.loads(line); op=req.get("op") if isinstance(req,dict) else None
                except ValueError: req={}; op=None
                if op=="drive":
                    try: await self.send_drive(parse_drive(req))
                    except ValueError as exc:
                        if not warned: LOG.warning("rejected local drive command: %s",exc); warned=True
                elif op=="status":
                    writer.write(json.dumps(self.status()).encode()+b"\n"); await writer.drain()
                else:
                    writer.write(b'{"ok":false,"error":"unknown operation"}\n'); await writer.drain()
        except (ConnectionError,ValueError): pass
        finally:
            writer.close()
            with contextlib.suppress(Exception): await writer.wait_closed()
    async def start_local(self):
        path=self.args.local_socket
        if not path: return None
        with contextlib.suppress(FileNotFoundError): os.unlink(path)
        os.makedirs(os.path.dirname(path) or ".",exist_ok=True)
        server=await asyncio.start_unix_server(self.local_client,path)
        mode=0o666
        if self.args.local_group:
            try: os.chown(path,-1,grp.getgrnam(self.args.local_group).gr_gid); mode=0o660
            except (KeyError,OSError) as exc:
                LOG.warning("local socket group %r unusable (%s); socket left world-writable",self.args.local_group,exc)
        os.chmod(path,mode)
        LOG.info("local API on %s",path)
        return server

async def run(args):
    services=PiServices(args.dry_run,args.sound_dir,args.alsa_device,args.tts_command,args.shutdown_delay)
    await services.start(); delay=.25
    link=PiLink(args); state=link.state
    write_status(args.status_file,state)
    local=await link.start_local()
    async def handle(packet,session):
        if packet.message_type==MessageType.BATTERY_STATUS:
            data=json.loads(packet.payload)
            state["voltage"]=float(data["voltage"])
            state["battery_sample_ns"]=data.get("sample_ns")
            write_status(args.status_file,state)
            return
        await services.handle(packet,session)
    try:
        while True:
            try:
                LOG.info("connecting to %s:%d",args.bone_host,args.bone_port)
                reader,writer=await asyncio.wait_for(asyncio.open_connection(args.bone_host,args.bone_port),3)
                delay=.25; LOG.info("connected to BeagleBone")
                state["connected"]=True; write_status(args.status_file,state)
                session=Session(reader,writer,2,handle); link.session=session
                try: await session.run()
                finally:
                    link.session=None
                    state["connected"]=False; write_status(args.status_file,state)
            except asyncio.CancelledError: raise
            except Exception as exc: LOG.warning("link down: %s",exc)
            await asyncio.sleep(delay*random.uniform(.8,1.2)); delay=min(delay*2,10)
    finally:
        state["connected"]=False; write_status(args.status_file,state)
        if local:
            local.close()
            with contextlib.suppress(FileNotFoundError): os.unlink(args.local_socket)
        await services.close()
def main():
    p=argparse.ArgumentParser(); p.add_argument("--bone-host",default=os.getenv("ROBOT_LINK_BONE_HOST","192.168.7.2"))
    p.add_argument("--bone-port",type=int,default=int(os.getenv("ROBOT_LINK_BONE_PORT","5555")))
    p.add_argument("--sound-dir",default=os.getenv("ROBOT_LINK_SOUND_DIR","/usr/local/share/robot-link/sounds"))
    p.add_argument("--alsa-device",default=os.getenv("ROBOT_LINK_ALSA_DEVICE","default"))
    p.add_argument("--tts-command",default=os.getenv("ROBOT_LINK_TTS_COMMAND",""))
    p.add_argument("--shutdown-delay",type=float,default=float(os.getenv("ROBOT_LINK_SHUTDOWN_DELAY","5")))
    p.add_argument("--status-file",default=os.getenv("ROBOT_LINK_STATUS_FILE","/run/robot-link/status.json"))
    p.add_argument("--local-socket",default=os.getenv("ROBOT_LINK_LOCAL_SOCKET","/run/robot-link/pi.sock"))
    p.add_argument("--local-group",default=os.getenv("ROBOT_LINK_LOCAL_GROUP",""),
                   help="group allowed to use the local socket (0660); empty = any local user (0666)")
    p.add_argument("--dry-run",action="store_true",default=os.getenv("ROBOT_LINK_DRY_RUN","0")=="1")
    p.add_argument("-v","--verbose",action="store_true"); args=p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    asyncio.run(run(args))
if __name__=="__main__": main()
