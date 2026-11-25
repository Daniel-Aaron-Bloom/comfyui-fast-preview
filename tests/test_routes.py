import asyncio
import io
import json
import os
import sys
import unittest

import numpy as np
import torch
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
import helpers  # noqa: E402


class RouteCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pkg = helpers.load()
        self.pkg.state.hub.bind(asyncio.get_running_loop())
        app = web.Application()
        app.add_routes(self.pkg.server.routes)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.node = self.pkg.nodes.FastPreview()

    async def asyncTearDown(self):
        await self.client.close()

    async def run_node(self, *args, **kw):
        kw.setdefault("unique_id", "5")
        ui = (await asyncio.get_running_loop().run_in_executor(None, lambda: self.node.preview(*args, **kw)))["ui"]
        return ui, json.loads(ui["fast_preview"][0])["t"]

    async def collect(self, ws, until_op="end", timeout=5):
        got = []
        while True:
            msg = await asyncio.wait_for(ws.receive(), timeout)
            if msg.type == web.WSMsgType.TEXT:
                data = json.loads(msg.data)
                got.append(("text", data))
                if data["op"] == until_op:
                    return got
            elif msg.type == web.WSMsgType.BINARY:
                _, meta, payload = self.pkg.wire.unpack(msg.data)
                got.append(("img", dict(meta, payload=payload)))
            else:
                raise AssertionError("socket closed early: %r" % (msg,))


class Socket(RouteCase):
    async def test_hello_then_live_frames_over_a_real_websocket(self):
        ws = await self.client.ws_connect("/fastpreview/ws?clientId=client-1")
        self.assertEqual(json.loads((await ws.receive()).data), {"v": 1, "op": "hello"})
        ui, token = await self.run_node(helpers.split_image(800, 600), right=helpers.split_image(800, 600))
        got = await self.collect(ws)
        self.assertEqual(got[0][1]["op"], "begin")
        self.assertEqual(got[-1][1]["op"], "end")
        self.assertEqual(sum(k == "img" for k, _ in got), 2)    # a thumbnail per side: the exact pixels come over HTTP
        self.assertTrue(all(f["t"] == token for _, f in got))
        await ws.close()

    async def test_binary_frames_are_not_compressed_on_the_wire(self):
        ws = await self.client.ws_connect("/fastpreview/ws?clientId=client-1")
        # permessage-deflate must not have been negotiated
        self.assertNotIn("permessage-deflate", ws._response.headers.get("Sec-WebSocket-Extensions", ""))
        await ws.close()

    async def test_other_clients_do_not_get_my_frames(self):
        mine = await self.client.ws_connect("/fastpreview/ws?clientId=client-1")
        theirs = await self.client.ws_connect("/fastpreview/ws?clientId=someone-else")
        await mine.receive(); await theirs.receive()
        await self.run_node(helpers.split_image())
        await self.collect(mine)
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(theirs.receive(), 0.3)
        await mine.close(); await theirs.close()

    async def test_no_client_id_is_refused(self):
        ws = await self.client.ws_connect("/fastpreview/ws")
        msg = await ws.receive()
        self.assertEqual(msg.type, web.WSMsgType.CLOSE)
        self.assertEqual(msg.data, 4400)

    async def test_want_replays_a_remembered_result(self):
        ui, token = await self.run_node(helpers.split_image(800, 600), feed="off")   # nobody connected yet
        ws = await self.client.ws_connect("/fastpreview/ws?clientId=client-1")
        await ws.receive()    # hello
        await ws.send_str(json.dumps({"op": "want", "t": token}))
        got = await self.collect(ws)
        self.assertEqual([f.get("op") or f["l"] for _, f in got], ["begin", 0, "end"])    # the thumbnail
        self.assertTrue(got[0][1]["replay"])
        await ws.close()

    async def test_want_of_something_forgotten_says_gone(self):
        ws = await self.client.ws_connect("/fastpreview/ws?clientId=client-1")
        await ws.receive()
        await ws.send_str(json.dumps({"op": "want", "t": "deadbeef"}))
        self.assertEqual(json.loads((await ws.receive()).data), {"t": "deadbeef", "op": "gone"})
        await ws.close()

    async def test_garbage_from_the_client_is_ignored(self):
        ws = await self.client.ws_connect("/fastpreview/ws?clientId=client-1")
        await ws.receive()
        for junk in ("not json", "[]", '{"op": 3}', '{"op":"want"}', '{"op":"want","t":[1]}'):
            await ws.send_str(junk)
        await ws.send_str(json.dumps({"op": "want", "t": "zz"}))
        self.assertEqual(json.loads((await ws.receive()).data)["op"], "gone")    # still alive and answering
        await ws.close()


