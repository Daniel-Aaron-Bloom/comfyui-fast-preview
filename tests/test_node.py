import asyncio
import io
import json
import os
import sys
import unittest

import numpy as np
import torch
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
import helpers  # noqa: E402
from test_hub import FakeWS, settle  # noqa: E402


def frames_of(ws, pkg):
    """The socket's output as (kind, parsed): text frames as dicts, images as meta+payload."""
    out = []
    for item in ws.sent:
        if isinstance(item, str):
            out.append(("text", json.loads(item)))
        else:
            _, meta, payload = pkg.wire.unpack(item)
            out.append(("img", dict(meta, payload=payload)))
    return out


class NodeCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pkg = helpers.load()
        self.pkg.state.hub.bind(asyncio.get_running_loop())
        self.node = self.pkg.nodes.FastPreview()
        self.ws = FakeWS()
        self.channel = self.pkg.state.hub.attach("client-1", self.ws)

    async def asyncTearDown(self):
        self.pkg.state.hub.detach("client-1", self.channel)

    async def run_node(self, *args, **kw):
        """ComfyUI runs nodes on a worker thread, never on the server's loop."""
        kw.setdefault("unique_id", "12")
        return await asyncio.get_running_loop().run_in_executor(
            None, lambda: self.node.preview(*args, **kw))

    async def until_end(self):
        await settle(lambda: any(isinstance(s, str) and '"op":"end"' in s for s in self.ws.sent))
        return frames_of(self.ws, self.pkg)


class Output(NodeCase):
    async def test_ui_values_are_all_lists(self):
        # ComfyUI merges ui dicts with `[y for x in uis for y in x[k]]`: a dict value breaks
        for kw in ({}, {"right": helpers.split_image()}, {"feed": "full"}):
            ui = (await self.run_node(helpers.split_image(), **kw))["ui"]
            for key, value in ui.items():
                self.assertIsInstance(value, list, key)

    async def test_metadata_is_invisible_to_the_feed(self):
        ui = (await self.run_node(helpers.split_image()))["ui"]
        self.assertEqual(len(ui["fast_preview"]), 1)
        self.assertIsInstance(ui["fast_preview"][0], str)    # strings are skipped by the jobs summary
        info = json.loads(ui["fast_preview"][0])
        self.assertEqual(info["n"], "12")
        self.assertEqual(info["sides"], [{"count": 1, "w": 96, "h": 64, "c": 3}])

    async def test_feed_entries_are_real_temp_files(self):
        ui = (await self.run_node(helpers.split_image(300, 200), right=helpers.split_image(300, 200, batch=2)))["ui"]
        names = [im["filename"] for im in ui["images"]]
        self.assertEqual(len(names), 3)
        self.assertEqual([n.split("_")[-1][0] for n in names], ["l", "r", "r"])    # all of left, then right
        for im in ui["images"]:
            self.assertEqual((im["type"], im["subfolder"]), ("temp", ""))
            path = os.path.join(self.pkg.temp_dir, im["filename"])
            with Image.open(path) as img:
                self.assertEqual(img.size, (300, 200))

    async def test_feed_off_writes_nothing(self):
        ui = (await self.run_node(helpers.split_image(), feed="off"))["ui"]
        self.assertNotIn("images", ui)
        self.assertEqual(os.listdir(self.pkg.temp_dir), [])

    async def test_feed_full_is_a_lossless_png_with_the_workflow(self):
        x = torch.rand(1, 40, 50, 3)
        ui = (await self.run_node(x, feed="full", prompt={"1": {"class_type": "X"}},
                                  extra_pnginfo={"workflow": {"id": "w"}}))["ui"]
        self.assertEqual(ui["images"][0]["filename"][-4:], ".png")
        with Image.open(os.path.join(self.pkg.temp_dir, ui["images"][0]["filename"])) as img:
            np.testing.assert_array_equal(np.asarray(img), helpers.stock_u8(x[0]))
            self.assertEqual(json.loads(img.text["prompt"]), {"1": {"class_type": "X"}})
            self.assertEqual(json.loads(img.text["workflow"]), {"id": "w"})

    async def test_no_browser_and_no_feed_means_nothing_to_do(self):
        self.pkg.server.client_id = None
        self.assertEqual(await self.run_node(helpers.split_image(), feed="off"), {"ui": {}})

    async def test_api_run_still_writes_the_feed_but_remembers_nothing(self):
        self.pkg.server.client_id = None
        ui = (await self.run_node(helpers.split_image()))["ui"]
        self.assertEqual(len(ui["images"]), 1)
        self.assertEqual(self.pkg.state.store.nbytes, 0)

    async def test_empty_batch_is_a_no_op(self):
        self.assertEqual(await self.run_node(torch.zeros(0, 8, 8, 3)), {"ui": {}})

    async def test_input_spec(self):
        spec = self.pkg.nodes.FastPreview.INPUT_TYPES()
        self.assertEqual(list(spec["required"]), ["left"])
        self.assertEqual(list(spec["optional"]), ["right", "feed"])    # no size, no quality: nothing to tune
        self.assertTrue(spec["optional"]["feed"][1]["advanced"])
        self.assertTrue(self.pkg.nodes.FastPreview.OUTPUT_NODE)


