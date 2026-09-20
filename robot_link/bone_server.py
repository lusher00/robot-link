import argparse, asyncio, json, logging, sys
from .protocol import Flags, MessageType, Packet
from .session import Session

async def run(host,port):
    async def connected(reader,writer):
        queue=asyncio.Queue()
        async def received(packet,session): logging.info("Pi reply type=%#x payload=%r",packet.message_type,packet.payload)
        session=Session(reader,writer,1,received)
        async def console():
            loop=asyncio.get_running_loop()
            while True:
                line=await loop.run_in_executor(None,sys.stdin.readline)
                if not line: return
                parts=line.strip().split(" ",1); cmd=parts[0]; arg=parts[1] if len(parts)>1 else ""
                if cmd=="sound": typ=MessageType.PLAY_SOUND; data={"name":arg}
                elif cmd=="speak": typ=MessageType.SPEAK; data={"text":arg}
                elif cmd=="shutdown": typ=MessageType.SHUTDOWN_REQUEST; data={"reason":"battery low","delay":float(arg or 5)}
                else: print("commands: sound NAME | speak TEXT | shutdown DELAY"); continue
                await session.send(Packet(typ,json.dumps(data).encode(),Flags.ACK_REQUIRED|Flags.HIGH_PRIORITY,1))
        task=asyncio.create_task(console())
        try: await session.run()
        finally: task.cancel(); await asyncio.gather(task,return_exceptions=True)
    server=await asyncio.start_server(connected,host,port)
    logging.info("listening on %s:%d",host,port)
    async with server: await server.serve_forever()
def main():
    p=argparse.ArgumentParser(); p.add_argument("--listen",default="0.0.0.0"); p.add_argument("--port",type=int,default=5555)
    a=p.parse_args(); logging.basicConfig(level=logging.INFO); asyncio.run(run(a.listen,a.port))
if __name__=="__main__": main()

