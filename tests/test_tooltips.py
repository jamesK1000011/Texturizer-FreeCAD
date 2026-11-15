# SPDX-License-Identifier: AGPL-3.0-only
# Toolbar tooltips: each says what the button does, keeps BumpMesh's own
# sentence word for word where it adds something, and carries no credit
# (macOS cut the long ones off at ~160 characters; the credit is in About,
# the sidebar's footer and the workbench's tooltip). Found by the GUI pass.
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from texturizer import CREDIT, commands, controls  # noqa: E402


class Tooltips(unittest.TestCase):
    def test_each_says_more_than_its_name_without_the_credit(self):
        for _name, key in commands.HEADER:
            tip = commands.tooltip(key)
            label = controls.BY_KEY[key]["label"].replace(" ⓘ", "")
            self.assertNotIn(CREDIT, tip, key)
            self.assertNotIn("Stefan Hermann", tip, key)
            self.assertLessEqual(len(tip), 160, (key, tip))
            self.assertNotEqual(tip.rstrip(".").lower(), label.lower(), key)
            self.assertTrue(tip.endswith((".", "!", "?")), (key, tip))

    def test_credit_stays_in_about_footer_and_workbench(self):
        import texturizer

        self.assertIn("Based on BumpMesh by Stefan Hermann (CNC Kitchen)", texturizer.about())
        self.assertIn("Stefan Hermann (CNC Kitchen)", texturizer.CREDIT)

    def test_bumpmesh_wording_kept(self):
        self.assertIn("Widely compatible with all slicers", commands.tooltip("exportStl"))

    def test_view_commands_say_what_they_do(self):
        tips = {name: tip for name, _label, tip in commands.VIEW}
        self.assertEqual(tips["Texturizer_Wireframe"], "Show the model as wireframe; press again for its normal look.")
        self.assertEqual(tips["Texturizer_Perspective"], "Switch between perspective and orthographic camera.")
        self.assertEqual(tips["Texturizer_Section"], "Cut the view with FreeCAD's clipping plane; press again to close it.")
        for tip in tips.values():
            self.assertNotIn("Std_", tip)  # no command ids in what the user reads


if __name__ == "__main__":
    unittest.main()
