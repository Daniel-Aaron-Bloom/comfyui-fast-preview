import io
import os
import sys
import unittest

import numpy as np
import torch
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
import helpers  # noqa: E402

pkg = helpers.load()
pipeline = pkg.pipeline


def decode(enc):
    img = Image.open(io.BytesIO(enc.data))
    img.load()
    return img


class Sizes(unittest.TestCase):
    def test_fit_never_upscales_and_keeps_aspect(self):
        self.assertEqual(pipeline.fit(512, 512, 1280), (512, 512))
        self.assertEqual(pipeline.fit(1280, 720, 1280), (1280, 720))
        self.assertEqual(pipeline.fit(4096, 2048, 1280), (1280, 640))
        self.assertEqual(pipeline.fit(2048, 4096, 1280), (640, 1280))
        self.assertEqual(pipeline.fit(10000, 3, 100), (100, 1))    # never collapses to 0

    def test_channels_normalised(self):
        for c, want in ((1, 3), (2, 4), (3, 3), (4, 4), (5, 3), (7, 3)):
            self.assertEqual(pipeline.as_image_batch(torch.rand(2, 5, 6, c)).shape, (2, 5, 6, want), c)
        with self.assertRaises(ValueError):
            pipeline.as_image_batch(torch.rand(5, 6, 3))
        with self.assertRaises(ValueError):
            pipeline.as_image_batch("not a tensor")


class Pixels(unittest.TestCase):
    def test_u8_is_exactly_what_save_image_writes(self):
        # out-of-range, exact .5 boundaries and values that round vs truncate differently
        x = torch.rand(3, 17, 19, 3) * 1.4 - 0.2
        x[0, 0, :4, 0] = torch.tensor([0.0, 1.0, 0.5, 254.99 / 255])
        for i in range(3):
            np.testing.assert_array_equal(pipeline.to_u8(x[i]).numpy(), helpers.stock_u8(x[i]))

    def test_u8_handles_half_precision(self):
        x = torch.rand(4, 4, 3).half()
        np.testing.assert_array_equal(pipeline.to_u8(x).numpy(), helpers.stock_u8(x.float()))

    def test_png_is_lossless_and_embeds_text(self):
        x = torch.rand(40, 50, 3)
        u8 = pipeline.to_u8(x).numpy()
        png = pipeline.encode_png(u8, level=1, text={"prompt": '{"a":1}', "workflow": "{}"})
        img = Image.open(io.BytesIO(png))
        np.testing.assert_array_equal(np.asarray(img), u8)
        self.assertEqual(img.text, {"prompt": '{"a":1}', "workflow": "{}"})
        self.assertEqual(Image.open(io.BytesIO(pipeline.encode_png(u8, level=0))).size, (50, 40))

    def test_png_keeps_alpha(self):
        u8 = pipeline.to_u8(torch.rand(8, 8, 4)).numpy()
        img = Image.open(io.BytesIO(pipeline.encode_png(u8)))
        self.assertEqual(img.mode, "RGBA")
        np.testing.assert_array_equal(np.asarray(img), u8)


class Encodes(unittest.TestCase):
    def test_thumb_is_small_and_keeps_aspect(self):
        enc = pipeline.make_thumb(helpers.split_image(2048, 1024)[0])
        self.assertEqual((enc.codec, enc.w, enc.h), ("jpeg", 64, 32))
        self.assertEqual(decode(enc).size, (64, 32))
        self.assertLess(len(enc.data), 2000)

    def test_thumb_of_a_tiny_image_is_not_upscaled(self):
        enc = pipeline.make_thumb(torch.rand(5, 7, 3))
        self.assertEqual((enc.w, enc.h), (7, 5))

    def test_preview_is_capped_and_oriented_and_coloured(self):
        enc = pipeline.make_preview(helpers.split_image(3000, 1500)[0], 1280, 85)
        self.assertEqual((enc.codec, enc.w, enc.h), ("jpeg", 1280, 640))
        arr = np.asarray(decode(enc).convert("RGB")).astype(int)
        left, right = arr[320, 100], arr[320, 1180]
        self.assertGreater(left[0], 200); self.assertLess(left[2], 60)       # red on the left
        self.assertGreater(right[2], 200); self.assertLess(right[0], 60)     # blue on the right

    def test_preview_of_a_small_image_keeps_its_size(self):
        enc = pipeline.make_preview(helpers.split_image(300, 200)[0], 1280, 85)
        self.assertEqual((enc.w, enc.h), (300, 200))

    def test_preview_tracks_the_source(self):
        # smooth content: a downscale should differ from the source by a few levels at most
        y, x = torch.meshgrid(torch.linspace(0, 1, 800), torch.linspace(0, 1, 1200), indexing="ij")
        img = torch.stack([x, y, (x + y) / 2], dim=-1)
        enc = pipeline.make_preview(img, 600, 92)
        ref = np.asarray(Image.fromarray(helpers.stock_u8(img)).resize((enc.w, enc.h), Image.Resampling.BOX)).astype(int)
        err = np.abs(np.asarray(decode(enc)).astype(int) - ref).mean()
        self.assertLess(err, 2.5)

    def test_alpha_goes_out_as_webp(self):
        enc = pipeline.make_preview(helpers.split_image(300, 200, channels=4)[0], 128, 85)
        self.assertEqual(enc.codec, "webp")
        self.assertEqual(decode(enc).mode, "RGBA")
        self.assertEqual(pipeline.make_thumb(helpers.split_image(300, 200, channels=4)[0]).codec, "webp")

    def test_non_float32_and_non_contiguous_inputs(self):
        base = helpers.split_image(400, 300)[0]
        for x in (base.half(), base.double(), base.permute(1, 0, 2).contiguous().permute(1, 0, 2)):
            self.assertEqual((pipeline.make_preview(x, 200, 80).w, pipeline.make_preview(x, 200, 80).h), (200, 150))

    def test_fallback_when_antialias_is_unsupported(self):
        orig = torch.nn.functional.interpolate

        def picky(*a, **kw):
            if kw.get("antialias"):
                raise NotImplementedError("no antialias here")
            return orig(*a, **kw)
        pipeline.F.interpolate = picky
        try:
            enc = pipeline.make_preview(helpers.split_image(400, 300)[0], 100, 80)
        finally:
            pipeline.F.interpolate = orig
        self.assertEqual((enc.w, enc.h), (100, 75))


if __name__ == "__main__":
    unittest.main()
