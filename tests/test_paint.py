# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The mask brush (paint.py, painttree.py): Circle and Single, Standard and
Precision (the split tree), erase, soft coverage at Hardness 50, Fill, each
against BumpMesh's own paintTree.js and main.js run in node
(tests/tools/paint_js.mjs) as the flattened mesh and its paint; one undo
step per stroke; a painted mask through .bumpmesh and back (and through
BumpMesh's own paint.json code); split masks in the engine; viewer events
with a fake pick; texture_mask paint and mesh fill."""
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
from texturizer import draft, live, paint, painttree, project  # noqa: E402

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


def box(sx=40.0, sy=30.0, sz=20.0):
    """A coarse body: a box of 12 triangles, two per face, as a CAD box tessellates."""
    def v(i):
        return [(-sx / 2, sx / 2)[i & 1], (-sy / 2, sy / 2)[(i >> 1) & 1], (0.0, sz)[(i >> 2) & 1]]
    tris = []
    for a, b, c, d in ((4, 5, 7, 6), (0, 2, 3, 1), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)):
        tris += [[v(a), v(b), v(c)], [v(a), v(c), v(d)]]
    return np.asarray(tris, np.float32).reshape(-1)


BOX = box()
BOX_FT = {f"Face{k + 1}": [2 * k, 2 * k + 1] for k in range(6)}


def box_drag(n=31):
    """The live test's drag: across the top face from (-12, -8) to (12, 8), looking down."""
    out = []
    for p in np.linspace([-12, -8, 20], [12, 8, 20], n):
        h = paint.ray_hit(BOX, p + [0, 0, 10], [0, 0, -1])
        out.append(dict(h, view=list(DOWN)))
    return out


def hit_at(x, y, view=DOWN):
    h = paint.ray_hit(POS, [x, y, 20.0], [0, 0, -1])
    assert h is not None, (x, y)
    return dict(h, view=list(view))


def snapshot(tree):
    flat = tree.flatten()
    p, h = tree.flat_paint(0, flat)
    return {"hard": h.tolist(), "paint": p.tolist(), "positions": flat["positions"].tolist(),
            "parent": flat["faceParentId"].tolist()}


def run_py(ops, positions=POS):
    s = paint.Surface(positions)
    st = paint.State(s)
    snaps = []
    for op in ops:
        if op["kind"] == "snapshot":
            snaps.append(snapshot(st.tree))
        elif op["kind"] == "fill":
            paint.fill(st, op["seed"], op["maxAngle"], op.get("erase", False))
        else:
            b = paint.Brush(op["circle"], op["size"], op["hardness"], "precision" if op["precision"] else "standard")
            paint.apply_stroke(st, op["hits"], b, op.get("erase", False))
    return snaps, st


def stroke(hits, circle=True, size=10.0, hardness=50.0, erase=False, precision=True):
    return {"kind": "stroke", "hits": hits, "circle": circle, "size": size, "hardness": hardness, "erase": erase,
            "precision": precision}


SNAP = {"kind": "snapshot"}
CASES = {
    "single vs circle": [stroke([hit_at(11.3, 9.1)], circle=False), SNAP,
                         stroke([hit_at(11.3, 9.1)], hardness=100, precision=False), SNAP],
    "standard ignores hardness": [stroke([hit_at(12.2, 12.7)], size=10, hardness=30, precision=False), SNAP],
    "precision hard edge": [stroke([hit_at(12.2, 12.7)], size=7, hardness=100), SNAP,
                            stroke([hit_at(14.5, 12.7)], size=3, hardness=100, erase=True), SNAP,
                            stroke([hit_at(12.2, 12.7)], size=12, hardness=100, erase=True), SNAP],
    "hard drag with a break": [stroke([hit_at(3, 3), hit_at(6, 4), None, hit_at(16, 16), hit_at(19, 13)],
                                      size=4, hardness=100), SNAP,
                               stroke([hit_at(3, 3), hit_at(6, 4)], size=4, hardness=100, precision=False), SNAP],
    "soft at 50": [stroke([hit_at(12.2, 12.7)], size=10, hardness=50), SNAP,
                   stroke([hit_at(5, 18), hit_at(9, 20), hit_at(13, 21)], size=6, hardness=50), SNAP],
    "erase": [stroke([hit_at(12, 12)], size=14, hardness=100), SNAP,
              stroke([hit_at(12, 12)], size=6, hardness=100, erase=True), SNAP,
              stroke([hit_at(8, 12)], size=6, hardness=50, erase=True), SNAP,
              stroke([hit_at(12, 17.5)], circle=False, erase=True), SNAP,
              stroke([hit_at(12, 12)], size=20, hardness=100, precision=False, erase=True), SNAP],
    "soft at the wall": [stroke([hit_at(23.5, 12)], size=8, hardness=30), SNAP],
    "fill": [{"kind": "fill", "seed": 5, "maxAngle": 30}, SNAP,    # the sheet, not the wall
             {"kind": "fill", "seed": 300, "maxAngle": 30}, SNAP,
             {"kind": "fill", "seed": 5, "maxAngle": 30, "erase": True}, SNAP],
}


class Brush(unittest.TestCase):
    def test_single_is_the_triangle_circle_reaches_out(self):
        snaps, st = run_py(CASES["single vs circle"])
        t = hit_at(11.3, 9.1)["triangle"]
        self.assertEqual(np.nonzero(snaps[0]["hard"])[0].tolist(), [t])
        self.assertFalse(st.split)   # Standard paints whole triangles
        got = set(np.nonzero(snaps[1]["hard"])[0].tolist())
        self.assertIn(t, got)
        self.assertGreater(len(got), 10)
        self.assertFalse(got & set(range(2 * 144, SURF.n)))   # the side-on wall is never reached
        c = SURF.positions.reshape(-1, 3, 3).mean(axis=1)[sorted(got)]
        self.assertLess(np.hypot(c[:, 0] - 11.3, c[:, 1] - 9.1).max(), 5 + 2 * 1.5)

    def test_soft_coverage_follows_brush_coverage(self):
        snaps, st = run_py(CASES["soft at 50"][:2])
        self.assertFalse(any(snaps[0]["hard"]))
        self.assertTrue(st.split)   # Precision refines under the rim
        h = hit_at(12.2, 12.7)
        t = st.tree
        cov = t.layers[0].cov
        for v in range(t.vert_count):
            if v >= SURF.vert_count and t.mid_ref[v] == 0:
                continue   # a midpoint merged away
            d = np.hypot(t.vx[v] - h["point"][0], t.vy[v] - h["point"][1])
            want = paint.brush_coverage(d, 5, 0.5) if d <= 5 else 0
            if cov[v] or want:
                self.assertAlmostEqual(float(cov[v]), want, places=6)
        self.assertEqual(max(cov), 1.0)

    def test_precision_follows_the_circle(self):
        """A hard Precision dab: the painted leaves' centroids lie inside the
        circle and the unpainted ones outside, to the edge limit (r / 5)."""
        snaps, st = run_py(CASES["precision hard edge"][:2])
        g = snaps[0]
        h = hit_at(12.2, 12.7)
        c = np.asarray(g["positions"]).reshape(-1, 3, 3).mean(axis=1)
        d = np.hypot(c[:, 0] - h["point"][0], c[:, 1] - h["point"][1])
        on = np.asarray(g["hard"], bool)
        self.assertLessEqual(d[on].max(), 3.5)
        self.assertGreaterEqual(d[~on & (d < 6)].min(), 3.5 - 0.7 / 2)
        self.assertGreater(len(on), SURF.n)   # split along the rim only, merged inside
        self.assertLess(len(on), SURF.n * 4)

    def test_brush_limits(self):
        b = paint.Brush(True, 500, 120)
        self.assertEqual((b.size, b.hardness), (100, 100))
        self.assertEqual(paint.Brush(True, 0, -1).size, 0.2)
        self.assertEqual((paint.Brush().mode, paint.Brush(hardness=40).h), ("precision", 0.4))
        self.assertEqual(paint.Brush(hardness=40, mode="standard").h, 1.0)   # Standard: no Hardness
        with self.assertRaises(ValueError):
            paint.Brush(mode="soft")

    @unittest.skipUnless(HAVE_JS, SKIP_JS)
    def test_against_bumpmesh(self):
        names = list(CASES)
        js = run_js([{"positions": POS.tolist(), "ops": CASES[k]} for k in names])
        for k, want in zip(names, js):
            got, _ = run_py(CASES[k])
            self.assertEqual(len(got), len(want), k)
            for i, (g, w) in enumerate(zip(got, want)):
                with self.subTest(case=k, snapshot=i):
                    self.assertEqual(g["parent"], w["parent"])
                    np.testing.assert_array_equal(g["positions"], np.asarray(w["positions"], np.float32))
                    self.assertEqual(g["hard"], w["hard"])
                    np.testing.assert_allclose(g["paint"], w["paint"], atol=1e-6)


class Drafted(unittest.TestCase):
    def test_one_undo_step_per_stroke(self):
        d = draft.Draft()
        b = paint.Brush(True, 6, 100, "standard")
        paint.commit(d, SURF, 0, lambda st: paint.apply_stroke(st, [hit_at(5, 5), hit_at(8, 5)], b))
        one = json.loads(json.dumps(d.mask()))
        paint.commit(d, SURF, 0, lambda st: paint.apply_stroke(st, [hit_at(18, 18)], paint.Brush(True, 6, 50)))
        self.assertNotIn("soft", one)
        self.assertNotIn("split", one)
        self.assertIn("soft", d.mask())   # a soft stroke paints coverage, no hard triangles
        self.assertIn("split", d.mask())  # Precision split the triangles under its rim
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

    def round_trip(self, standard):
        d = draft.Draft()
        d.add_layer()
        mode = "standard" if standard else "precision"
        b = paint.Brush(True, 8, 40, mode)
        paint.commit(d, SURF, 0, lambda st: paint.apply_stroke(st, [hit_at(6, 6), hit_at(10, 8)], b))
        paint.commit(d, SURF, 1, lambda st: paint.apply_stroke(st, [hit_at(16, 16)], paint.Brush(True, 6, 100, mode)))
        paint.commit(d, SURF, 1, lambda st: paint.apply_stroke(st, [hit_at(19, 16)], b))
        before = [json.loads(json.dumps(L["mask"])) for L in d.layers]
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
            t = painttree.PaintTree(SURF)   # the same file read here: BumpMesh's shared tree
            t.add_layer(1)
            self.assertTrue(t.deserialize(painttree.from_json(pj)))
            want = snapshot(t)
            self.assertEqual(got["parent"], want["parent"])
            self.assertEqual(got["hard"], want["hard"])
            np.testing.assert_allclose(got["paint"], want["paint"], atol=1e-7)
        return before

    def test_round_trip_through_bumpmesh_project(self):
        before = self.round_trip(standard=True)   # whole triangles: BumpMesh's unsplit paint.json
        self.assertTrue(before[1]["triangles"] and not any(m.get("split") for m in before))
        before = self.round_trip(standard=False)  # Precision: the split tree
        self.assertTrue(before[0].get("soft") and before[0].get("split") and before[1].get("split"))

    @unittest.skipUnless(HAVE_JS, SKIP_JS)
    def test_bumpmesh_soft_paint_json_reads_here(self):
        ops = CASES["soft at 50"] + [{"kind": "save"}]
        js = run_js([{"positions": POS.tolist(), "ops": ops}])[0]
        pj = js[-1]["json"]
        m = project.decode_split(pj, POS.reshape(-1, 3, 3))[1]
        got = snapshot(paint.state_from_mask(SURF, m).tree)
        self.assertEqual(got["parent"], js[1]["parent"])
        self.assertEqual(got["hard"], js[1]["hard"])
        np.testing.assert_allclose(got["paint"], js[1]["paint"], atol=1e-7)
        ops = CASES["soft at 50"][:1] + [{"kind": "save"}]   # an unsplit standard paint.json still reads
        pj = run_js([{"positions": POS.tolist(), "ops": [dict(ops[0], precision=False), {"kind": "save"}]}])[0][-1]["json"]
        self.assertIsNone(project.decode_split(pj, POS.reshape(-1, 3, 3)))
        self.assertTrue(project.decode_paint(pj)[1])


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


class CoarseBody(unittest.TestCase):
    """The Precision brush on a 12-triangle box: a band under the stroke, and
    the Quick look (3D Preview off) drawing it on the tree's leaves."""

    def test_precision_paints_a_band_the_quick_look_shows(self):
        from texturizer import window

        d = draft.Draft("Box")
        s = paint.Surface(BOX)
        paint.commit(d, s, 0, lambda st: paint.apply_stroke(st, box_drag(), paint.Brush(True, 10, 50)), BOX_FT)
        m = d.mask()
        self.assertEqual(sorted(r[0] for r in m["split"]), [0, 1])   # the top face's two triangles
        pts, tri, _n = window.quicklook_mesh(BOX, BOX_FT)
        plain = window.layer_weights(len(tri), tri, len(pts), d.layers, BOX_FT)[:, 0]
        self.assertTrue((plain == 1).all())   # the source corners alone cannot carry the band
        got = window.quicklook_split(BOX, BOX_FT, d.layers)
        self.assertIsNotNone(got)
        fpos, fft, layers = got
        self.assertEqual(sorted(fft), sorted(BOX_FT))
        pts, tri, _n = window.quicklook_mesh(fpos, fft)
        W = window.layer_weights(len(tri), tri, len(pts), layers, None)[:, 0]
        a, b = np.array([-12.0, -8.0]), np.array([12.0, 8.0])
        u = np.clip((pts[:, :2] - a) @ (b - a) / ((b - a) @ (b - a)), 0, 1)
        dist = np.linalg.norm(pts[:, :2] - (a + u[:, None] * (b - a)), axis=1)
        top = pts[:, 2] > 19.9
        self.assertTrue((W[top & (dist < 2.0)] < 0.01).all())   # masked along the stroke (Exclude)
        self.assertTrue((W[top & (dist > 5.5)] == 1).all())     # textured beyond the brush
        self.assertTrue((W[~top] == 1).all())
        self.assertIsNone(window.quicklook_split(BOX, BOX_FT, [dict(L, mask={"faces": [], "triangles": [3]})
                                                                for L in d.layers]))


class SplitEngine(unittest.TestCase):
    """The Precision brush's split masks in the engine: flattened for an export
    (BumpMesh's _pipelineInputs), sampled per refined corner in the preview."""

    def painted(self, include=False):
        d = draft.Draft("P")
        if include:
            d.set("includeOnly", True)
        paint.commit(d, SURF, 0, lambda st: paint.apply_stroke(st, [hit_at(12.2, 12.7)], paint.Brush(True, 7, 100)))
        self.assertTrue(d.mask().get("split"))
        return d

    def test_export_input_is_the_flattened_tree(self):
        from texturizer import engine, handlers

        d = self.painted()
        run = handlers.run_settings(d, {})
        st = paint.state_from_mask(SURF, d.mask())
        flat = st.tree.flatten()
        got = paint.flatten_masks(POS, run["layers"])
        np.testing.assert_array_equal(got[0], flat["positions"])
        np.testing.assert_array_equal(got[2], flat["faceParentId"])
        np.testing.assert_array_equal(got[1][0]["mask"]["paint"], st.tree.flat_paint(0, flat)[0])
        p = engine.Pipeline(POS, run, mode="bake")
        self.assertEqual(p.map()["pin"]["inputTriangles"], flat["triCount"])
        self.assertEqual(int(p.flat_parent.max()), SURF.n - 1)

    def test_preview_samples_the_tree(self):
        from texturizer import engine

        h = hit_at(12.2, 12.7)["point"]
        for include in (False, True):
            d = self.painted(include)
            r = engine.preview({"draft": d.to_dict(), "mesh": {"positions": POS}, "tri_budget": 60000})
            V, W = r["mesh"]["vertices"], r["mesh"]["weights"][:, 0]
            top = V[:, 2] > -0.9   # the sheet, not the wall
            dist = np.hypot(V[:, 0] - h[0], V[:, 1] - h[1])
            inside, outside = top & (dist < 3.5 - 0.8), top & (dist > 3.5 + 0.8)
            want_in, want_out = (1.0, 0.0) if include else (0.0, 1.0)
            with self.subTest(include=include):
                np.testing.assert_allclose(W[inside], want_in, atol=1e-6)
                np.testing.assert_allclose(W[outside], want_out, atol=1e-6)
                self.assertTrue(inside.any() and outside.any())


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
        wm = paint.mask_from_state(want, {})
        self.assertTrue(wm.get("split"))   # Precision, the default Mode
        self.assertEqual(d.mask(), json.loads(json.dumps(wm)))
        self.assertEqual(len(d._undo), 1)
        self.assertTrue(t.button(paint.LEFT, True, 2, 1, shift=True))   # Shift erases
        self.assertTrue(t.button(paint.LEFT, False, 2, 1))
        self.assertEqual(len(d._undo), 2)
        self.assertNotEqual(d.mask(), json.loads(json.dumps(wm)))
        d.undo()
        self.assertEqual(d.mask(), json.loads(json.dumps(wm)))
        d.set("brushPrecision", False)   # Standard: whole triangles
        d.mask_clear()
        self.assertTrue(t.button(paint.LEFT, True, 1, 1))
        self.assertTrue(t.button(paint.LEFT, False, 1, 1))
        std = paint.apply_stroke(paint.State(SURF), [hit_at(5, 5)], paint.Brush(True, 10, 100, "standard"))
        self.assertEqual(d.mask(), {"faces": [], "triangles": np.nonzero(std.hard)[0].tolist()})

    def test_a_stroke_reaches_the_draft_once(self):
        """Dabs paint the stroke's state and redraw it; the draft (and so the
        preview) changes once, on release, or when the stroke is cut short."""
        d, events, drawn = self.d, [], []
        d.subscribe(lambda ev: events.append(ev["kind"]))
        t = paint.PaintTool(d, lambda: (SURF, {}), lambda x, y: self.at.get((x, y)), on_paint=lambda: drawn.append(1))
        d.set("exclusionTool", "brush")
        d.set("brushPrecision", False)
        events.clear()
        self.assertTrue(t.button(paint.LEFT, True, 1, 1))
        for xy in ((2, 1), (3, 1), (2, 1)):
            self.assertTrue(t.move(*xy))
        self.assertEqual((events, len(drawn)), ([], 4))
        self.assertTrue(t.stroke["state"].hard.any())
        self.assertTrue(t.button(paint.LEFT, False, 2, 1))
        self.assertEqual(events, ["mask"])
        want = paint.apply_stroke(paint.State(SURF), [hit_at(5, 5), hit_at(7, 5), hit_at(9, 6), hit_at(7, 5)],
                                  paint.Brush(True, 10, 50, "standard"))
        self.assertEqual(d.mask()["triangles"], np.nonzero(want.hard)[0].tolist())
        d.set("brushPrecision", True)
        events.clear()
        t.button(paint.LEFT, True, 3, 1, shift=True)
        t.cancel()   # the tool switched mid-stroke: the erase so far is kept, as one step
        self.assertEqual(events, ["mask"])
        self.assertIsNone(t.stroke)
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
        self.assertEqual(d.mask(), json.loads(json.dumps(paint.mask_from_state(want, {}))))
        n = len(d._undo)
        ok, out = self.run("texture_mask", {"paint": {"strokes": [[{"point": a["point"]}]], "erase": True, "size": 3}})
        self.assertTrue(ok, out)
        self.assertEqual(len(d._undo), n + 1)
        ok, out = self.run("texture_mask", {"paint": {"strokes": [[{"point": [1, 2]}]]}})
        self.assertFalse(ok)
        ok, out = self.run("texture_mask", {"paint": {"strokes": [[a]], "mode": "soft"}})
        self.assertFalse(ok)
        ok, out = self.run("texture_mask", {"clear": True})
        ok, out = self.run("texture_mask", {"paint": {"strokes": [[a]], "mode": "standard", "size": 6}})
        self.assertTrue(ok, out)
        self.assertNotIn("split", d.mask())
        ok, out = self.run("texture_mask", {"clear": True, "fill": {"triangles": [5], "maxAngle": 30}})
        self.assertTrue(ok, out)
        st = paint.fill(paint.State(SURF), 5, 30)
        self.assertEqual(d.mask()["triangles"], np.nonzero(st.hard)[0].tolist())
        ok, out = self.run("texture_mask", {"fill": {"faces": ["Face1"]}})
        self.assertFalse(ok)

    def test_the_draft_reports_brush_paint(self):
        """Precision then Standard over the same spot: the triangles stay split
        (BumpMesh merges a subtree only where its coverage agrees), and are
        still reported as masked."""
        ok, out = self.run("texture_draft", {"part": "Scan"})
        self.assertEqual((out["draft"]["layers"][0]["triangles"], out["draft"]["layers"][0].get("brush")), (0, None))
        a, b = hit_at(5, 5), hit_at(9, 7)
        for mode in ("precision", "standard"):
            ok, out = self.run("texture_mask", {"paint": {"strokes": [[a, b]], "mode": mode, "size": 6, "hardness": 50}})
            self.assertTrue(ok, out)
        m = live.draft().mask()
        split = {r[0] for r in m["split"]}
        soft = {c // 3 for c in (m.get("soft") or {}).get("corners", [])}
        n = len(set(m["triangles"]) | split | soft)
        self.assertGreater(len(split), 0)
        self.assertEqual((out["triangles"], out["brush"]), (n, {"split": len(split), "soft": len(soft)}))
        ok, out = self.run("texture_draft", {})
        L = out["draft"]["layers"][0]
        self.assertEqual((L["faces"], L["triangles"], L["brush"]["split"]), ([], n, len(split)))
        self.assertNotIn("Every face", self.run("texture_mask", {})[1]["note"])


if __name__ == "__main__":
    unittest.main()
