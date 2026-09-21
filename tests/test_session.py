import asyncio, time, unittest
from robot_link.session import Session

async def _noop(packet,session): pass

class SessionTimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_silent_peer_ends_session(self):
        # A peer that accepts the connection and then never sends anything:
        # what a pulled USB cable or a Pi losing power looks like. The
        # heartbeat timeout must end run() instead of it blocking in read().
        release=asyncio.Event()
        async def silent(reader,writer):
            await release.wait(); writer.close()
        server=await asyncio.start_server(silent,"127.0.0.1",0)
        port=server.sockets[0].getsockname()[1]
        try:
            reader,writer=await asyncio.open_connection("127.0.0.1",port)
            session=Session(reader,writer,2,_noop,timeout=.3,interval=.05)
            start=time.monotonic()
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(session.run(),3)
            self.assertLess(time.monotonic()-start,1.5)
        finally:
            release.set(); server.close(); await server.wait_closed()

    async def test_peer_close_ends_session_cleanly(self):
        async def closer(reader,writer): writer.close()
        server=await asyncio.start_server(closer,"127.0.0.1",0)
        port=server.sockets[0].getsockname()[1]
        try:
            reader,writer=await asyncio.open_connection("127.0.0.1",port)
            session=Session(reader,writer,2,_noop,timeout=2,interval=.05)
            await asyncio.wait_for(session.run(),3)
        finally:
            server.close(); await server.wait_closed()
