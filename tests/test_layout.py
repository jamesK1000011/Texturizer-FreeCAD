# SPDX-License-Identifier: AGPL-3.0-only
# The panel layout setting (layout.py, the same in every add-on of the
# suite), without a window: read with its default, and which panels show
# per workbench.
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from texturizer import layout  # noqa: E402


class Params:
    def __init__(self, **kw):
        self.v = dict(kw)

    def GetString(self, k, d=""):  # noqa: N802
        return self.v.get(k, d)


class Setting(unittest.TestCase):
    def test_default_and_values(self):
        self.assertEqual(layout.read(Params()), "left-tab")
        self.assertEqual(layout.read(Params(PanelLayout="sideways")), "left-tab")
        for v in ("right", "left-tab", "left-slide"):
            self.assertEqual(layout.read(Params(PanelLayout=v)), v)
        self.assertEqual(layout.PARAMS, "User parameter:BaseApp/Preferences/Mod/AgentLink")

    def test_caps(self):
        self.assertEqual(layout.TREE_MAX, 300)
        self.assertEqual(layout.TASK_MAX, 360)
        self.assertEqual(layout.SLIDE_MAX, 300)
        self.assertTrue(layout.is_combo("Std_ComboView"))  # FreeCAD 1.x's Combo View

    def test_only_the_active_workbenchs_panel(self):
        panels = {"own": "TexturizerWorkbench", "other": "SomeOtherWorkbench", "hub": layout.EVERYWHERE}
        self.assertEqual(layout.shown("TexturizerWorkbench", panels), ["own", "hub"])
        self.assertEqual(layout.shown("SomeOtherWorkbench", panels), ["other", "hub"])

    def test_alone_its_own_placer(self):
        self.assertIs(layout.placer(), layout.own())


class TexturePanel(unittest.TestCase):
    """With Agent Link's placer (service panel_layout) the Texture panel is
    Texturizer's own dock, not a tab of Agent Link's panel."""

    def test_own_dock_when_agent_link_places_panels(self):
        import importlib

        try:
            import FreeCAD  # noqa: F401  (the texture tools need it)
            from agentlink import hooks
        except ImportError:
            self.skipTest("FreeCAD and Agent Link are not both here")
        import texturizer

        state, was = hooks.saved(), getattr(texturizer, "_hosted", False)
        try:
            hooks.reset()
            hooks.provide(layout.SERVICE, object())
            fam = importlib.import_module("texturizer.agentlink_families")
            importlib.reload(fam)
            self.assertNotIn("Texture", [t["title"] for t in hooks.TABS])
            hooks.reset()
            importlib.reload(fam)  # an Agent Link that cannot place a dock: the tab
            self.assertIn("Texture", [t["title"] for t in hooks.TABS])
        finally:
            hooks.restore(state)
            texturizer._hosted = was


class QuietLoad(unittest.TestCase):
    """Loading the selection as the workbench opens is the window's own
    doing: under Agent Link it goes quiet (nothing shown, nothing in
    Activity); a click is not quiet."""

    def test_quiet_only_when_asked_and_supported(self):
        from texturizer import panel

        seen = []

        def caller(tool, args, quiet=False):
            seen.append((tool, quiet))
            return True, {}

        caller.quiet_ok = True
        was = panel.HOST_CALL
        panel.HOST_CALL = caller
        try:
            panel.run("texture_draft", {"load": "selection"}, quiet=True)
            panel.run("texture_set", {})
        finally:
            panel.HOST_CALL = was
        self.assertEqual(seen, [("texture_draft", True), ("texture_set", False)])


if __name__ == "__main__":
    unittest.main()
