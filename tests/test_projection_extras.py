# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""Projection section helpers: Auto-fit and Position on model against
BumpMesh's own main.js / mapping.js (run in node through
tests/tools/bm_js.mjs), Reset, From face with a fake Part.Cylinder surface,
Stamp mode, and apply() onto a draft."""
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(HERE, "tools"))

import numpy as np  # noqa: E402

import gen_controls  # noqa: E402
from texturizer import draft, projection_extras as px  # noqa: E402

SRC = os.environ.get("BUMPMESH_SRC", gen_controls.DEFAULT_SRC)
HAVE_JS = os.path.exists(os.path.join(SRC, "js", "main.js")) and shutil.which("node")
BM_JS = os.path.join(HERE, "tools", "bm_js.mjs")


def run_js(cmd, spec):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "spec.json")
        with open(path, "w") as fh:
            json.dump(spec, fh)
        r = subprocess.run(["node", BM_JS, SRC, cmd, path], capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise AssertionError(r.stderr[-2000:])
    return json.loads(r.stdout)


def f32(a):
    return np.asarray(a, dtype=np.float32).astype(float)


def cylinder(cx, cy, r, h, n=24, z0=0.0, inner=None, caps=True):
    """Side, top cap and (optionally) a bore, as triangles (N, 3, 3), float32-exact."""
    T = []
    for i in range(n):
        a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
        p0 = (cx + r * math.cos(a0), cy + r * math.sin(a0))
        p1 = (cx + r * math.cos(a1), cy + r * math.sin(a1))
        T += [[(*p0, z0), (*p1, z0), (*p1, z0 + h)], [(*p0, z0), (*p1, z0 + h), (*p0, z0 + h)]]
        if caps:
            T += [[(cx, cy, z0 + h), (*p0, z0 + h), (*p1, z0 + h)], [(cx, cy, z0), (*p1, z0), (*p0, z0)]]
        if inner:
            q0 = (cx + inner * math.cos(a0), cy + inner * math.sin(a0))
            q1 = (cx + inner * math.cos(a1), cy + inner * math.sin(a1))
            T += [[(*q1, z0), (*q0, z0), (*q0, z0 + h)], [(*q1, z0), (*q0, z0 + h), (*q1, z0 + h)]]
    return f32(T)


def box(sx, sy, sz, o=(0, 0, 0)):
    x0, y0, z0 = o
    x1, y1, z1 = x0 + sx, y0 + sy, z0 + sz
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    f = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return f32([[v[a], v[b], v[c]] for a, b, c in f])


def sphere(r, n=16, c=(0, 0, 0)):
    T = []
    for i in range(n):
        t0, t1 = math.pi * i / n, math.pi * (i + 1) / n
        for j in range(2 * n):
            p0, p1 = 2 * math.pi * j / (2 * n), 2 * math.pi * (j + 1) / (2 * n)

            def P(t, p):
                return (c[0] + r * math.sin(t) * math.cos(p), c[1] + r * math.sin(t) * math.sin(p), c[2] + r * math.cos(t))
            T += [[P(t0, p0), P(t1, p0), P(t1, p1)], [P(t0, p0), P(t1, p1), P(t0, p1)]]
    return f32(T)


class FakeVec:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


class FakeCylinder:
    """Duck-typed like Part.Cylinder: Center, Axis, Radius."""

    def __init__(self, center, axis, radius):
        self.Center, self.Axis, self.Radius = FakeVec(*center), FakeVec(*axis), radius


MESHES = {
    "cylinder": cylinder(3.0, -2.0, 10.0, 20.0),
    "tube": cylinder(-5.0, 7.5, 12.5, 30.0, n=32, inner=8.0),
    "noisy": None,
    "box": box(20, 30, 10, (-4, 2, 0)),
    "flat": f32([[(0, 0, 0), (10, 0, 0), (10, 10, 0)], [(0, 0, 0), (10, 10, 0), (0, 10, 0)]]),
}
_rng = np.random.default_rng(7)
_n = cylinder(0.0, 0.0, 15.0, 25.0, n=40)
MESHES["noisy"] = f32(_n + _rng.normal(0, 0.05, _n.shape))


class TestAutoFitReset(unittest.TestCase):
    def test_cylinder(self):
        r = px.auto_fit(MESHES["cylinder"])
        self.assertAlmostEqual(r["cylinderCenterX"], 3.0, places=4)
        self.assertAlmostEqual(r["cylinderCenterY"], -2.0, places=4)
        # Kasa through the polygon's vertices: the circumradius, 10.
        self.assertAlmostEqual(r["cylinderRadius"], 10.0, places=4)

    def test_fails_like_bumpmesh(self):
        self.assertIsNone(px.auto_fit(MESHES["flat"]))  # no side triangles
        self.assertIsNone(px.auto_fit([]))

    def test_reset(self):
        self.assertEqual(px.reset(), {"cylinderCenterX": None, "cylinderCenterY": None, "cylinderRadius": None})

    @unittest.skipUnless(HAVE_JS, "node or the BumpMesh checkout ($BUMPMESH_SRC) is missing")
    def test_against_bumpmesh(self):
        names = list(MESHES)
        js = run_js("autofit", [{"positions": MESHES[k].reshape(-1).tolist()} for k in names])
        for k, j in zip(names, js):
            p = px.auto_fit(MESHES[k])
            with self.subTest(mesh=k):
                if j is None:
                    self.assertIsNone(p)
                    continue
                self.assertIsNotNone(p)
                for key in px.CYLINDER_KEYS:
                    self.assertAlmostEqual(p[key], j[key], delta=1e-6 * max(1.0, abs(j[key])))
        self.assertIsNotNone(js[0])  # the cases do exercise a fit


class TestFromFace(unittest.TestCase):
    def test_z_axis(self):
        u = px.from_face(FakeCylinder((4.0, -1.5, 12.0), (0, 0, -1), 6.25))
        self.assertEqual(u, {"mappingMode": 3, "cylinderCenterX": 4.0, "cylinderCenterY": -1.5, "cylinderRadius": 6.25})

    def test_sequences_and_frame(self):
        def to_frame(v, is_dir):  # the frame is the document moved by (10, 20, 0)
            return v if is_dir else (v[0] + 10, v[1] + 20, v[2])
        s = FakeCylinder((1, 2, 3), (0, 0, 1), 5)
        s.Center, s.Axis = (1, 2, 3), (0, 0, 1)
        u = px.from_face(s, to_frame=to_frame)
        self.assertEqual((u["cylinderCenterX"], u["cylinderCenterY"], u["cylinderRadius"]), (11, 22, 5.0))

    def test_tilted_axis_names_the_rotation(self):
        with self.assertRaises(ValueError) as cm:
            px.from_face(FakeCylinder((0, 0, 0), (1, 0, 0), 3))
        self.assertIn("Rotate", str(cm.exception))
        with self.assertRaises(ValueError):
            px.from_face(FakeCylinder((0, 0, 0), (0, 0, 1), 0))

    def test_align_rotation(self):
        for axis in [(1, 0, 0), (0, 1, 0), (0.3, -0.5, 0.8), (-1, 2, -3), (0, 0, 1)]:
            r = px.align_rotation(axis)
            a, b = math.radians(r["rotateX"]), math.radians(r["rotateY"])
            Rx = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])
            Ry = np.array([[math.cos(b), 0, math.sin(b)], [0, 1, 0], [-math.sin(b), 0, math.cos(b)]])
            v = np.asarray(axis, float) / np.linalg.norm(axis)
            np.testing.assert_allclose(Rx @ Ry @ v, [0, 0, 1], atol=1e-12)


class TestStamp(unittest.TestCase):
    def test_stamp_mode(self):
        self.assertEqual(px.stamp_mode({"mappingMode": 5}, True), {"stampOnce": True, "mappingMode": 7})
        self.assertEqual(px.stamp_mode({"mappingMode": 1}, True), {"stampOnce": True})
        self.assertEqual(px.stamp_mode({"mappingMode": 7}, False), {"stampOnce": False, "mappingMode": 5})
        self.assertEqual(px.stamp_mode({"mappingMode": 7}, False, mode_before=6), {"stampOnce": False, "mappingMode": 6})
        self.assertEqual(px.stamp_mode({"mappingMode": 0}, False), {"stampOnce": False})

    def test_planar_pick_axis(self):
        u = px.stamp_place((1, 2, 3), (0.1, -0.9, 0.2), {"mappingMode": 0})
        self.assertEqual(u["mappingMode"], 1)
        self.assertEqual(u["stampSide"], -1)
        self.assertEqual((u["stampCenterX"], u["stampCenterY"], u["stampCenterZ"]), (1, 2, 3))
        self.assertNotIn("mappingMode", px.stamp_place((0, 0, 0), (0, 0, -1), {"mappingMode": 0}))

    def test_apply_keeps_stamp_fields_on_the_layer(self):
        d = draft.Draft()
        u = px.stamp_place((1, 2, 3), (0, 0, 1), {"mappingMode": 2})
        px.apply(d, dict(px.stamp_mode({"mappingMode": 5}, True), **u))
        self.assertTrue(d.get("stampOnce"))
        self.assertEqual(d.get("mappingMode"), 0)
        self.assertEqual(d.get("stampCenterZ"), 3)
        self.assertEqual(d.get("stampSide"), 1)
        self.assertNotIn("bumpmesh", d.layer())
        d.undo()
        self.assertFalse(d.get("stampOnce"))
        self.assertIsNone(d.get("stampCenterZ"))

    def test_undo_covers_position_on_model(self):
        d = draft.Draft()
        px.apply(d, dict(px.stamp_mode({"mappingMode": 5}, True), **px.stamp_place((1, 2, 3), (0, 0, 1), {"mappingMode": 2})))
        first = {k: d.get(k) for k in px.STAMP_KEYS}
        px.apply(d, px.stamp_place((4, 5, -6), (0, 0, -1), {"mappingMode": d.get("mappingMode")}))
        self.assertEqual((d.get("stampCenterZ"), d.get("stampSide")), (-6, -1))
        d.undo()   # one step back: the first placement
        self.assertEqual({k: d.get(k) for k in px.STAMP_KEYS}, first)
        d.redo()
        self.assertEqual((d.get("stampCenterX"), d.get("stampCenterZ"), d.get("stampSide")), (4, -6, -1))

    def test_load_and_json_keep_stamp_fields(self):
        d = draft.Draft()
        px.apply(d, {"stampOnce": True, "mappingMode": 7, "stampCenterX": 1.5, "stampCenterY": -2, "stampCenterZ": 3,
                     "stampSide": -1, "stampNormalX": 0.0, "stampNormalY": 0.6, "stampNormalZ": 0.8})
        want = {k: d.get(k) for k in px.STAMP_KEYS}
        e = draft.Draft.from_json(d.to_json())
        self.assertEqual({k: e.get(k) for k in px.STAMP_KEYS}, want)
        f = draft.Draft()
        f.load(d.to_dict())
        self.assertEqual({k: f.get(k) for k in px.STAMP_KEYS}, want)
        self.assertEqual(f.layer()["settings"], d.layer()["settings"])
        f.undo()   # load is one undo step: back to the defaults
        self.assertIsNone(f.get("stampCenterX"))

    @unittest.skipUnless(HAVE_JS, "node or the BumpMesh checkout ($BUMPMESH_SRC) is missing")
    def test_against_bumpmesh(self):
        S = dict(draft.layer_defaults(), stampOnce=True)
        sph, cyl, bx = sphere(10.0, c=(1, -2, 3)), MESHES["tube"], MESHES["box"]
        cases = [
            (bx, dict(S, mappingMode=0), (5, 10, 10), (0, 0, 1), True, None),
            (bx, dict(S, mappingMode=0), (5, 2, 4), (0, -1, 0), True, None),
            (bx, dict(S, mappingMode=2), (-4, 12, 4), (-1, 0, 0), True, None),
            (bx, dict(S, mappingMode=1), (16, 2, 4), (0.2, -0.9, 0.3), False, None),
            (cyl, dict(S, mappingMode=3), (7.5, 7.5, 12), (1, 0, 0), True, None),
            (cyl, dict(S, mappingMode=3, cylinderCenterX=-5, cylinderCenterY=7.5), (3.0, 7.5, 9), (-1, 0, 0), True, None),
            (cyl, dict(S, mappingMode=3), (-5, 7.5, 30), (0, 0, 1), True, None),
            (sph, dict(S, mappingMode=7, scaleU=8, scaleV=8), (1 + 6, -2 + 6, 3 + 4.9), (0.6, 0.6, 0.5), True, None),
            (sph, dict(S, mappingMode=7, scaleU=4, scaleV=12), (1, -12, 3), (0, -1, 0), True, (256, 512)),
            (bx, dict(S, mappingMode=7, scaleU=10, scaleV=10), (15.5, 31.7, 9.5), (0.5, 0.5, 0.7), True, None),
            (cyl, dict(S, mappingMode=7, scaleU=6, scaleV=6), (-5 + 12.4, 7.5, 15), (1, 0, 0), True, None),
        ]
        spec = [{"positions": m.reshape(-1).tolist(), "settings": s, "point": list(p), "normal": list(n),
                 "pickAxis": pick, "texSize": tex} for m, s, p, n, pick, tex in cases]
        js = run_js("stamp", spec)
        for k, ((m, s, p, n, pick, tex), j) in enumerate(zip(cases, js)):
            u = px.stamp_place(p, n, s, positions=m, pick_axis=pick, tex_size=tex)
            got = dict(s, **u)
            with self.subTest(case=k):
                self.assertIsNotNone(j)
                for key, want in j.items():
                    if want is None:
                        self.assertIsNone(got.get(key), key)
                    else:
                        self.assertAlmostEqual(got[key], want, delta=1e-6, msg=key)
        # The surface-normal cases did snap and re-fit (not the picked normal).
        self.assertNotAlmostEqual(js[7]["stampNormalZ"], 0.5 / math.sqrt(0.97), places=3)


if __name__ == "__main__":
    unittest.main()
