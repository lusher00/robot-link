import asyncio, json, os, tempfile, time, unittest
from types import SimpleNamespace
from robot_link.bone_daemon import BoneDaemon
from robot_link.protocol import MessageType

class FakeSession:
    ready=True
    def __init__(self): self.sent=[]
    async def send(self,packet): self.sent.append(packet)

class BoneDaemonTest(unittest.IsolatedAsyncioTestCase):
    async def test_sustained_low_voltage_requests_shutdown_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            battery=os.path.join(tmp,"battery.json")
            with open(battery,"w") as f: json.dump({"voltage":9.4},f)
            args=SimpleNamespace(battery_file=battery,battery_max_age=5,battery_interval=.01,
                shutdown_voltage=9.6,hysteresis=.4,low_samples=3,pi_shutdown_delay=5)
            daemon=BoneDaemon(args); daemon.session=FakeSession()
            task=asyncio.create_task(daemon.battery_loop())
            await asyncio.sleep(.03)
            self.assertEqual(daemon.session.sent,[])
            for voltage in (9.3,9.2):
                await asyncio.sleep(.002)
                with open(battery,"w") as f: json.dump({"voltage":voltage},f)
                await asyncio.sleep(.02)
            task.cancel(); await asyncio.gather(task,return_exceptions=True)
            self.assertEqual(len(daemon.session.sent),1)
            self.assertEqual(daemon.session.sent[0].message_type,MessageType.SHUTDOWN_REQUEST)
    async def test_stale_voltage_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            battery=os.path.join(tmp,"battery.json")
            with open(battery,"w") as f: json.dump({"voltage":8.0},f)
            os.utime(battery,(time.time()-30,time.time()-30))
            args=SimpleNamespace(battery_file=battery,battery_max_age=5,battery_interval=.01,
                shutdown_voltage=9.6,hysteresis=.4,low_samples=1,pi_shutdown_delay=5)
            daemon=BoneDaemon(args); daemon.session=FakeSession()
            task=asyncio.create_task(daemon.battery_loop())
            await asyncio.sleep(.03); task.cancel(); await asyncio.gather(task,return_exceptions=True)
            self.assertEqual(daemon.session.sent,[])
