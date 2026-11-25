"""web/protocol.js must read exactly what wire.py writes. Skipped without node."""

import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))

import wire  # noqa: E402


def _js(frames):
    done = subprocess.run(["node", os.path.join(HERE, "protocol_parity.mjs")],
                          input=json.dumps([f.hex() for f in frames]),
                          capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class Parity(unittest.TestCase):
    def test_what_python_writes_javascript_reads(self):
        cases = [
            ({"t": "abc123", "s": 1, "i": 3, "l": 1, "c": "jpeg", "w": 1280, "h": 720}, bytes(range(256)) * 40),
            ({"note": "naïve ☃ 日本語"}, b"\x00\xff\x00"),
            ({}, b""),
            ({"big": "x" * 70000}, b"payload"),     # a meta block well past one byte of length
        ]
        got = _js([wire.pack_image(m, p) for m, p in cases])
        for (meta, payload), parsed in zip(cases, got):
            self.assertEqual(parsed["kind"], wire.KIND_IMAGE)
            self.assertEqual(parsed["meta"], meta)
            self.assertEqual(bytes.fromhex(parsed["payload"]), payload)

    def test_javascript_refuses_what_it_should(self):
        good = wire.pack_image({"a": 1}, b"x")
        bad = [b"", b"FP", b"XX" + good[2:], good[:2] + b"\x09" + good[3:], good[:9],
               good[:8] + b"{not json}"[: len(good) - 8]]
        self.assertEqual(_js(bad), [None] * len(bad))


if __name__ == "__main__":
    unittest.main()
