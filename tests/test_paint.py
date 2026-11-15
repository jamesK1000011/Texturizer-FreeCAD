# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The mask brush (paint.py): Circle and Single, erase, soft coverage at
Hardness 50, Fill, each against BumpMesh's own paintTree.js and main.js run
in node (tests/tools/paint_js.mjs); one undo step per stroke; a painted
mask through .bumpmesh and back (and through BumpMesh's own paint.json
code); viewer events with a fake pick; texture_mask paint and mesh fill."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))

import gen_controls  # noqa: E402
from texturizer import draft, live, paint, project  # noqa: E402

SRC = os.environ.get("BUMPMESH_SRC", gen_controls.DEFAULT_SRC)
HAVE_JS = os.path.exists(os.path.join(SRC, "js", "paintTree.js")) and shutil.which("node")
PAINT_JS = os.path.join(HERE, "tools", "paint_js.mjs")
SKIP_JS = "node or the BumpMesh checkout ($BUMPMESH_SRC) is missing"
DOWN = [0.0, 0.0, -1.0]


def run_js(cases):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "cases.json")
        with open(path, "w") as fh:
            json.dump(cases, fh)
        r = subprocess.run(["node", PAINT_JS, SRC, path], capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise AssertionError(r.stderr[-2000:])
    return json.loads(r.stdout)


def grid(n=12, cell=2.0):
    """A bumpy sheet facing +Z, two triangles a cell, plus a wall down its far edge."""
    def z(x, y):
        return 0.8 * np.sin(x / 4.0) * np.cos(y / 5.0)
    tris = []
    for i in range(n):
        for j in range(n):
            x0, y0, x1, y1 = i * cell, j * cell, (i + 1) * cell, (j + 1) * cell
            a, b, c, d = ([x0, y0, z(x0, y0)], [x1, y0, z(x1, y0)], [x1, y1, z(x1, y1)], [x0, y1, z(x0, y1)])
            tris += [[a, b, c], [a, c, d]]
    X = n * cell
    for j in range(n):   # the wall: faces +X, side-on to a view straight down
        y0, y1 = j * cell, (j + 1) * cell
        a, b, c, d = [X, y0, z(X, y0)], [X, y1, z(X, y1)], [X, y1, -5.0], [X, y0, -5.0]
        tris += [[a, d, c], [a, c, b]]
    return np.asarray(tris, np.float32).reshape(-1)


POS = grid()
SURF = paint.Surface(POS)


def hit_at(x, y, view=DOWN):
    h = paint.ray_hit(POS, [x, y, 20.0], [0, 0, -1])
    assert h is not None, (x, y)
    return dict(h, view=list(view))


def run_py(ops, positions=POS):
    s = paint.Surface(positions)
    st = paint.State(s)
    snaps = []
    for op in ops:
        if op["kind"] == "snapshot":
            snaps.append({"hard": st.hard.tolist(), "paint": st.corners().tolist()})
        elif op["kind"] == "fill":
            paint.fill(st, op["seed"], op["maxAngle"], op.get("erase", False))
        else:
            b = paint.Brush(op["circle"], op["size"], op["hardness"])
            paint.apply_stroke(st, op["hits"], b, op.get("erase", False))
    return snaps, st


def stroke(hits, circle=True, size=10.0, hardness=50.0, erase=False):
    return {"kind": "stroke", "hits": hits, "circle": circle, "size": size, "hardness": hardness, "erase": erase}


SNAP = {"kind": "snapshot"}
CASES = {
    "single vs circle": [stroke([hit_at(11.3, 9.1)], circle=False), SNAP,
                         stroke([hit_at(11.3, 9.1)], hardness=100), SNAP],
    "hard drag with a break": [stroke([hit_at(3, 3), hit_at(6, 4), None, hit_at(16, 16), hit_at(19, 13)],
                                      size=4, hardness=100), SNAP],
    "soft at 50": [stroke([hit_at(12.2, 12.7)], size=10, hardness=50), SNAP,
                   stroke([hit_at(5, 18), hit_at(9, 20), hit_at(13, 21)], size=6, hardness=50), SNAP],
    "erase": [stroke([hit_at(12, 12)], size=14, hardness=100), SNAP,
              stroke([hit_at(12, 12)], size=6, hardness=100, erase=True), SNAP,
              stroke([hit_at(8, 12)], size=6, hardness=50, erase=True), SNAP,
              stroke([hit_at(12, 17.5)], circle=False, erase=True), SNAP],
    "soft at the wall": [stroke([hit_at(23.5, 12)], size=8, hardness=30), SNAP],
    "fill": [{"kind": "fill", "seed": 5, "maxAngle": 30}, SNAP,    # the sheet, not the wall
             {"kind": "fill", "seed": 300, "maxAngle": 30}, SNAP,
             {"kind": "fill", "seed": 5, "maxAngle": 30, "erase": True}, SNAP],
}


class Brush(unittest.TestCase):
    def test_single_is_the_triangle_circle_reaches_out(self):
        snaps, _ = run_py(CASES["single vs circle"])
        t = hit_at(11.3, 9.1)["triangle"]
        self.assertEqual(np.nonzero(snaps[0]["hard"])[0].tolist(), [t])
        got = set(np.nonzero(snaps[1]["hard"])[0].tolist())
        self.assertIn(t, got)
        self.assertGreater(len(got), 10)
        self.assertFalse(got & set(range(2 * 144, SURF.n)))   # the side-on wall is never reached
        c = SURF.positions.reshape(-1, 3, 3).mean(axis=1)[sorted(got)]
        self.assertLess(np.hypot(c[:, 0] - 11.3, c[:, 1] - 9.1).max(), 5 + 2 * 1.5)

    def test_soft_coverage_follows_brush_coverage(self):
        snaps, st = run_py(CASES["soft at 50"][:2])
        self.assertFalse(any(snaps[0]["hard"]))
        h = hit_at(12.2, 12.7)
        for v in range(SURF.vert_count):
            x, y, _z = SURF.V[v]
            d = np.hypot(x - h["point"][0], y - h["point"][1])
            want = paint.brush_coverage(d, 5, 0.5) if d <= 5 else 0
            if st.cov[v] or want:
                self.assertAlmostEqual(float(st.cov[v]), want, places=6)
        self.assertEqual(float(st.cov.max()), 1.0)

    def test_brush_limits(self):
        b = paint.Brush(True, 500, 120)
        self.assertEqual((b.size, b.hardness), (100, 100))
        self.assertEqual(paint.Brush(True, 0, -1).size, 0.2)
        with self.assertRaises(ValueError):
            paint.Brush(mode="precision")

    @unittest.skipUnless(HAVE_JS, SKIP_JS)
    def test_against_bumpmesh(self):
        names = list(CASES)
        js = run_js([{"positions": POS.tolist(), "ops": CASES[k]} for k in names])
        for k, want in zip(names, js):
            got, _ = run_py(CASES[k])
            self.assertEqual(len(got), len(want), k)
            for i, (g, w) in enumerate(zip(got, want)):
                with self.subTest(case=k, snapshot=i):
                    self.assertEqual(g["hard"], w["hard"])
                    np.testing.assert_allclose(g["paint"], w["paint"], atol=1e-6)


class Drafted(unittest.TestCase):
    def test_one_undo_step_per_stroke(self):
        d = draft.Draft()
        b = paint.Brush(True, 6, 100)
        paint.commit(d, SURF, 0, lambda st: paint.apply_stroke(st, [hit_at(5, 5), hit_at(8, 5)], b))
        one = json.loads(json.dumps(d.mask()))
        paint.commit(d, SURF, 0, lambda st: paint.apply_stroke(st, [hit_at(18, 18)], paint.Brush(True, 6, 50)))
        self.assertNotIn("soft", one)
        self.assertIn("soft", d.mask())   # a soft stroke paints coverage, no hard triangles
        self.assertEqual(d.mask()["triangles"], one["triangles"])
        d.undo()
        self.assertEqual(d.mask(), one)
        d.undo()
        self.assertEqual(d.mask(), {"faces": [], "triangles": []})
        d.redo()
        self.assertEqual(d.mask(), one)

    def test_a_face_the_eraser_cuts_becomes_triangles(self):
        d = draft.Draft()
        ft = {"Face1": list(range(0, 24)), "Face2": list(range(200, 210))}
        d.mask_add(faces=["Face1", "Face2"])
        paint.commit(d, SURF, 0, lambda st: paint.paint_faces(st, [3], erase=True), ft)
        m = d.mask()
        self.assertEqual(m["faces"], ["Face2"])
        self.assertEqual(m["triangles"], [t for t in range(24) if t != 3])

    def test_round_trip_through_bumpmesh_project(self):
        d = draft.Draft()
        d.add_layer()
        b = paint.Brush(True, 8, 40)
        paint.commit(d, SURF, 0, lambda st: paint.apply_stroke(st, [hit_at(6, 6), hit_at(10, 8)], b))
        paint.commit(d, SURF, 1, lambda st: paint.apply_stroke(st, [hit_at(16, 16)], paint.Brush(True, 6, 100)))
        paint.commit(d, SURF, 1, lambda st: paint.apply_stroke(st, [hit_at(19, 16)], b))
        before = [json.loads(json.dumps(L["mask"])) for L in d.layers]
        self.assertTrue(before[0].get("soft") and before[1]["triangles"])
        model = POS.reshape(-1, 3, 3)
        files = project.build(d, model=model)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.bumpmesh")
            project.save(path, d, model=model)
            back = draft.Draft()
            project.load(path, back)
        self.assertEqual([L["mask"] for L in back.layers], before)
        if HAVE_JS:   # BumpMesh's own importProject path: PaintTree.fromJSON + deserialize
            pj = json.loads(files["paint.json"])
            got = run_js([{"positions": POS.tolist(), "ops": [{"kind": "load", "json": pj}, SNAP]}])[0][0]
            st = paint.state_from_mask(SURF, before[0])
            self.assertEqual(got["hard"], st.hard.tolist())
            np.testing.assert_allclose(got["paint"], st.corners(), atol=1e-7)

    @unittest.skipUnless(HAVE_JS, SKIP_JS)
    def test_bumpmesh_soft_paint_json_reads_here(self):
        ops = CASES["soft at 50"] + [{"kind": "save"}]
        js = run_js([{"positions": POS.tolist(), "ops": ops}])[0]
        pj = js[-1]["json"]
        soft = project.decode_soft(pj)[1]
        tris = project.decode_paint(pj)[1]
        st = paint.state_from_mask(SURF, {"triangles": tris, "soft": soft})
        self.assertEqual(st.hard.tolist(), js[1]["hard"])
        np.testing.assert_allclose(st.corners(), js[1]["paint"], atol=1e-7)


class Engine(unittest.TestCase):
    def test_soft_paint_reaches_the_engine_and_quick_look(self):
        from texturizer import engine, window

        m = {"triangles": [1], "soft": {"corners": [0, 7], "values": [0.25, 0.5]}}
        L = engine._resolve_masks([{"mask": m, "includeOnly": False}], {})[0]
        p = engine._mask_dict(L["mask"], 4)["paint"]
        np.testing.assert_allclose(p, [0.25, 0, 0, 1, 1, 1, 0, 0.5, 0, 0, 0, 0])
        tri = np.arange(12).reshape(4, 3)
        W = window.layer_weights(4, tri, 12, [{"mask": m}, {"mask": m, "includeOnly": True}], {})
        np.testing.assert_allclose(W[:, 0], 1 - p)
        np.testing.assert_allclose(W[:, 1], p)


class Events(unittest.TestCase):
    """PaintTool with a fake pick: left paints, middle / right and misses navigate."""

    def setUp(self):
        self.d = draft.Draft()
        self.cursor = []
        self.at = {}
        self.tool = paint.PaintTool(self.d, lambda: (SURF, {}), lambda x, y: self.at.get((x, y)),
                                    on_cursor=lambda h, b, e: self.cursor.append((h is not None, e)))
        self.at.update({(1, 1): hit_at(5, 5), (2, 1): hit_at(7, 5), (3, 1): hit_at(9, 6)})

    def test_drag_is_one_stroke(self):
        d, t = self.d, self.tool
        self.assertFalse(t.button(paint.LEFT, True, 1, 1))   # no tool chosen
        d.set("exclusionTool", "brush")
        d.set("brushHardness", 100)
        self.assertFalse(t.button(paint.MIDDLE, True, 1, 1))
        self.assertFalse(t.button(paint.RIGHT, True, 1, 1))
        self.assertFalse(t.button(paint.LEFT, True, 9, 9))   # a miss: the drag orbits
        self.assertFalse(t.move(1, 1))                       # hover: the circle only
        self.assertEqual(self.cursor[-1], (True, False))
        self.assertTrue(t.button(paint.LEFT, True, 1, 1))
        self.assertTrue(t.move(2, 1))
        self.assertTrue(t.move(9, 9))                        # off the model mid-stroke
        self.assertTrue(t.move(3, 1))
        self.assertTrue(t.button(paint.LEFT, False, 3, 1))
        self.assertFalse(t.button(paint.LEFT, False, 3, 1))
        want = paint.State(SURF)
        paint.apply_stroke(want, [hit_at(5, 5), hit_at(7, 5), None, hit_at(9, 6)], paint.Brush(True, 10, 100))
        self.assertEqual(d.mask()["triangles"], np.nonzero(want.hard)[0].tolist())
        self.assertEqual(len(d._undo), 1)
        self.assertTrue(t.button(paint.LEFT, True, 2, 1, shift=True))   # Shift erases
        self.assertTrue(t.button(paint.LEFT, False, 2, 1))
        self.assertEqual(len(d._undo), 2)
        self.assertLess(len(d.mask()["triangles"]), int(want.hard.sum()))
        d.undo()
        self.assertEqual(d.mask()["triangles"], np.nonzero(want.hard)[0].tolist())

    def test_fill_click(self):
        self.d.set("exclusionTool", "bucket")
        self.d.set("bucketThreshold", 30)
        self.assertTrue(self.tool.button(paint.LEFT, True, 1, 1))
        st = paint.fill(paint.State(SURF), hit_at(5, 5)["triangle"], 30)
        self.assertEqual(int(st.hard.sum()), 288)
        self.assertEqual(self.d.mask()["triangles"], np.nonzero(st.hard)[0].tolist())
        self.assertFalse(self.tool.move(2, 1))

    def test_pick_mapping(self):
        parent = np.array([4, 4, 7, 9])
        self.assertEqual(paint.pick_hit(2, (1, 2, 3), (0, 0, 1), parent, SURF.n)["triangle"], 7)
        self.assertIsNone(paint.pick_hit(8, (1, 2, 3), None, parent))
        self.assertEqual(paint.pick_hit(5, (1, 2, 3), None)["triangle"], 5)
        np.testing.assert_allclose(paint.view_dir(True, None, (0, 0, -2), (1, 1, 1)), DOWN)
        np.testing.assert_allclose(paint.view_dir(False, (0, 0, 10), None, (0, 0, 0)), DOWN)
        h = paint.ray_hit(POS, [5, 5, 20], [0, 0, -1])
        self.assertGreater(h["normal"][2], 0)
        self.assertIsNone(paint.ray_hit(POS, [5, 5, -20], [0, 0, 1]))   # only back faces that way


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

    def test_texture_mask_paint_and_fill(self):
        ok, out = self.run("texture_draft", {"part": "Scan"})
        self.assertTrue(ok, out)
        d = live.draft()
        a, b = hit_at(5, 5), hit_at(8, 6)
        ok, out = self.run("texture_mask", {"paint": {"strokes": [[a, b], [hit_at(18, 18)]], "size": 6, "hardness": 100}})
        self.assertTrue(ok, out)
        self.assertEqual(out["strokes"], 2)
        want = paint.State(SURF)
        for s_ in ([a, b], [hit_at(18, 18)]):
            paint.apply_stroke(want, s_, paint.Brush(True, 6, 100))
        self.assertEqual(d.mask()["triangles"], np.nonzero(want.hard)[0].tolist())
        n = len(d._undo)
        ok, out = self.run("texture_mask", {"paint": {"strokes": [[{"point": a["point"]}]], "erase": True, "size": 3}})
        self.assertTrue(ok, out)
        self.assertEqual(len(d._undo), n + 1)
        ok, out = self.run("texture_mask", {"paint": {"strokes": [[{"point": [1, 2]}]]}})
        self.assertFalse(ok)
        ok, out = self.run("texture_mask", {"paint": {"strokes": [[a]], "mode": "precision"}})
        self.assertFalse(ok)
        ok, out = self.run("texture_mask", {"clear": True, "fill": {"triangles": [5], "maxAngle": 30}})
        self.assertTrue(ok, out)
        st = paint.fill(paint.State(SURF), 5, 30)
        self.assertEqual(d.mask()["triangles"], np.nonzero(st.hard)[0].tolist())
        ok, out = self.run("texture_mask", {"fill": {"faces": ["Face1"]}})
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
