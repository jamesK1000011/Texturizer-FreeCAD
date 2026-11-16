# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The sliver clean-up (regularize) in batches: regularize_batched against
regularize_sequential (the JS's one collapse at a time) on 18 inputs, the
14 parity cases' own (export path) and four more (a box with a 3.8 mm round
hole at the 0.5 mm preview edge, a square hole, Preserve Untextured's frozen
faces, two slivers on one edge). Expected: the same output bit for bit, the
same collapse and reject totals, and so the same quality (smallest angle,
slivers left, closed, manifold). Prints the milliseconds of both on the
holed box."""
import os
import sys
import time
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from texturizer import engine  # noqa: E402
from texturizer.engine import exportpipeline  # noqa: E402
from texturizer.engine import regularize as R  # noqa: E402
from texturizer.engine.index import DEFAULTS, regularize_opts  # noqa: E402
from texturizer.engine.meshindex import weld  # noqa: E402
from test_untangle import round_hole, square_hole  # noqa: E402


def parity_inputs():
    """The regularize calls of the 14 parity cases (export path)."""
    import test_engine_parity as T

    calls = []
    orig = exportpipeline.regularize_mesh

    def spy(*a, **k):
        calls.append((a, k))
        return orig(*a, **k)
    exportpipeline.regularize_mesh = spy
    try:
        T.EngineParity.setUpClass()
        t = T.EngineParity()
        for case in T.EngineParity.refs["cases"]:
            t.run_case(case)
    finally:
        exportpipeline.regularize_mesh = orig
    return calls


def subdivided(pos, edge, weights=None):
    sub = engine.subdivide(np.asarray(pos, np.float32), edge, None, weights, fast=True)
    return sub["position"], sub["faceParentId"], sub.get("excludeWeight")


def quality(r):
    """(smallest angle in degrees, slivers (aspect >= 5^2), closed, manifold)."""
    p = np.asarray(r["position"], np.float32).reshape(-1, 3)
    if not len(p):
        return (0.0, 0, True, True)
    ids, nv, _ = weld(p.astype(np.float64), 1e5)
    C = ids.reshape(-1, 3)
    V = p.astype(np.float64)
    A, B, Cc = V[0::3], V[1::3], V[2::3]

    def ang(a, b, c):
        u, v = b - a, c - a
        cos = (u * v).sum(1) / np.maximum(np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1), 1e-300)
        return np.degrees(np.arccos(np.clip(cos, -1, 1)))
    min_angle = float(np.min([ang(A, B, Cc), ang(B, Cc, A), ang(Cc, A, B)]))
    slivers = int((R._aspect_rows(A, B, Cc) >= 25).sum())
    E = np.sort(C[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), 1)
    _, cnt = np.unique(E[:, 0] * (nv + 1) + E[:, 1], return_counts=True)
    return (round(min_angle, 9), slivers, bool((cnt == 2).all()), bool((cnt <= 2).all()))


class Batched(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cases = []
        for k, (a, kw) in enumerate(parity_inputs()):
            cases.append((f"parity{k}", a, kw))
        opts = regularize_opts({**DEFAULTS})
        pos, par, _ = subdivided(round_hole(H=10.0, r=1.9), 0.5)
        cases.append(("roundHole0.5", (pos, par, 0.5, opts), {}))
        pos, par, _ = subdivided(square_hole(), 1.0)
        cases.append(("squareHole1.0", (pos, par, 1.0, opts), {}))
        box = np.asarray(square_hole(), np.float32)
        w = np.zeros(len(box) // 3, np.float32)
        w[: len(w) // 3] = 1.0                         # the first third of the triangles excluded
        pos, par, ew = subdivided(box, 1.0, w)
        cases.append(("preserveExcluded", (pos, par, 1.0, {**opts, "preserveExcluded": True}, ew), {}))
        two = np.asarray([[0, 0, 0], [10, 0, 0], [5, 0.05, 0], [0, 0, 0], [5, -0.05, 0], [10, 0, 0]],
                         np.float32).reshape(-1)
        cases.append(("twoSlivers", (two, np.arange(2), 5.0, {}), {}))
        cls.cases = cases

    def test_eighteen_cases(self):
        self.assertEqual(len(self.cases), 18)

    def test_same_as_one_at_a_time(self):
        rows = []
        for name, a, kw in self.cases:
            with self.subTest(case=name):
                t = time.perf_counter()
                s = R.regularize_sequential(*a, **kw)
                ts = time.perf_counter() - t
                t = time.perf_counter()
                b = R.regularize_batched(*a, **kw)
                tb = time.perf_counter() - t
                for k in ("position", "normal", "faceParentId"):
                    self.assertEqual(np.asarray(s[k]).tobytes(), np.asarray(b[k]).tobytes(), k)
                if s["excludeWeight"] is None:
                    self.assertIsNone(b["excludeWeight"])
                else:
                    self.assertEqual(s["excludeWeight"].tobytes(), b["excludeWeight"].tobytes())
                self.assertEqual(s["collapseCount"], b["collapseCount"])
                # the totals and the gates before the per-triangle ones are
                # the same; which per-triangle gate counts a refusal may not be
                self.assertEqual(sum(s["rejectStats"].values()), sum(b["rejectStats"].values()))
                for k in ("frozen", "wingCount", "foldedApex", "linkCondition"):
                    self.assertEqual(s["rejectStats"][k], b["rejectStats"][k], k)
                qs, qb = quality(s), quality(b)
                self.assertEqual(qs, qb)
                rows.append((name, s["collapseCount"], ts * 1000, tb * 1000, qb))
        for name, n, ts, tb, q in rows:
            print(f"  {name:18s} collapses {n:6d}  sequential {ts:7.0f} ms  batched {tb:6.0f} ms  "
                  f"min angle {q[0]:.3f} slivers {q[1]} closed {q[2]} manifold {q[3]}")

    def test_fast_weld_is_weld(self):
        for name, a, _ in self.cases:
            p = np.asarray(a[0], np.float32).reshape(-1, 3)
            got = R.fast_weld(p)
            ref = weld(p.astype(np.float64), R.QUANTISE)
            self.assertTrue(np.array_equal(got[0], ref[0]), name)
            self.assertEqual(got[1], ref[1])
            self.assertTrue(np.array_equal(got[2], ref[2]), name)
        # -0 and 0, and points within the grid's step: one vertex each
        p = np.asarray([[0, 0, 0], [-0.0, 0, 0], [1, 1, 1], [1 + 1e-7, 1, 1], [2, 2, 2], [0, 0, 0]], np.float32)
        got, ref = R.fast_weld(p), weld(p.astype(np.float64), R.QUANTISE)
        self.assertTrue(np.array_equal(got[0], ref[0]))
        self.assertTrue(np.array_equal(got[2], ref[2]))

    def test_a_stale_cleanup_stops(self):
        name, a, kw = self.cases[-1]
        self.assertIsNone(R.regularize_batched(*a, should_abort=lambda: True))
        self.assertIsNone(R.regularize_sequential(*a, should_abort=lambda: True))


if __name__ == "__main__":
    unittest.main()
