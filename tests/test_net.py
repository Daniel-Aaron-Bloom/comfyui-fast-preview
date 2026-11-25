"""web/net.js: what a result holds while its image loads in two steps. Skipped without node."""

import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(__file__)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class Result(unittest.TestCase):
    def test_result_cases(self):
        done = subprocess.run(["node", os.path.join(HERE, "net_cases.mjs")], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
