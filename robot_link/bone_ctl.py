import argparse, asyncio, json, os
async def run(args):
    reader,writer=await asyncio.open_unix_connection(args.socket)
    req={"op":args.command}
    if args.command=="sound": req["name"]=args.value
    elif args.command=="speak": req["text"]=args.value
    elif args.command=="shutdown": req.update(reason=args.reason,delay=args.delay)
    writer.write(json.dumps(req).encode()+b"\n"); await writer.drain(); print((await reader.readline()).decode().strip())
    writer.close(); await writer.wait_closed()
def main():
    p=argparse.ArgumentParser(); p.add_argument("--socket",default="/run/robot-link/bone.sock")
    sub=p.add_subparsers(dest="command",required=True); sub.add_parser("status")
    for cmd in ("sound","speak"): q=sub.add_parser(cmd); q.add_argument("value")
    q=sub.add_parser("shutdown"); q.add_argument("--reason",default="manual"); q.add_argument("--delay",type=float,default=5)
    asyncio.run(run(p.parse_args()))
if __name__=="__main__": main()

