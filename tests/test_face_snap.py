# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The precision brush's two ways of marking: by surface position (Precision,
a small Size and Hardness 100: a hard edge that follows the circle on any
tessellation) and Snap: Whole Faces (every CAD face the brush touches,
painted in full and kept in the mask by its face name, so it follows the
part through CAD changes as a picked face does). Data model, the agent's
texture_mask paint { snap }, the hover preview and the sidebar's row."""
import json
import os
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from texturizer import controls, draft, live, paint  # noqa: E402
from test_paint import BOX_FT, box  # noqa: E402

BOX = box()
SURF = paint.surface_for(BOX)
DOWN = [0.0, 0.0, -1.0]


def top(x, y):
    h = paint.ray_hit(BOX, [x, y, 30.0], [0, 0, -1])
    return dict(h, view=DOWN)


def face_of(t):
    return next(f for f, ts in BOX_FT.items() if t in ts)


class Model(unittest.TestCase):
    def test_snap_paints_the_face_under_the_brush_whole_by_name(self):
        d = draft.Draft("Body")
        br = paint.Brush(True, 4.0, 100.0, "precision", snap="faces")
        h = top(0, 0)
        paint.commit(d, SURF, 0, lambda st: paint.apply_stroke(st, [h], br, face_triangles=BOX_FT), BOX_FT)
        m = d.mask(0)
        self.assertEqual(m["faces"], [face_of(h["triangle"])])
        self.assertEqual((m["triangles"], m.get("split")), ([], None))

    def test_a_circle_over_an_edge_takes_both_faces(self):
        d = draft.Draft("Body")
        br = paint.Brush(True, 6.0, 100.0, "precision", snap="faces")
        h = dict(top(19.0, 0), view=[-0.7071, 0.0, -0.7071])   # 1 mm from the +X edge, seen from above that edge
        paint.commit(d, SURF, 0, lambda st: paint.apply_stroke(st, [h], br, face_triangles=BOX_FT), BOX_FT)
        side = paint.ray_hit(BOX, [40.0, 0, 10.0], [-1, 0, 0])
        self.assertEqual(sorted(d.mask(0)["faces"]), sorted({face_of(h["triangle"]), face_of(side["triangle"])}))

    def test_erase_snapped_takes_the_face_out(self):
        d = draft.Draft("Body")
        br = paint.Brush(True, 2.0, 100.0, "precision", snap="faces")
        h = top(0, 0)
        paint.commit(d, SURF, 0, lambda st: paint.apply_stroke(st, [h], br, face_triangles=BOX_FT), BOX_FT)
        paint.commit(d, SURF, 0, lambda st: paint.apply_stroke(st, [h], br, erase=True, face_triangles=BOX_FT), BOX_FT)
        self.assertEqual((d.mask(0)["faces"], d.mask(0)["triangles"]), ([], []))

    def test_snap_over_precision_paint_replaces_the_split(self):
        d = draft.Draft("Body")
        fine = paint.Brush(True, 1.0, 100.0, "precision")
        h = top(0, 0)
        paint.commit(d, SURF, 0, lambda st: paint.apply_stroke(st, [h], fine), BOX_FT)
        self.assertTrue(d.mask(0).get("split"))
        snap = paint.Brush(True, 1.0, 100.0, "precision", snap="faces")
        paint.commit(d, SURF, 0, lambda st: paint.apply_stroke(st, [h], snap, face_triangles=BOX_FT), BOX_FT)
        self.assertEqual(d.mask(0)["faces"], [face_of(h["triangle"])])
        self.assertFalse(d.mask(0).get("split"))

    def test_without_snap_a_small_hard_brush_paints_by_position(self):
        """Precision, Size 1, Hardness 100: split triangles, no soft paint, no whole face."""
        d = draft.Draft("Body")
        br = paint.Brush(True, 1.0, 100.0, "precision")
        paint.commit(d, SURF, 0, lambda st: paint.apply_stroke(st, [top(0, 0), top(3, 0)], br), BOX_FT)
        m = d.mask(0)
        self.assertTrue(m.get("split"))
        self.assertEqual((m["faces"], m.get("soft")), ([], None))

    def test_from_draft_and_hover(self):
        d = draft.Draft("Body")
        d.set("exclusionTool", "brush")
        self.assertIsNone(paint.Brush.from_draft(d).snap)
        d.set("brushFaceSnap", True)
        self.assertEqual(paint.Brush.from_draft(d).snap, "faces")
        tool = paint.PaintTool(d, lambda: (SURF, BOX_FT), lambda x, y: None)
        h = top(0, 0)
        self.assertEqual(tool.hover_faces(h, SURF, face_triangles=BOX_FT), sorted(BOX_FT[face_of(h["triangle"])]))

    def test_the_sidebar_row(self):
        row = controls.BY_KEY["brushFaceSnap"]
        self.assertEqual((row["origin"], row["scope"], row["default"], row["group"]), ("added", "ui", False, "brush"))
        self.assertEqual([o["label"] for o in row["options"]], ["Off", "Whole Faces"])


class Agent(unittest.TestCase):
    def setUp(self):
        from texturizer import handlers
        from test_wiring import Doc, FakeMesh, Obj

        live.reset()
        self.doc = Doc()
        self.body = Obj(self.doc, "Body", "Body", None)
        self.body.Mesh = object()
        self.doc.Objects.append(self.body)
        self.run = handlers.local_runner(handlers.Context(doc=self.doc, out_dir=tempfile.mkdtemp()))
        self._saved = (live.mesh_for, handlers._make_mesh, live.GUI)
        self.ft = BOX_FT
        live.mesh_for = lambda part, doc=None, tol=None: (BOX, self.ft)
        handlers._make_mesh = FakeMesh
        live.GUI = None
        self.h = handlers

    def tearDown(self):
        live.mesh_for, self.h._make_mesh, live.GUI = self._saved
        live.reset()

    def test_texture_mask_paint_snap(self):
        ok, out = self.run("texture_draft", {"part": "Body"})
        self.assertTrue(ok, out)
        h = top(0, 0)
        ok, out = self.run("texture_mask", {"paint": {"strokes": [[h]], "snap": "faces", "size": 2}})
        self.assertTrue(ok, out)
        self.assertEqual(live.draft().mask(0)["faces"], [face_of(h["triangle"])])
        ok, out = self.run("texture_mask", {"paint": {"strokes": [[h]], "snap": "edges"}})
        self.assertFalse(ok)
        self.ft = {}
        ok, out = self.run("texture_mask", {"paint": {"strokes": [[h]], "snap": "faces"}})
        self.assertFalse(ok)
        self.assertIn("faces", json.dumps(out))

    def test_the_tool_schema_lists_snap(self):
        with open(os.path.join(os.path.dirname(HERE), "texturizer", "data", "tools.json")) as fh:
            tools = json.load(fh)
        p = tools["entries"]["texture_mask"]["inputSchema"]["properties"]["paint"]
        self.assertEqual(p["properties"]["snap"]["enum"], ["faces", "off"])


if __name__ == "__main__":
    unittest.main()