class Images(RouteCase):
    async def test_full_resolution_png_is_lossless(self):
        x = torch.rand(1, 90, 120, 3)
        _, token = await self.run_node(x)
        r = await self.client.get("/fastpreview/img", params={"t": token, "s": "l", "i": 0})
        self.assertEqual((r.status, r.content_type), (200, "image/png"))
        self.assertIn("immutable", r.headers["Cache-Control"])
        with Image.open(io.BytesIO(await r.read())) as img:
            np.testing.assert_array_equal(np.asarray(img), helpers.stock_u8(x[0]))

    async def test_b_side_and_batch_index(self):
        a, b = torch.rand(1, 20, 20, 3), torch.rand(3, 20, 20, 3)
        _, token = await self.run_node(a, right=b)
        r = await self.client.get("/fastpreview/img", params={"t": token, "s": "r", "i": 2})
        np.testing.assert_array_equal(np.asarray(Image.open(io.BytesIO(await r.read()))), helpers.stock_u8(b[2]))

    async def test_thumb_and_full_over_http(self):
        _, token = await self.run_node(helpers.split_image(2000, 1000))
        r = await self.client.get("/fastpreview/img", params={"t": token, "lod": 0})
        self.assertEqual((r.status, r.content_type), (200, "image/jpeg"))
        self.assertEqual(Image.open(io.BytesIO(await r.read())).size, (128, 64))
        r = await self.client.get("/fastpreview/img", params={"t": token, "lod": 1})
        self.assertEqual((r.status, r.content_type), (200, "image/png"))
        self.assertEqual(Image.open(io.BytesIO(await r.read())).size, (2000, 1000))
        self.assertEqual((await self.client.get("/fastpreview/img", params={"t": token, "lod": 2})).status, 400)

    async def test_download_is_named_and_smaller(self):
        _, token = await self.run_node(helpers.split_image(128, 128))    # flat colour: compresses well
        view = await (await self.client.get("/fastpreview/img", params={"t": token})).read()
        r = await self.client.get("/fastpreview/img", params={"t": token, "dl": 1})
        self.assertIn('attachment; filename="fast_preview_left_00001.png"', r.headers["Content-Disposition"])
        self.assertLess(len(await r.read()), len(view))    # level 3 vs level 0 on loopback

    async def test_through_a_proxy_the_client_is_not_local(self):
        _, token = await self.run_node(helpers.split_image(128, 128))
        local = await (await self.client.get("/fastpreview/img", params={"t": token})).read()
        proxied = await (await self.client.get("/fastpreview/img", params={"t": token},
                                               headers={"X-Forwarded-For": "203.0.113.9"})).read()
        self.assertLess(len(proxied), len(local))    # compressed: level 1, not 0

    async def test_a_burst_of_requests_encodes_once(self):
        _, token = await self.run_node(torch.rand(1, 256, 256, 3))
        entry = self.pkg.state.store.get(token)
        calls, real = [], entry.png

        def counting(*a):
            calls.append(a)
            return real(*a)
        entry.png = counting
        rs = await asyncio.gather(*[self.client.get("/fastpreview/img", params={"t": token}) for _ in range(6)])
        bodies = [await r.read() for r in rs]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len({len(b) for b in bodies}), 1)
        await self.client.get("/fastpreview/img", params={"t": token})
        self.assertEqual(len(calls), 1)    # and later ones come from the cache

    async def test_errors(self):
        _, token = await self.run_node(torch.rand(1, 16, 16, 3))
        get = lambda **p: self.client.get("/fastpreview/img", params=p)
        self.assertEqual((await get(t="nope")).status, 404)
        self.assertEqual((await get()).status, 404)
        self.assertEqual((await get(t=token, s="a")).status, 400)
        self.assertEqual((await get(t=token, i="x")).status, 400)
        self.assertEqual((await get(t=token, s="r")).status, 404)       # no right image was connected
        self.assertEqual((await get(t=token, i=1)).status, 404)         # batch of one
        self.assertEqual((await get(t=token, i=-1)).status, 404)

    async def test_pixels_that_were_never_kept_are_a_404_not_a_crash(self):
        _, token = await self.run_node(torch.rand(1, 16, 16, 3))
        entry = self.pkg.state.store.get(token)
        await asyncio.sleep(0.2)
        entry._u8.clear(); entry._pending.clear()
        self.assertEqual((await self.client.get("/fastpreview/img", params={"t": token})).status, 404)


if __name__ == "__main__":
    unittest.main()
