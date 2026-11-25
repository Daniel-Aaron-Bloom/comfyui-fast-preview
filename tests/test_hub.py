import asyncio
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import helpers  # noqa: E402

pkg = helpers.load()
hub_mod = pkg.hub


class FakeWS:
    def __init__(self, delay=0.0, fail_after=None):
        self.sent, self.delay, self.fail_after = [], delay, fail_after

    async def _send(self, item):
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            raise ConnectionResetError("gone")
        if self.delay:
            await asyncio.sleep(self.delay)
        self.sent.append(item)

    send_str = send_bytes = _send


async def settle(cond, timeout=2.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.005)


class Queueing(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = hub_mod.Hub()
        self.hub.bind(asyncio.get_running_loop())

    async def test_frames_arrive_in_the_order_they_were_queued(self):
        ws = FakeWS()
        ch = self.hub.attach("c", ws)
        for i in range(5):
            ch.push(("k", i), b"%d" % i)
        ch.push(("t",), "text")
        await settle(lambda: len(ws.sent) == 6)
        self.assertEqual(ws.sent, [b"0", b"1", b"2", b"3", b"4", "text"])
        self.hub.detach("c", ch)

    async def test_same_key_replaces_in_place(self):
        ws = FakeWS(delay=0.05)
        ch = self.hub.attach("c", ws)
        ch.push(("block",), b"first")        # occupies the sender while we queue the rest
        await asyncio.sleep(0.01)
        ch.push(("k",), b"old")
        ch.push(("other",), b"mid")
        ch.push(("k",), b"new")
        await settle(lambda: len(ws.sent) == 3)
        self.assertEqual(ws.sent, [b"first", b"new", b"mid"])    # `new` kept `old`'s place

    async def test_a_new_result_for_a_node_drops_the_old_ones_queued_frames(self):
        ws = FakeWS(delay=0.05)
        ch = self.hub.attach("c", ws)
        ch.push(("block",), b"blocker")
        await asyncio.sleep(0.01)
        ch.push(("img", "t1", 0), b"t1-frame", node="7", token="t1")
        ch.push(("img", "other", 0), b"node8-frame", node="8", token="x")
        ch.push(("begin", "t2"), "begin-t2", node="7", token="t2", supersede=True)
        ch.push(("img", "t2", 0), b"t2-frame", node="7", token="t2")
        await settle(lambda: len(ws.sent) == 4)
        self.assertEqual(ws.sent, [b"blocker", b"node8-frame", "begin-t2", b"t2-frame"])

    async def test_a_stalled_client_sheds_old_images_but_never_control_frames(self):
        ws = FakeWS(delay=0.3)
        ch = self.hub.attach("c", ws)
        ch.push(("block",), b"x")
        await asyncio.sleep(0.01)
        big = b"z" * (20 << 20)
        ch.push(("begin",), "begin-text", node="1", token="a")
        for i in range(5):
            ch.push(("img", i), big, node="1", token="a")
        queued = [k for k in ch._queue]
        self.assertIn(("begin",), queued)
        self.assertLessEqual(ch._bytes, hub_mod.MAX_QUEUED_BYTES + len(big))
        self.assertLess(len([k for k in queued if k[0] == "img"]), 5)
        self.assertIn(("img", 4), queued)       # the newest survived
        ch.close()

    async def test_a_slow_client_does_not_hold_up_a_fast_one(self):
        slow, fast = FakeWS(delay=0.5), FakeWS()
        a, b = self.hub.attach("slow", slow), self.hub.attach("fast", fast)
        for i in range(3):
            self.hub.publish("slow", ("k", i), b"s")
            self.hub.publish("fast", ("k", i), b"f")
        await settle(lambda: len(fast.sent) == 3, timeout=0.3)
        self.assertLess(len(slow.sent), 3)
        a.close(); b.close()

    async def test_every_socket_of_a_client_gets_it(self):
        w1, w2 = FakeWS(), FakeWS()
        self.hub.attach("c", w1); self.hub.attach("c", w2)
        self.hub.publish("c", ("k",), b"hi")
        await settle(lambda: w1.sent and w2.sent)

    async def test_publish_from_another_thread(self):
        ws = FakeWS()
        self.hub.attach("c", ws)
        t = threading.Thread(target=lambda: [self.hub.publish("c", ("k", i), b"%d" % i) for i in range(50)])
        t.start(); t.join()
        await settle(lambda: len(ws.sent) == 50)
        self.assertEqual(ws.sent, [b"%d" % i for i in range(50)])

    async def test_has_client_tracks_attach_and_detach(self):
        self.assertFalse(self.hub.has_client("c"))
        ch = self.hub.attach("c", FakeWS())
        self.assertTrue(self.hub.has_client("c"))
        self.hub.detach("c", ch)
        self.assertFalse(self.hub.has_client("c"))
        self.assertFalse(self.hub.has_client(None))
        self.hub.publish("c", ("k",), b"nobody home")    # must not raise

    async def test_a_dead_socket_closes_its_channel_quietly(self):
        ws = FakeWS(fail_after=1)
        ch = self.hub.attach("c", ws)
        ch.push(("a",), b"1"); ch.push(("b",), b"2")
        await settle(lambda: ch.closed)
        self.assertEqual(ws.sent, [b"1"])
        ch.push(("c",), b"3")    # pushing to a closed channel is a no-op, not an error

    async def test_publish_after_the_loop_is_gone_is_harmless(self):
        h = hub_mod.Hub()
        loop = asyncio.new_event_loop()
        h.bind(loop)
        h._channels["c"] = {object()}
        loop.close()
        h.publish("c", ("k",), b"x")


if __name__ == "__main__":
    unittest.main()
