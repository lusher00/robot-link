import argparse, asyncio, logging, os, random
from .services import PiServices
from .session import Session

LOG=logging.getLogger("robot-linkd")
async def run(args):
    services=PiServices(args.dry_run,args.sound_dir,args.alsa_device,args.tts_command,args.shutdown_delay)
    await services.start(); delay=.25
    try:
        while True:
            try:
                LOG.info("connecting to %s:%d",args.bone_host,args.bone_port)
                reader,writer=await asyncio.wait_for(asyncio.open_connection(args.bone_host,args.bone_port),3)
                delay=.25; LOG.info("connected to BeagleBone")
                await Session(reader,writer,2,services.handle).run()
            except asyncio.CancelledError: raise
            except Exception as exc: LOG.warning("link down: %s",exc)
            await asyncio.sleep(delay*random.uniform(.8,1.2)); delay=min(delay*2,10)
    finally: await services.close()
def main():
    p=argparse.ArgumentParser(); p.add_argument("--bone-host",default=os.getenv("ROBOT_LINK_BONE_HOST","192.168.7.2"))
    p.add_argument("--bone-port",type=int,default=int(os.getenv("ROBOT_LINK_BONE_PORT","5555")))
    p.add_argument("--sound-dir",default=os.getenv("ROBOT_LINK_SOUND_DIR","/usr/local/share/robot-link/sounds"))
    p.add_argument("--alsa-device",default=os.getenv("ROBOT_LINK_ALSA_DEVICE","default"))
    p.add_argument("--tts-command",default=os.getenv("ROBOT_LINK_TTS_COMMAND",""))
    p.add_argument("--shutdown-delay",type=float,default=float(os.getenv("ROBOT_LINK_SHUTDOWN_DELAY","5")))
    p.add_argument("--dry-run",action="store_true",default=os.getenv("ROBOT_LINK_DRY_RUN","0")=="1")
    p.add_argument("-v","--verbose",action="store_true"); args=p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    asyncio.run(run(args))
if __name__=="__main__": main()

