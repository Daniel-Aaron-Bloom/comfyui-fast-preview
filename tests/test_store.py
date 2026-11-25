import os
import sys
import threading
import unittest

import numpy as np
import torch
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
import helpers  # noqa: E402

pkg = helpers.load()
Entry, Store = pkg.store.Entry, pkg.store.Store
Encoded = pkg.pipeline.Encoded


def entry(token, kb=10, side_count=1):
    e = Entry(token, "1", [{"count": side_count, "w": 8, "h": 8, "c": 3}])
    e.put("preview", 0, 0, Encoded(b"x" * (kb * 1024), "jpeg", 8, 8))
    return e


class Eviction(unittest.TestCase):
    def test_oldest_goes_first_and_the_newest_stays(self):
        s = Store(max_bytes=25 * 1024)
        for t in "abc":
            s.add(entry(t))
        self.assertIsNone(s.get("a"))
        self.assertIsNotNone(s.get("b")); self.assertIsNotNone(s.get("c"))

    def test_reading_refreshes(self):
        s = Store(max_bytes=25 * 1024)
        s.add(entry("a")); s.add(entry("b"))
        s.get("a")
        s.add(entry("c"))
        self.assertIsNotNone(s.get("a")); self.assertIsNone(s.get("b"))

    def test_a_result_over_the_cap_is_still_kept(self):
        s = Store(max_bytes=1024)
        s.add(entry("big", kb=500))
        self.assertIsNotNone(s.get("big"))

    def test_growth_after_adding_triggers_eviction(self):
        s = Store(max_bytes=40 * 1024)
        s.add(entry("a", kb=10))
        late = entry("b", kb=10)
        s.add(late)
        late.put("thumb", 0, 0, Encoded(b"y" * 30 * 1024, "jpeg", 8, 8))   # now 50 KB total
        self.assertIsNone(s.get("a"))
        self.assertIsNotNone(s.get("b"))

    def test_png_cache_is_bounded_and_forgets_evicted_results(self):
        s = Store(max_bytes=1 << 30, png_bytes=25)
        s.add(entry("a"))
        for i in range(3):
            s.png_put(("a", 0, 0, i), b"p" * 10)
        self.assertIsNone(s.png_get(("a", 0, 0, 0)))
        self.assertEqual(s.png_get(("a", 0, 0, 2)), b"p" * 10)
        s2 = Store(max_bytes=1, png_bytes=1 << 20)
        s2.add(entry("a")); s2.png_put(("a", 0, 0, 0), b"p")
        s2.add(entry("b"))     # evicts a, and with it a's PNG
        self.assertIsNone(s2.png_get(("a", 0, 0, 0)))


class Pixels(unittest.TestCase):
    def test_u8_converts_lazily_once_and_matches_save_image(self):
        x = torch.rand(20, 30, 3)
        e = Entry("t", "1", [{"count": 1, "w": 30, "h": 20, "c": 3}])
        s = Store(); s.add(e)
        e.hold(0, 0, x)
        self.assertTrue(e.has_u8(0, 0))
        a = e.u8(0, 0)
        np.testing.assert_array_equal(a, helpers.stock_u8(x))
        self.assertIs(e.u8(0, 0), a)                       # cached, not redone
        self.assertEqual(e.nbytes, a.nbytes)
        self.assertEqual(e._pending, {})                    # the float tensor is let go

    def test_unknown_image_is_none(self):
        e = Entry("t", "1", [])
        self.assertIsNone(e.u8(0, 0)); self.assertIsNone(e.png(0, 0, 1))

    def test_concurrent_requests_convert_once(self):
        x = torch.rand(64, 64, 3)
        e = Entry("t", "1", [{"count": 1, "w": 64, "h": 64, "c": 3}])
        e.hold(0, 0, x)
        got = []
        threads = [threading.Thread(target=lambda: got.append(e.u8(0, 0))) for _ in range(8)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertTrue(all(g is got[0] for g in got))
        self.assertEqual(e.nbytes, got[0].nbytes)

    def test_png_embeds_workflow_text(self):
        import io
        e = Entry("t", "1", [{"count": 1, "w": 8, "h": 8, "c": 3}], png_text=lambda: {"prompt": "{}"})
        e.hold(0, 0, torch.rand(8, 8, 3))
        self.assertEqual(Image.open(io.BytesIO(e.png(0, 0, 1))).text, {"prompt": "{}"})

    def test_convert_all_goes_through_the_batch_in_order(self):
        e = Entry("t", "1", [{"count": 3, "w": 4, "h": 4, "c": 3}])
        for i in range(3):
            e.hold(0, i, torch.rand(4, 4, 3))
        e.convert_all()
        self.assertEqual(sorted(e._u8), [(0, 0), (0, 1), (0, 2)])


if __name__ == "__main__":
    unittest.main()
