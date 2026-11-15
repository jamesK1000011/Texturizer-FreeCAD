# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""Dragging the projection (gizmo.py), as BumpMesh's Rotate rings and stamp
gestures: the rings' size, picking and swept angle, one undo step a drag;
Position on model places and drags, a press on the patch grabs it with its
offset, Surface normal follows the surface, the wheel resizes while armed
(Lock keeps V with U) and zooms again after a press elsewhere. A fake
camera that looks straight down: screen x, y are the document's."""
import importlib
import math
import os
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from test_paint import POS  # noqa: E402

from texturizer import draft, gizmo, preview, qt  # noqa: E402
from texturizer.engine import frame_matrix  # noqa: E402


def down(x, y):
    return (float(x), float(y), 50.0), (0.0, 0.0, -1.0)


class Math(unittest.TestCase):
    def test_euler_round_trip(self):
        rng = np.random.default_rng(3)
        for _ in range(50):
            e = rng.uniform(-170, 170, 3)
            e[1] = rng.uniform(-80, 80)
            got = gizmo.matrix_to_euler(frame_matrix(e.tolist()))
            np.testing.assert_allclose(frame_matrix(got), frame_matrix(e.tolist()), atol=1e-6)

    def test_turn_frame_turns_the_projection_about_the_document_axis(self):
        got = gizmo.turn_frame([20, 0, 0], "z", 30)
        np.testing.assert_allclose(frame_matrix(got), frame_matrix([20, 0, 0]) @ gizmo.axis_matrix("z", -30), atol=1e-9)

    def test_rings(self):
        c, r = gizmo.bounding_sphere(POS)
        P = POS.reshape(-1, 3)
        self.assertAlmostEqual(r, float(np.sqrt(((P - c) ** 2).sum(1)).max()))
        R = 10.0
        h = R * math.sqrt(0.5)
        self.assertEqual(gizmo.pick_ring(*down(h, h), (0, 0, 0), R), "z")     # on the Z ring, from above
        self.assertIsNone(gizmo.pick_ring(*down(0.3 * R, 0.2 * R), (0, 0, 0), R))
        self.assertEqual(gizmo.pick_ring(*down(0, 0.5 * R), (0, 0, 0), R), "x")   # the X ring seen edge-on
        self.assertAlmostEqual(gizmo.plane_angle(*down(0, 5), (0, 0, 0), "z"), math.pi / 2)
        self.assertIsNone(gizmo.plane_angle(*down(0, 5), (0, 0, 0), "x"))    # parallel to the ray
        self.assertAlmostEqual(gizmo.wrap(1.5 * math.pi), -0.5 * math.pi)

    def test_scale(self):
        self.assertEqual(gizmo.scale_by({"scaleU": 10, "scaleV": 4, "lockScale": True}, 2), {"scaleU": 20, "scaleV": 20})
        self.assertEqual(gizmo.scale_by({"scaleU": 10, "scaleV": 4}, 2, 0, 15), {"scaleU": 15, "scaleV": 8})


