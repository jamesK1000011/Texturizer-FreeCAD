# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The projection gizmo (gizmo.py, drawn by gizmoview.py with Coin like the
rings): with Rotate on, on a Planar, Triplanar or Cubic layer, an arrow along
the projection (drag its tip: the frame turns the least that points the
projection there), a dial for Rotation and a knob for Size; each drag one
undo step. Headless: texture_set { frame: { direction } } with rotation and
scaleU / scaleV in settings."""
import math
import os
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from texturizer import draft, gizmo, live  # noqa: E402
from texturizer.engine import frame_matrix  # noqa: E402
from test_paint import POS  # noqa: E402


def down(x, y):
    return (float(x), float(y), 80.0), (0.0, 0.0, -1.0)


class Math(unittest.TestCase):
    def test_point_projection_turns_the_axis_there(self):
        for mode, n in ((0, 2), (1, 1), (2, 0), (5, 2), (6, 2)):
            for rot in ([0, 0, 0], [20, -35, 70]):
                for want in ([0, 0, 1], [1, 2, 3], [0, -1, 0], [-0.3, 0.1, -1]):
                    got = frame_matrix(gizmo.point_projection(rot, mode, want))[n]
                    np.testing.assert_allclose(got, np.asarray(want) / np.linalg.norm(want), atol=1e-6)

    def test_the_least_turn(self):
        """Pointing a planar XY projection along X turns it about Y alone."""
        np.testing.assert_allclose(gizmo.point_projection([0, 0, 0], 0, [1, 0, 0]), [0, -90, 0], atol=1e-6)
        np.testing.assert_allclose(frame_matrix(gizmo.point_projection([0, 0, 0], 0, [0, 0, -1]))[2], [0, 0, -1], atol=1e-9)

    def test_applies(self):
        self.assertTrue(gizmo.projection_applies({"mappingMode": 0}))
        self.assertTrue(gizmo.projection_applies({"mappingMode": 5}))
        self.assertFalse(gizmo.projection_applies({"mappingMode": 3}))   # cylindrical: its own controls
        self.assertFalse(gizmo.projection_applies({"mappingMode": 0, "stampOnce": True, "stampCenterX": 1}))

    def test_widget(self):
        w = gizmo.projection_widget({"mappingMode": 1, "rotation": 90, "scaleU": 4, "scaleV": 2}, [0, 0, 0], (1, 2, 3), 10)
        np.testing.assert_allclose(w["normal"], [0, 1, 0])
        np.testing.assert_allclose(w["arrow"], [1, 2 + 9.5, 3])
        np.testing.assert_allclose(w["tu"], [0, 0, -1], atol=1e-12)       # U turned by -90 deg in the XZ plane
        self.assertEqual(len(w["tile"]), 4)
        self.assertAlmostEqual(float(np.linalg.norm(w["tile"][1] - w["tile"][0])), 4.0)
        self.assertIsNone(gizmo.projection_widget({"mappingMode": 5}, [0, 0, 0], (0, 0, 0), 10)["arrow"])
        self.assertEqual(len(gizmo.dial_points(w)), gizmo.RING_SEGMENTS + 1)


class Tool(unittest.TestCase):
    def setUp(self):
        self.d = draft.Draft("P")
        self.drawn = []
        self.t = gizmo.GizmoTool(self.d, lambda: POS, down, on_projection=self.drawn.append)
        self.d.update({"mappingMode": 0, "scaleU": 5.0, "scaleV": 5.0, "lockScale": True, "rotation": 0})
        self.d.set("rotate", True)
        self.w = self.t.projection()
        self.t.draw()

    def drag(self, a, b, steps=6):
        self.assertTrue(self.t.button(gizmo.LEFT, True, *a))
        for k in range(1, steps + 1):
            f = k / steps
            self.assertTrue(self.t.move(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
        self.assertTrue(self.t.button(gizmo.LEFT, False, *b))

    def test_shown_with_rotate_on_planar(self):
        self.assertIsNotNone(self.w)
        self.assertIsNotNone(self.drawn[-1])
        self.d.set("rotate", False)
        self.t.draw()
        self.assertIsNone(self.drawn[-1])
        self.d.set("rotate", True)
        self.d.set("mappingMode", 3)
        self.t.draw()
        self.assertIsNone(self.drawn[-1])

    def test_arrow_drag_points_the_projection(self):
        c, tip = self.w["centre"], self.w["arrow"]
        self.assertFalse(self.t.move(tip[0], tip[1]))
        self.assertEqual(self.t.phover, "direction")
        n = len(self.d._undo)
        r = float(np.linalg.norm(tip - c))
        self.drag((tip[0], tip[1]), (c[0] + 0.5 * r, c[1]))
        self.assertEqual(len(self.d._undo), n + 1)                       # one undo step a drag
        q = gizmo._on_sphere(*down(c[0] + 0.5 * r, c[1]), c, r)
        np.testing.assert_allclose(frame_matrix(self.d.frame["rotation"])[2], (q - c) / r, atol=1e-6)
        self.d.undo()
        self.assertEqual(self.d.frame["rotation"], [0, 0, 0])

    def test_dial_drag_sets_rotation(self):
        c, R = self.w["centre"], self.w["dial_radius"]
        self.drag((c[0] + R, c[1]), (c[0], c[1] + R), steps=9)          # a quarter turn, counter-clockwise from above
        self.assertAlmostEqual(self.d.get("rotation"), 270.0, places=3)
        w = self.t.projection()
        np.testing.assert_allclose(w["dial"], c + np.array([0, R, 0]), atol=1e-6)   # the knob followed the cursor

    def test_size_knob_scales_both_with_lock(self):
        c, k = self.w["centre"], self.w["scale"]
        far = c + 2 * (k - c)
        self.drag((k[0], k[1]), (far[0], far[1]))
        self.assertAlmostEqual(self.d.get("scaleU"), 10.0, places=6)
        self.assertAlmostEqual(self.d.get("scaleV"), 10.0, places=6)

    def test_a_press_elsewhere_navigates(self):
        c = self.w["centre"]
        r = self.w["radius"] * gizmo.RING_SCALE
        self.assertFalse(self.t.button(gizmo.LEFT, True, c[0] + 0.3 * r, c[1] - 0.2 * r))


class _Field:
    def __init__(self):
        self.v = None

    def setValue(self, *a):  # noqa: N802
        self.v = a

    def setValues(self, start, n, vals):  # noqa: N802
        self.v = list(vals)[:n]

    def setNum(self, n):  # noqa: N802
        self.n = n


class _Node:
    CIRCLE_FILLED_9_9 = 7

    def __init__(self):
        self.kids = []
        self.whichChild = None
        for f in ("lineWidth", "pointSize", "markerIndex"):
            setattr(self, f, None)
        for f in ("rgb", "point", "numVertices", "numPoints"):
            setattr(self, f, _Field())

    def addChild(self, n):  # noqa: N802
        self.kids.append(n)


class Draw(unittest.TestCase):
    def test_coin_drawing_on_stand_ins(self):
        """gizmoview draws the handles (Coin stand-ins): lines, knobs, hide."""
        import types

        from texturizer import gizmoview

        coin = types.SimpleNamespace(**{k: type(k, (_Node,), {}) for k in (
            "SoSwitch", "SoAnnotation", "SoDrawStyle", "SoSeparator", "SoBaseColor", "SoCoordinate3", "SoLineSet",
            "SoMarkerSet")})
        pivy = types.ModuleType("pivy")
        pivy.coin = coin
        saved = sys.modules.get("pivy"), sys.modules.get("pivy.coin")
        sys.modules["pivy"], sys.modules["pivy.coin"] = pivy, coin
        try:
            graph = _Node()
            gv = types.SimpleNamespace(view=types.SimpleNamespace(getSceneGraph=lambda: graph), proj=None)
            w = gizmo.projection_widget({"mappingMode": 0, "scaleU": 4, "scaleV": 4}, [0, 0, 0], (0, 0, 0), 10)
            w.update(hover="rotation", drag=None)
            gizmoview.GizmoView._draw_projection(gv, w)
            self.assertEqual(gv.proj.whichChild, 0)
            sep, col, pts, ls, knob = gv._parts["rotation"]
            self.assertEqual(ls.numVertices.v, (gizmo.RING_SEGMENTS + 1,))
            self.assertEqual(col.rgb.v, gizmo.PROJ_HOVER["rotation"])
            self.assertEqual(knob[1].numPoints.v, (1,))
            self.assertEqual(gv._parts["tile"][3].numVertices.v, (5,))
            gizmoview.GizmoView._draw_projection(gv, None)
            self.assertEqual(gv.proj.whichChild, -1)
        finally:
            for k, v in zip(("pivy", "pivy.coin"), saved):
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v


class Agent(unittest.TestCase):
    def setUp(self):
        from texturizer import handlers
        from test_wiring import Doc, FakeMesh, Obj

        live.reset()
        self.doc = Doc()
        self.body = Obj(self.doc, "Scan", "Scan", None)
        self.body.Mesh = object()
        self.doc.Objects.append(self.body)
        self.run = handlers.local_runner(handlers.Context(doc=self.doc, out_dir=tempfile.mkdtemp()))
        self._saved = (live.mesh_for, handlers._make_mesh, live.GUI)
        live.mesh_for = lambda part, doc=None, tol=None: (POS, {})
        handlers._make_mesh = FakeMesh
        live.GUI = None
        self.h = handlers

    def tearDown(self):
        live.mesh_for, self.h._make_mesh, live.GUI = self._saved
        live.reset()

    def test_texture_set_frame_direction(self):
        ok, out = self.run("texture_draft", {"part": "Scan"})
        self.assertTrue(ok, out)
        ok, out = self.run("texture_set", {"settings": {"mappingMode": 1, "rotation": 30, "scaleU": 3},
                                           "frame": {"direction": [1, 1, 0]}})
        self.assertTrue(ok, out)
        d = live.draft()
        np.testing.assert_allclose(frame_matrix(d.frame["rotation"])[1], [math.sqrt(0.5), math.sqrt(0.5), 0], atol=1e-6)
        self.assertEqual((d.get("rotation"), d.get("scaleU")), (30, 3))
        for bad in ([0, 0, 0], [1, 2], "up"):
            ok, out = self.run("texture_set", {"frame": {"direction": bad}})
            self.assertFalse(ok, bad)


if __name__ == "__main__":
    unittest.main()
