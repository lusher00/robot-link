import argparse, asyncio, json, os
async def run(args):
    reader,writer=await asyncio.open_unix_connection(args.socket)
    if args.command=="drive":
        # Pi local socket only. Streams the command at 10 Hz for --for seconds;
        # drive lines are never answered, so there is nothing to read back.
        cmd={"op":"drive","x":args.x,"y":args.y,"ttl_ms":args.ttl}
        for _ in range(max(1,round(args.seconds*10))):
            writer.write(json.dumps(cmd).encode()+b"\n"); await writer.drain(); await asyncio.sleep(.1)
        writer.write(b'{"op":"status"}\n'); await writer.drain()
        print((await reader.readline()).decode().strip())
        writer.close(); await writer.wait_closed(); return
    req={"op":args.command}
    if args.command=="sound": req["name"]=args.value
    elif args.command=="speak": req["text"]=args.value
    elif args.command=="shutdown": req.update(reason=args.reason,delay=args.delay)
    writer.write(json.dumps(req).encode()+b"\n"); await writer.drain(); print((await reader.readline()).decode().strip())
    writer.close(); await writer.wait_closed()
def main():
    p=argparse.ArgumentParser(description="Robot Link local control (Bone: bone.sock, Pi: pi.sock)")
    p.add_argument("--socket",default=os.getenv("ROBOT_LINK_CTL_SOCKET",
        "/run/robot-link/pi.sock" if os.path.exists("/run/robot-link/pi.sock") else "/run/robot-link/bone.sock"))
    sub=p.add_subparsers(dest="command",required=True); sub.add_parser("status")
    for cmd in ("sound","speak"): q=sub.add_parser(cmd); q.add_argument("value")
    q=sub.add_parser("shutdown"); q.add_argument("--reason",default="manual"); q.add_argument("--delay",type=float,default=5)
    q=sub.add_parser("drive",help="Pi only: send a drive command at 10 Hz (x steer, y drive, each -1..1)")
    q.add_argument("x",type=float); q.add_argument("y",type=float)
    q.add_argument("--for",dest="seconds",type=float,default=1.0); q.add_argument("--ttl",type=int,default=300)
    asyncio.run(run(p.parse_args()))
if __name__=="__main__": main()