class Tool(unittest.TestCase):
    def setUp(self):
        self.d = draft.Draft("P")
        self.armed = []
        self.drawn = []
        self.t = gizmo.GizmoTool(self.d, lambda: POS, down, on_rings=self.drawn.append,
                                 on_armed=self.armed.append)

    def test_ring_drag_is_one_step(self):
        d, t = self.d, self.t
        d.set("rotate", True)
        g = t.rings()
        cx, cy = g["centre"][0], g["centre"][1]
        rr = g["radius"]
        at = lambda deg: (cx + rr * math.cos(math.radians(deg)), cy + rr * math.sin(math.radians(deg)))  # noqa: E731
        self.assertFalse(t.move(*at(45)))      # hover
        self.assertEqual(t.hover, "z")
        self.assertEqual(self.drawn[-1]["hover"], "z")
        self.assertTrue(t.button(gizmo.LEFT, True, *at(45)))
        for k in range(1, 10):
            self.assertTrue(t.move(*at(45 + 10 * k)))
        self.assertTrue(t.button(gizmo.LEFT, False, *at(135)))
        np.testing.assert_allclose(d.frame["rotation"], [0, 0, -90], atol=1e-6)
        self.assertEqual(len(d._undo), 1)
        d.undo()
        self.assertEqual(d.frame["rotation"], [0, 0, 0])
        self.assertFalse(t.button(gizmo.LEFT, True, cx + 0.3 * rr, cy + 0.2 * rr))   # off the rings: FreeCAD navigates

    def stamp(self, mode=0):
        self.d.update({"stampOnce": True, "mappingMode": mode, "scaleU": 6, "scaleV": 6})
        n = len(self.d._undo)
        return n

    def test_position_on_model_places_and_drags(self):
        d, t = self.d, self.t
        d.set("mappingMode", 0)
        self.assertTrue(t.arm_place())
        self.assertTrue(d.get("stampOnce"))          # Stamp mode switched on for it
        n = len(d._undo)
        self.assertTrue(t.button(gizmo.LEFT, True, 6, 7))
        self.assertAlmostEqual(d.get("stampCenterX"), 6)
        self.assertAlmostEqual(d.get("stampCenterY"), 7)
        self.assertEqual(d.get("stampSide"), 1)       # the face looks up: XY, top
        self.assertTrue(t.move(9, 8))
        self.assertTrue(t.button(gizmo.LEFT, False, 9, 8))
        self.assertAlmostEqual(d.get("stampCenterX"), 9)
        self.assertEqual(len(d._undo), n + 1)
        self.assertFalse(t.placing)
        self.assertEqual(self.armed, [True])

    def test_grab_keeps_the_offset_and_the_wheel_resizes(self):
        d, t = self.d, self.t
        self.stamp(0)
        d.update({"stampCenterX": 12.0, "stampCenterY": 12.0, "stampCenterZ": 0.0, "stampSide": 1})
        self.assertFalse(t.button(gizmo.LEFT, True, 2, 2))    # off the patch: orbit
        n = len(d._undo)
        self.assertTrue(t.button(gizmo.LEFT, True, 13, 12))   # on the patch, 1 mm right of the centre
        self.assertTrue(t.move(16, 15))
        t.button(gizmo.LEFT, False, 16, 15)
        self.assertAlmostEqual(d.get("stampCenterX"), 15)
        self.assertAlmostEqual(d.get("stampCenterY"), 15)
        self.assertTrue(t.armed)
        self.assertTrue(t.wheel(100))
        self.assertTrue(t.wheel(100))
        self.assertAlmostEqual(d.get("scaleU"), 6 * math.exp(-0.3), places=4)
        self.assertEqual(len(d._undo), n + 2)   # the drag, then the run of wheel steps
        t.button(3, True, 0, 0)                 # a right press: the wheel zooms again
        self.assertFalse(t.wheel(100))
        self.assertEqual(self.armed, [True, False])

    def test_surface_normal_follows_the_surface(self):
        d, t = self.d, self.t
        self.stamp(7)
        t.arm_place()
        t.button(gizmo.LEFT, True, 6, 6)
        t.move(10, 9)
        t.button(gizmo.LEFT, False, 10, 9)
        n = np.array([d.get(k) for k in ("stampNormalX", "stampNormalY", "stampNormalZ")])
        self.assertAlmostEqual(float(np.linalg.norm(n)), 1.0, places=5)
        self.assertGreater(n[2], 0.8)          # the bumpy sheet faces up
        self.assertLess(abs(d.get("stampCenterX") - 10), 0.6)

    def test_cylindrical_grab_turns_about_the_axis(self):
        f = {"mode": 3, "ccx": 0.0, "ccy": 0.0, "cx": 10.0, "cy": 0.0, "cz": 2.0}
        g = gizmo.grab(f, {"point": [10.0, 0.0, 0.0], "normal": [1, 0, 0]})
        up = gizmo.move_to({"point": [0.0, 7.0, 1.0]}, g)
        self.assertAlmostEqual(up["stampCenterX"], 0, places=9)
        self.assertAlmostEqual(up["stampCenterY"], 10)
        self.assertAlmostEqual(up["stampCenterZ"], 3)

    def test_tools_and_rotate_keep_the_left_button(self):
        d, t = self.d, self.t
        self.stamp(0)
        d.update({"stampCenterX": 12.0, "stampCenterY": 12.0, "stampCenterZ": 0.0})
        d.set("exclusionTool", "brush")
        self.assertFalse(t.button(gizmo.LEFT, True, 12, 12))   # the brush has it
        d.set("exclusionTool", None)
        t.cancel()
        self.assertFalse(t.wheel(100))


class Wiring(unittest.TestCase):
    def test_stamp_moves_rerun_from_the_map(self):
        for k in ("stampCenterX", "stampSide", "stampNormalZ"):
            self.assertEqual(preview.stage_for({"kind": "set", "key": k}), "map")

    def test_position_on_model_arms_the_view(self):
        """The sidebar's Position on model goes to the 3D view when there is
        one (BumpMesh's placement mode), on the stand-in Qt."""
        import _fakeqt
        from test_sidebar import fake_library

        from texturizer import gallery, sidebar

        saved = (qt.QtCore, qt.QtGui, qt.QtWidgets)
        qt.QtCore, qt.QtGui, qt.QtWidgets = _fakeqt.modules()
        try:
            importlib.reload(gallery)
            sb_mod = importlib.reload(sidebar)
            qt.app()
            calls, armed = [], []
            fav = gallery.Favourites(path=os.path.join(tempfile.mkdtemp(), "f.json"), entries=fake_library())
            sb = sb_mod.Sidebar(draft.Draft(), runner=lambda t, a: calls.append(t) or (True, {}),
                                list_textures=fake_library, favourites=fav, confirm=lambda: True)
            sb.press("stampPlace")
            self.assertEqual(calls, ["texture_set"])   # no view: the runner's pick
            sb.stamp_place = lambda: armed.append(1) or True
            sb.press("stampPlace")
            self.assertEqual((calls, armed), (["texture_set"], [1]))
            self.assertEqual(sb.status.text(), "Drag the stamp on the model to move it, then scroll to resize it.")
        finally:
            qt.QtCore, qt.QtGui, qt.QtWidgets = saved
            importlib.reload(gallery)
            importlib.reload(sidebar)


if __name__ == "__main__":
    unittest.main()
