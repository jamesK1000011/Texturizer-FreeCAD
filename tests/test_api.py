# SPDX-License-Identifier: AGPL-3.0-only
# Texturizer with no agent: its tool data, STL written and read back, and
# its API importing nothing of Agent Link in a fresh Python.
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from texturizer import mesh  # noqa: E402
from texturizer.refusal import Refused  # noqa: E402


class Api(unittest.TestCase):
    def test_stl_round_trip(self):
        tris = [[(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 0, 0), (0, 1, 0), (0, 0, 1)]]
        p = os.path.join(tempfile.mkdtemp(), "t.stl")
        mesh.write_stl(p, tris)
        got = mesh.read_stl(p)
        self.assertEqual(got.shape, (2, 3, 3))
        self.assertEqual(got[1][2].tolist(), [0.0, 0.0, 1.0])

    def test_check_refuses_a_missing_file(self):
        with self.assertRaises(Refused) as e:
            mesh.check(None, "/nonexistent.stl", "x.json")
        self.assertEqual(e.exception.code, "not_found")

    def test_api_imports_no_agent(self):
        code = "import sys; sys.path.insert(0, %r); import texturizer, texturizer.mesh; print(sorted(m for m in sys.modules if m.startswith('agentlink')))" % ROOT
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "[]")

    def test_tool_data(self):
        with open(os.path.join(ROOT, "texturizer", "data", "tools.json"), encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual(sorted(d["entries"]), ["texture_check", "texture_prepare"])
        try:
            from agentlink import tooldata
        except ImportError:
            self.skipTest("Agent Link is not beside it")
        self.assertEqual(tooldata.check(d), [])


if __name__ == "__main__":
    unittest.main()
