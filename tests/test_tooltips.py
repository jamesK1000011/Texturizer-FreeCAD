# SPDX-License-Identifier: AGPL-3.0-only
# Toolbar tooltips: each says what the button does, keeps BumpMesh's own
# sentence word for word where it adds something, ends each sentence before
# the credit, and keeps the credit (found by the GUI pass on the Mac).
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from texturizer import CREDIT, commands, controls  # noqa: E402


class Tooltips(unittest.TestCase):
    def test_each_says_more_than_its_name_and_keeps_the_credit(self):
        for _name, key in commands.HEADER:
            tip = commands.tooltip(key)
            body = tip[: -len(CREDIT)].strip()
            label = controls.BY_KEY[key]["label"].replace(" ⓘ", "")
            self.assertTrue(tip.endswith(CREDIT), key)
            self.assertNotEqual(body.rstrip(".").lower(), label.lower(), key)
            self.assertTrue(body.endswith((".", "!", "?")), (key, body))

    def test_bumpmesh_wording_kept(self):
        self.assertIn("Widely compatible with all slicers", commands.tooltip("exportStl"))


if __name__ == "__main__":
    unittest.main()
