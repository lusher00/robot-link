import asyncio, json, os, tempfile, time, unittest
from types import SimpleNamespace
from robot_link.bone_daemon import BoneDaemon
from robot_link.protocol import MessageType

class FakeSession:
    ready=True
    def __init__(self): self.sent=[]
    async def send(self,packet): self.sent.append(packet)

class BoneDaemonTest(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_event_is_forwarded_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            battery=os.path.join(tmp,"battery.json")
            with open(battery,"w") as f: json.dump({"voltage":9.4,
                "shutdown_requested":1,"shutdown_event":1234},f)
            args=SimpleNamespace(battery_file=battery,battery_max_age=5,battery_interval=.01,
                pi_shutdown_delay=5)
            daemon=BoneDaemon(args); daemon.session=FakeSession()
            task=asyncio.create_task(daemon.battery_loop())
            await asyncio.sleep(.03)
            task.cancel(); await asyncio.gather(task,return_exceptions=True)
            shutdowns=[p for p in daemon.session.sent
                       if p.message_type==MessageType.SHUTDOWN_REQUEST]
            self.assertEqual(len(shutdowns),1)
    async def test_voltage_alone_never_requests_shutdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            battery=os.path.join(tmp,"battery.json")
            args=SimpleNamespace(battery_file=battery,battery_max_age=5,
                battery_interval=.01,pi_shutdown_delay=5)
            daemon=BoneDaemon(args); daemon.session=FakeSession()
            task=asyncio.create_task(daemon.battery_loop())
            for voltage in (9.5,9.4,9.3):
                with open(battery,"w") as f: json.dump({"voltage":voltage,
                    "status":"critical","shutdown_requested":0},f)
                await asyncio.sleep(.02)
            task.cancel(); await asyncio.gather(task,return_exceptions=True)
            self.assertFalse(any(p.message_type==MessageType.SHUTDOWN_REQUEST
                                 for p in daemon.session.sent))
    async def test_stale_voltage_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            battery=os.path.join(tmp,"battery.json")
            with open(battery,"w") as f: json.dump({"voltage":8.0},f)
            os.utime(battery,(time.time()-30,time.time()-30))
            args=SimpleNamespace(battery_file=battery,battery_max_age=5,battery_interval=.01,
                pi_shutdown_delay=5)
            daemon=BoneDaemon(args); daemon.session=FakeSession()
            task=asyncio.create_task(daemon.battery_loop())
            await asyncio.sleep(.03); task.cancel(); await asyncio.gather(task,return_exceptions=True)
            self.assertEqual(daemon.session.sent,[])
