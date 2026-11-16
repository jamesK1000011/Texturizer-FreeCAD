# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The full textured preview within about 0.5 s of letting go (the speed
bar). Live on the Mac a brush stroke or a face mask took ~1.7 s to land:

  - with Preserve Untextured on (the default) a mask change asks for a new
    subdivision (the untextured faces stay coarse), about a second of
    subdivide and sliver clean-up on any part. Its first look now runs on
    the part's last full subdivision (engine._sibling, result "provisional"):
    the textured result at once, the untextured faces denser until the exact
    subdivision replaces it from the same job;
  - a Precision brush stroke sampled its split tree per refined corner in
    Python (~1 s on a box's top face). Now sampled in numpy
    (PaintTree.sample_paint_many, value for value the same), each shared
    corner once, and only for the split triangles a stroke changed (and the
    ones touching them);
  - interpolate_from_parents interpolates only the triangles whose parent's
    corners differ.

The budget is 0.5 s, scaled for slower CI runners (TEXTURIZER_BENCH_SCALE,
default 3 under CI, 1.5 elsewhere). TEXTURIZER_BENCH=1 prints the times."""
import os
import sys
import time
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from texturizer import draft, engine, paint, preview  # noqa: E402
from texturizer.engine import softmask  # noqa: E402
from test_paint import BOX_FT, box, box_drag  # noqa: E402

BUDGET_S = 0.5 * float(os.environ.get("TEXTURIZER_BENCH_SCALE") or (3.0 if os.environ.get("CI") else 1.5))
SHOW = bool(os.environ.get("TEXTURIZER_BENCH"))


def sphere(nu=124, nv=122, r=20.0):
    """A representative tessellated part: a sphere of ~30 k triangles."""
    th = np.linspace(0, np.pi, nv + 1)
    ph = np.linspace(0, 2 * np.pi, nu + 1)
    P = np.stack([r * np.sin(th)[:, None] * np.cos(ph)[None], r * np.sin(th)[:, None] * np.sin(ph)[None],
                  r * np.cos(th)[:, None] * np.ones_like(ph)[None] + r], -1)
    P[:, -1] = P[:, 0]
    tris = []
    for i in range(nv):
        for j in range(nu):
            a, b, c, d = P[i, j], P[i, j + 1], P[i + 1, j + 1], P[i + 1, j]
            if i > 0:
                tris.append([a, d, c])
            if i < nv - 1:
                tris.append([a, c, b])
    return np.asarray(tris, np.float32).reshape(-1)


SPHERE = sphere()
N = len(SPHERE) // 9
SPHERE_FT = {f"Face{k + 1}": list(range(k * (N // 4), (k + 1) * (N // 4))) for k in range(4)}


def line(y, x0=-10, x1=10, n=21):
    out = []
    for x in np.linspace(x0, x1, n):
        h = paint.ray_hit(SPHERE, [x, y, 60.0], [0, 0, -1])
        out.append(dict(h, view=[0, 0, -1]))
    return out


def scalar_sample_split(positions, mask, include, child_pos, parents):
    """The first port's sample_split: the tree sampled per refined corner, one point at a time."""
    split = {int(r[0]) for r in mask.get("split") or []}
    parents = np.asarray(parents, np.int64)
    rows = np.nonzero(np.isin(parents, np.fromiter(split, np.int64)))[0]
    t = paint.state_from_mask(paint.surface_for(positions), mask).tree
    cp = np.asarray(child_pos, np.float32).astype(np.float64).reshape(-1, 3, 3)[rows].tolist()
    p = np.zeros((len(rows), 3))
    for i, (f, tri) in enumerate(zip(parents[rows].tolist(), cp)):
        for k in range(3):
            p[i, k] = t.sample_paint(0, f, *tri[k])
    hard = (p >= 1).all(axis=1)
    cover, hm = (p, ~hard & (p == 0).all(axis=1)) if include else (1 - p, hard)
    return rows, (1 - cover).astype(np.float32), hm.astype(np.uint8)


def timed(fn):
    t = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - t


class Sampling(unittest.TestCase):
    def test_numpy_sampler_equals_the_scalar_one(self):
        d = draft.Draft(part="Body")
        d.set_texture("Knurling")
        sub = engine.preview({"draft": d.to_dict(), "mesh": {"positions": box()}, "face_triangles": BOX_FT})["stages"]["subdivide"]
        s = paint.surface_for(box())
        for hard in (100.0, 40.0):
            for step in range(2):
                br = paint.Brush(True, 6.0 + step, hard, "precision")
                hits = [dict(h, point=[h["point"][0] + 3 * step, h["point"][1], h["point"][2]]) for h in box_drag(11)]
                paint.commit(d, s, 0, lambda st: paint.apply_stroke(st, hits, br), BOX_FT)
                for include in (False, True):
                    got = paint.sample_split(box(), d.mask(0), include, sub["positions"], sub["faceParentId"])
                    ref = scalar_sample_split(box(), d.mask(0), include, sub["positions"], sub["faceParentId"])
                    for a, b in zip(got, ref):
                        np.testing.assert_array_equal(a, b)

    def test_incremental_strokes_equal_a_fresh_sample(self):
        """Three strokes on a 30 k-triangle part: each later stroke samples only
        what it changed (and what touches it) and still equals sampling all."""
        d = draft.Draft(part="Body")
        d.set_texture("Knurling")
        sub = engine.preview({"draft": d.to_dict(), "mesh": {"positions": SPHERE}, "face_triangles": SPHERE_FT})["stages"]["subdivide"]
        s = paint.surface_for(SPHERE)
        br = paint.Brush(True, 4.0, 60.0, "precision")
        for k, y in enumerate((-2.0, 6.0, 3.0)):
            paint.commit(d, s, 0, lambda st: paint.apply_stroke(st, line(y), br), SPHERE_FT)
            got = paint.sample_split(SPHERE, d.mask(0), False, sub["positions"], sub["faceParentId"])
            stats = dict(paint.SPLIT_STATS)
            if k:
                self.assertGreater(stats["reused"], 0)
            paint._SPLIT_KEEP.clear()
            fresh = paint.sample_split(SPHERE, d.mask(0), False, sub["positions"], sub["faceParentId"])
            for a, b in zip(got, fresh):
                np.testing.assert_array_equal(a, b)
        ref = scalar_sample_split(SPHERE, d.mask(0), False, sub["positions"], sub["faceParentId"])
        for a, b in zip(got, ref):
            np.testing.assert_array_equal(a, b)

    def test_interpolate_only_non_uniform_parents(self):
        rng = np.random.default_rng(3)
        parent = rng.normal(size=(50, 9)).astype(np.float32)
        pid = rng.integers(0, 50, 400)
        w = rng.dirichlet([1, 1, 1], size=(400, 3))
        child = np.einsum("nkj,njc->nkc", w, parent.reshape(-1, 3, 3)[pid].astype(np.float64)).astype(np.float32)
        vals = rng.random((50, 3)).astype(np.float32)
        vals[::2] = vals[::2, :1]      # half the parents uniform
        got = softmask.interpolate_from_parents(child.reshape(-1), pid, parent.reshape(-1), vals.reshape(-1))
        # every row interpolated (the uniform ones come out as their value)
        ref = np.empty((400, 3))
        P = parent.reshape(-1, 3, 3).astype(np.float64)[pid]
        for i in range(400):
            a, b, c = P[i]
            e0, e1 = b - a, c - a
            d00, d01, d11 = e0 @ e0, e0 @ e1, e1 @ e1
            den = d00 * d11 - d01 * d01
            for k in range(3):
                q = child.reshape(-1, 3, 3)[i, k].astype(np.float64) - a
                v = (d11 * (q @ e0) - d01 * (q @ e1)) / den
                ww = (d00 * (q @ e1) - d01 * (q @ e0)) / den
                u = 1 - v - ww
                bc = np.clip([u, v, ww], 0, None)
                bc = bc / bc.sum() if min(u, v, ww) < 0 else np.array([u, v, ww])
                ref[i, k] = np.clip(bc @ vals[pid[i]].astype(np.float64), 0, 1)
        np.testing.assert_allclose(got.reshape(-1, 3), ref, atol=2e-6)
        same = (vals[pid] == vals[pid][:, :1]).all(axis=1)
        np.testing.assert_array_equal(got.reshape(-1, 3)[same], vals[pid][same])


class Budget(unittest.TestCase):
    """The preview path on a ~30 k-triangle part (about 165 k preview triangles)."""

    @classmethod
    def setUpClass(cls):
        engine._SUBDIVIDED.clear()
        engine._BASE.clear()
        cls.d = draft.Draft(part="Body")
        cls.d.set_texture("Knurling")
        cls.cold, cls.t_cold = timed(lambda: engine.preview(cls.si()))

    @classmethod
    def si(cls, **kw):
        return {"draft": cls.d.to_dict(), "mesh": {"positions": SPHERE}, "face_triangles": SPHERE_FT, **kw}

    def report(self, what, t):
        if SHOW:
            print(f"\n  {what}: {t * 1000:.0f} ms (budget {BUDGET_S * 1000:.0f} ms, cold {self.t_cold * 1000:.0f} ms)")

    def test_a_brush_stroke_lands_within_budget(self):
        d = self.d
        cache = dict(self.cold["stages"])
        s = paint.surface_for(SPHERE)
        br = paint.Brush(True, 4.0, 60.0, "precision")
        for y in (-12.0, -6.0):
            _, t_rel = timed(lambda: paint.commit(d, s, 0, lambda st: paint.apply_stroke(st, line(y), br), SPHERE_FT))
            self.assertTrue(engine.kept(self.si()))      # split paint leaves the untextured faces as they were
            r, t = timed(lambda: engine.preview(self.si(rerun_from="map", cache=cache)))
            self.report(f"brush stroke at y={y}: commit {t_rel * 1000:.0f} ms, preview", t)
            self.assertLess(t, BUDGET_S)
            self.assertFalse(r["provisional"])

    def test_a_face_mask_with_preserve_untextured_shows_within_budget(self):
        d = self.d
        d.mask_add(faces=["Face2"])
        si = self.si()
        self.assertFalse(engine.kept(si))                # a new subdivision is needed (Preserve Untextured)
        first, t = timed(lambda: engine.preview(dict(si, coarse=True)))
        self.report("face mask, first look (provisional)", t)
        self.assertTrue(first["provisional"] and first["coarse"])
        self.assertLess(t, BUDGET_S)
        self.assertEqual(first["stages"], {})          # not kept as the full run's
        exact = engine.preview(si)
        self.assertFalse(exact["provisional"])
        # the exact one differs only on the untextured faces, which it keeps coarser
        self.assertGreaterEqual(len(first["mesh"]["faces"]), len(exact["mesh"]["faces"]))
        engine._SUBDIVIDED.clear()
        again = engine.preview(si)
        for k in ("vertices", "faces", "normals", "colours"):
            np.testing.assert_array_equal(exact["mesh"][k], again["mesh"][k])
        d.mask_remove(faces=["Face2"])

    def test_a_ring_drag_release_within_budget(self):
        cache = dict(self.cold["stages"])
        self.d.set_frame(rotation=[0, 0, 25.0])
        r, t = timed(lambda: engine.preview(self.si(rerun_from="map", cache=cache)))
        self.report("ring release (from map)", t)
        self.assertLess(t, BUDGET_S)
        self.d.set_frame(rotation=[0, 0, 0])


class Controller(unittest.TestCase):
    def test_mask_change_shows_the_provisional_then_the_exact(self):
        from test_preview import Clock, View, Worker

        engine._SUBDIVIDED.clear()
        engine._BASE.clear()
        d = draft.Draft("Body")
        d.set_texture("Knurling")
        d.set("useDisplacement", True)
        eng = engine.Engine(mesh_for=lambda part: ({"positions": SPHERE}, SPHERE_FT))
        clock, worker, view = Clock(), Worker(), View()
        c = preview.PreviewController(eng, d, view, quicklook_ok=False, run_async=worker, schedule=clock.schedule)
        c.open()
        worker.run()
        n0 = len(view.meshes)
        d.mask_add(faces=["Face3"])
        clock.advance(preview.DEBOUNCE_S + 0.01)
        worker.run()
        self.assertIn(("note", preview.PROVISIONAL_NOTE), view.log)
        self.assertEqual(len(view.meshes) - n0, 2)        # the provisional, then the exact replacing it
        self.assertTrue(c.current)


if __name__ == "__main__":
    unittest.main()