class Live(NodeCase):
    async def test_frames_come_in_the_promised_order(self):
        await self.run_node(helpers.split_image(2000, 1000), right=helpers.split_image(2000, 1000))
        frames = await self.until_end()
        kinds = [(k, f.get("op") or ("thumb" if f["l"] == 0 else "full") + "-" + "lr"[f["s"]]) for k, f in frames]
        self.assertEqual(kinds, [("text", "begin"), ("img", "thumb-l"), ("img", "thumb-r"), ("text", "end")])

    async def test_begin_describes_the_result(self):
        await self.run_node(helpers.split_image(300, 200), right=helpers.split_image(600, 400, batch=3),
                            extra_pnginfo={"workflow": {"id": "wf-9"}})
        begin = (await self.until_end())[0][1]
        self.assertEqual((begin["op"], begin["n"], begin["wf"], begin["replay"]), ("begin", "12", "wf-9", False))
        self.assertEqual([(s["count"], s["w"], s["h"]) for s in begin["sides"]], [(1, 300, 200), (3, 600, 400)])

    async def test_every_image_frame_decodes_to_what_it_claims(self):
        await self.run_node(helpers.split_image(1500, 1000), right=helpers.split_image(1500, 1000, batch=2))
        imgs = [f for k, f in await self.until_end() if k == "img"]
        self.assertEqual(len(imgs), 1 + 2)    # one thumbnail each for A: 1 and B: 2; the exact pixels come over HTTP
        for f in imgs:
            self.assertEqual(Image.open(io.BytesIO(f["payload"])).size, (f["w"], f["h"]))
            self.assertEqual((f["c"], f["l"]), ("jpeg", 0))
            self.assertLessEqual(max(f["w"], f["h"]), 128)
        self.assertEqual({(f["s"], f["i"]) for f in imgs}, {(0, 0), (1, 0), (1, 1)})

    async def test_one_token_ties_the_frames_together(self):
        ui = (await self.run_node(helpers.split_image()))["ui"]
        token = json.loads(ui["fast_preview"][0])["t"]
        for kind, f in await self.until_end():
            self.assertEqual(f["t"], token)

    async def test_nothing_is_pushed_when_no_browser_is_listening(self):
        self.pkg.state.hub.detach("client-1", self.channel)
        ui = (await self.run_node(helpers.split_image()))["ui"]
        await asyncio.sleep(0.1)
        self.assertEqual(self.ws.sent, [])
        self.assertIsNotNone(self.pkg.state.store.get(json.loads(ui["fast_preview"][0])["t"]))    # still replayable
        self.channel = self.pkg.state.hub.attach("client-1", self.ws)

    async def test_pixels_are_kept_for_zoom_and_save(self):
        x = torch.rand(1, 64, 64, 3)
        ui = (await self.run_node(x))["ui"]
        entry = self.pkg.state.store.get(json.loads(ui["fast_preview"][0])["t"])
        await settle(lambda: entry.u8(0, 0) is not None)
        np.testing.assert_array_equal(entry.u8(0, 0), helpers.stock_u8(x[0]))

    async def test_a_failing_thumbnail_does_not_fail_the_node_or_strand_the_client(self):
        def boom(*a, **k):
            raise RuntimeError("encoder exploded")
        self.pkg.pipeline.make_thumb = boom
        with self.assertLogs(self.pkg.nodes.log, level="WARNING"):
            ui = (await self.run_node(helpers.split_image(), feed="off"))["ui"]
            frames = await self.until_end()    # `end` still arrives, so the UI never waits forever
        self.assertEqual([k for k, f in frames if k == "img"], [])
        self.assertIn("fast_preview", ui)

    async def test_a_failing_encode_is_an_error_when_the_feed_needs_it(self):
        def boom(*a, **k):
            raise RuntimeError("nope")
        self.pkg.pipeline.make_preview = boom
        with self.assertRaises(RuntimeError):
            await self.run_node(helpers.split_image(), feed="preview")

    async def test_rerunning_a_node_supersedes_what_was_still_queued(self):
        slow = FakeWS(delay=0.15)
        self.pkg.state.hub.detach("client-1", self.channel)
        self.channel = self.pkg.state.hub.attach("client-1", slow)
        await self.run_node(helpers.split_image(), feed="off")
        await self.run_node(helpers.split_image(), feed="off")
        await settle(lambda: sum(isinstance(s, str) and '"op":"end"' in s for s in slow.sent) >= 1, timeout=5)
        begins = [json.loads(s) for s in slow.sent if isinstance(s, str) and '"op":"begin"' in s]
        self.assertGreaterEqual(len(begins), 1)    # the stale first result may be skipped entirely
        self.assertEqual(begins[-1]["t"], json.loads(slow.sent[-1])["t"] if '"op":"end"' in slow.sent[-1] else begins[-1]["t"])


if __name__ == "__main__":
    unittest.main()
