"""The download-name template (web/filename.js). Skipped without node."""

import json
import os
import re
import shutil
import subprocess
import unittest

HERE = os.path.dirname(__file__)
CTX = {"side": "left", "index": 3, "width": 2048, "height": 1024, "node": "12", "title": "My Preview"}


def run(cases):
    done = subprocess.run(["node", os.path.join(HERE, "filename_cases.mjs")], input=json.dumps(cases),
                          capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def name(template, **kw):
    return run([dict(template=template, ctx=kw.pop("ctx", CTX), **kw)])[0]


@unittest.skipUnless(shutil.which("node"), "node not installed")
class Names(unittest.TestCase):
    def test_default_template(self):
        self.assertEqual(name("fast_preview_%side%_%index:5%"), "fast_preview_left_00003.png")
        self.assertEqual(name("fast_preview_%side%_%index:5%", ctx=dict(CTX, side="right", index=12)),
                         "fast_preview_right_00012.png")

    def test_index_padding(self):
        self.assertEqual(name("%index%"), "3.png")
        self.assertEqual(name("%index:1%"), "3.png")
        self.assertEqual(name("%index:3%"), "003.png")
        self.assertEqual(name("%index:x%"), "3.png")                      # not a number: no padding
        self.assertEqual(len(name("%index:999%")), 20 + len("3.png") - 1)  # padding is capped

    def test_simple_tokens(self):
        self.assertEqual(name("%width%x%height%_%node%_%title%"), "2048x1024_12_My Preview.png")

    def test_tokens_are_case_insensitive_and_text_is_kept(self):
        self.assertEqual(name("Shot-%SIDE%-%Index:2%-final"), "Shot-left-03-final.png")

    def test_unknown_tokens_are_left_as_written(self):
        self.assertEqual(name("a_%bogus%_b_%side%"), "a_%bogus%_b_left.png")
        self.assertEqual(name("100%_%side%"), "100%_left.png")            # a lone percent sign is just a character

    def test_dates(self):
        now = [2026, 10, 24, 9, 5, 7]                                      # 24 November 2026, 09:05:07 (months count from 0)
        self.assertEqual(name("%date%", now=now), "2026-11-24.png")
        self.assertEqual(name("%date:yyyyMMdd-hhmmss%", now=now), "20261124-090507.png")
        self.assertEqual(name("%date:yy.MM.dd%", now=now), "26.11.24.png")
        self.assertEqual(name("%date:dd/MM%", now=now), "24_11.png")      # the slash is not allowed in a name

    def test_random_has_the_length_asked_for(self):
        for n, want in (("8", 8), ("1", 1), ("64", 64), ("0", 1), ("999", 64), ("", 6), ("x", 6)):
            got = name("%%random:%s%%" % n if n != "" else "%random%")
            self.assertEqual(len(got), want + 4, (n, got))
            self.assertRegex(got, r"^[a-z0-9]+\.png$")
        self.assertEqual(len(name("%random%")), 6 + 4)

    def test_random_is_random_each_time_even_within_one_name(self):
        got = name("%random:16%-%random:16%")
        a, b = got[:-4].split("-")
        self.assertNotEqual(a, b)
        self.assertNotEqual(name("%random:16%"), name("%random:16%"))

    def test_random_uses_every_character_equally(self):
        counts = {}
        for ch in run([dict(randomChars=100000)])[0]:
            counts[ch] = counts.get(ch, 0) + 1
        self.assertEqual(len(counts), 36)
        expected = 100000 / 36
        self.assertTrue(all(abs(c - expected) < expected * 0.1 for c in counts.values()), counts)

    def test_random_skips_biased_bytes(self):
        # 252..255 would make a-d (bytes mod 36) likelier than the rest, so they are thrown away.
        # What is left: 0 -> a, 1 -> b, 35 -> 9 (the last of 26 letters + 10 digits), 36 wraps to a.
        self.assertEqual(run([dict(randomChars=4, bytes=[255, 254, 253, 252, 0, 1, 35, 36])])[0], "ab9a")

    def test_illegal_characters_become_underscores(self):
        self.assertEqual(name('a/b\\c:d*e?f"g<h>i|j'), "a_b_c_d_e_f_g_h_i_j.png")
        self.assertEqual(name("%title%", ctx=dict(CTX, title="../../etc/passwd")), "_.._etc_passwd.png")
        self.assertEqual(name("x\x00y\ty"), "x_y_y.png")

    def test_png_is_added_once(self):
        self.assertEqual(name("shot.png"), "shot.png")
        self.assertEqual(name("shot.PNG"), "shot.png")
        self.assertEqual(name("shot.jpg"), "shot.jpg.png")

    def test_nothing_usable_falls_back(self):
        for t in ("", "   ", "...", " . ", "%title%"):
            self.assertEqual(name(t, ctx=dict(CTX, title="")), "fast_preview.png", t)

    def test_leading_and_trailing_dots_and_spaces(self):
        self.assertEqual(name("..hidden"), "hidden.png")
        self.assertEqual(name("name. . "), "name.png")                     # Windows would drop them anyway

    def test_long_names_are_cut(self):
        got = name("x" * 400)
        self.assertEqual(len(got), 150 + 4)
        self.assertTrue(got.endswith(".png"))

    def test_unicode_survives(self):
        self.assertEqual(name("写真_%side%"), "写真_left.png")


if __name__ == "__main__":
    unittest.main()
