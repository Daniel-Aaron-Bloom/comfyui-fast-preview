import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import wire  # noqa: E402


class Frames(unittest.TestCase):
    def test_roundtrip(self):
        meta = {"t": "abc", "s": 1, "i": 3, "l": 1, "c": "jpeg", "w": 640, "h": 480, "note": "naïve ☃"}
        payload = bytes(range(256)) * 10
        kind, got, body = wire.unpack(wire.pack_image(meta, payload))
        self.assertEqual((kind, got, body), (wire.KIND_IMAGE, meta, payload))

    def test_empty_payload(self):
        self.assertEqual(wire.unpack(wire.pack_image({}, b""))[2], b"")

    def test_header_layout_is_what_the_js_reads(self):
        frame = wire.pack_image({"a": 1}, b"XY")
        self.assertEqual(frame[:2], b"FP")
        self.assertEqual(frame[2], wire.VERSION)
        self.assertEqual(frame[3], wire.KIND_IMAGE)
        self.assertEqual(int.from_bytes(frame[4:8], "little"), len(b'{"a":1}'))
        self.assertEqual(frame[8:], b'{"a":1}XY')

    def test_rejects_garbage(self):
        good = wire.pack_image({"a": 1}, b"x")
        for bad in (b"", b"FP", b"XX" + good[2:], good[:2] + b"\x09" + good[3:], good[:9]):
            with self.assertRaises(ValueError, msg=bad[:12]):
                wire.unpack(bad)

    def test_control_is_compact_json(self):
        self.assertEqual(wire.control("gone", t="x"), '{"t":"x","op":"gone"}')

    def test_accepts_memoryview_and_bytearray(self):
        frame = wire.pack_image({"a": 1}, b"zz")
        self.assertEqual(wire.unpack(bytearray(frame))[2], b"zz")
        self.assertEqual(wire.unpack(memoryview(frame))[2], b"zz")


if __name__ == "__main__":
    unittest.main()
