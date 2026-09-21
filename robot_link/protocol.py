from dataclasses import dataclass
from enum import IntEnum, IntFlag
import struct

START, END, VERSION, OVERHEAD, MAX_PAYLOAD = 0xAA, 0x55, 2, 12, 4096
HEADER = struct.Struct("!BHHBH")

class Flags(IntFlag):
    ACK_REQUIRED=1; RESPONSE=2; ERROR=4; EVENT=8; IDEMPOTENT=16; HIGH_PRIORITY=32

class MessageType(IntEnum):
    HELLO=0x0001; READY=0x0002; HEARTBEAT=0x0003; ACK=0x0004; NACK=0x0005
    PING=0x0007; PONG=0x0008; DRIVE_COMMAND=0x0200; BATTERY_STATUS=0x0400; SHUTDOWN_REQUEST=0x0401
    SPEAK=0x0600; PLAY_SOUND=0x0601; AUDIO_STATUS=0x0602

def crc16_xmodem(data: bytes) -> int:
    crc=0
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xffff if crc & 0x8000 else (crc << 1) & 0xffff
    return crc

@dataclass(frozen=True, slots=True)
class Packet:
    message_type: int
    payload: bytes=b""
    flags: Flags=Flags(0)
    sequence: int=0
    def encode(self) -> bytes:
        if len(self.payload)>MAX_PAYLOAD or int(self.flags)&0xc0: raise ValueError("invalid packet")
        protected=HEADER.pack(VERSION,len(self.payload),self.message_type,int(self.flags),self.sequence)+self.payload
        return bytes((START,))+protected+struct.pack("!H",crc16_xmodem(protected))+bytes((END,))

class PacketParser:
    def __init__(self,max_payload: int=MAX_PAYLOAD):
        self.max_payload=max_payload; self.buffer=bytearray(); self.crc_errors=0; self.frame_errors=0
    def feed(self,data: bytes) -> list[Packet]:
        self.buffer.extend(data); result=[]
        while True:
            try: start=self.buffer.index(START)
            except ValueError: self.buffer.clear(); return result
            del self.buffer[:start]
            if len(self.buffer)<OVERHEAD: return result
            version,length,msg_type,flags,sequence=HEADER.unpack_from(self.buffer,1)
            if version!=VERSION or length>self.max_payload or flags&0xc0:
                self.frame_errors+=1; del self.buffer[0]; continue
            total=OVERHEAD+length
            if len(self.buffer)<total: return result
            end=1+HEADER.size+length; protected=bytes(self.buffer[1:end])
            received=struct.unpack_from("!H",self.buffer,end)[0]
            if self.buffer[total-1]!=END or received!=crc16_xmodem(protected):
                self.crc_errors+=1; del self.buffer[0]; continue
            result.append(Packet(msg_type,bytes(self.buffer[1+HEADER.size:end]),Flags(flags),sequence))
            del self.buffer[:total]

