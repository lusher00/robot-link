import asyncio, json, logging
from pathlib import Path
from .protocol import Flags, MessageType, Packet

LOG=logging.getLogger(__name__)

class PiServices:
    def __init__(self,dry_run,sound_dir,alsa_device,tts_command,shutdown_delay):
        self.dry_run=dry_run; self.sound_dir=Path(sound_dir).resolve(); self.alsa_device=alsa_device
        self.tts_command=tts_command; self.shutdown_delay=shutdown_delay
        self.audio_queue=asyncio.Queue(maxsize=16); self.worker=None
    async def start(self): self.worker=asyncio.create_task(self._audio_loop())
    async def close(self):
        if self.worker: self.worker.cancel(); await asyncio.gather(self.worker,return_exceptions=True)
    async def _reply(self,session,request,ok,detail):
        payload=json.dumps({"type":request.message_type,"ok":ok,"detail":detail},separators=(",",":")).encode()
        await session.send(Packet(MessageType.ACK if ok else MessageType.NACK,payload,
                                  Flags.RESPONSE|(Flags(0) if ok else Flags.ERROR),request.sequence))
    async def handle(self,packet,session):
        if packet.message_type==MessageType.SHUTDOWN_REQUEST: await self._shutdown(packet,session)
        elif packet.message_type==MessageType.PLAY_SOUND: await self._queue("sound",packet,session)
        elif packet.message_type==MessageType.SPEAK: await self._queue("speak",packet,session)
        else: await self._reply(session,packet,False,"unsupported message")
    async def _shutdown(self,packet,session):
        try:
            data=json.loads(packet.payload or b"{}"); delay=max(0,min(float(data.get("delay",self.shutdown_delay)),60))
            reason=str(data.get("reason","requested by BeagleBone"))[:200]
        except (ValueError,TypeError,json.JSONDecodeError) as exc:
            await self._reply(session,packet,False,f"invalid shutdown payload: {exc}"); return
        LOG.warning("shutdown accepted in %.1fs: %s",delay,reason)
        await self._reply(session,packet,True,f"shutdown scheduled in {delay:.1f}s")
        asyncio.create_task(self._poweroff(delay))
    async def _poweroff(self,delay):
        await asyncio.sleep(delay)
        if self.dry_run: LOG.warning("DRY RUN: systemctl poweroff"); return
        process=await asyncio.create_subprocess_exec("/usr/bin/systemctl","poweroff"); await process.wait()
    async def _queue(self,kind,packet,session):
        try:
            data=json.loads(packet.payload); value=str(data["name"] if kind=="sound" else data["text"])
            if kind=="sound" and Path(value).name!=value: raise ValueError("sound name may not contain a path")
            self.audio_queue.put_nowait((kind,value))
        except (KeyError,ValueError,TypeError,json.JSONDecodeError,asyncio.QueueFull) as exc:
            await self._reply(session,packet,False,f"audio rejected: {exc}"); return
        await self._reply(session,packet,True,"audio queued")
    async def _audio_loop(self):
        while True:
            kind,value=await self.audio_queue.get()
            try:
                if self.dry_run: LOG.info("DRY RUN audio %s: %s",kind,value)
                elif kind=="sound": await self._play(value)
                else: await self._speak(value)
            except Exception: LOG.exception("audio job failed")
            finally: self.audio_queue.task_done()
    async def _play(self,name):
        if Path(name).name!=name: raise ValueError("sound name may not contain a path")
        path=(self.sound_dir/name).resolve()
        if path.parent!=self.sound_dir or not path.is_file(): raise FileNotFoundError(path)
        p=await asyncio.create_subprocess_exec("/usr/bin/aplay","-D",self.alsa_device,str(path),
            stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.PIPE)
        _,err=await p.communicate()
        if p.returncode: raise RuntimeError(err.decode(errors="replace").strip())
    async def _speak(self,text):
        if not self.tts_command: raise RuntimeError("TTS command not configured")
        p=await asyncio.create_subprocess_exec(self.tts_command,stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.PIPE)
        _,err=await p.communicate(text.encode())
        if p.returncode: raise RuntimeError(err.decode(errors="replace").strip())
