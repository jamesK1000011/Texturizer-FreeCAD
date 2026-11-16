# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The export is clean on a part with a hole, as FreeCAD's Mesh evaluation
judges it: no self-intersections (displaced walls crossing at the hole and
its corners: engine.untangle), no non-manifold edges (walls meeting at a
square hole's inner corner), no points on edges (cap triangles:
meshrepair.fix_caps via engine.printable, by FreeCAD's own criterion,
MeshEvalPointOnEdge: a point within 0.001 mm of a facet edge, inside the
edge's box; BumpMesh's own output has them too, so engine.texture keeps
them and stays bit-identical to it). Live: a 40 x 30 x 20 box with an M3 hole had 21
self-intersecting pairs. Offline before this: 96 pairs and 1 cap with a
3.6 mm round hole; 35 pairs, 18 non-manifold edges and 1 cap with a 4 mm
square one."""
import os
import sys
import unittest
import warnings

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from texturizer import draft, engine, handlers  # noqa: E402
from texturizer.engine import untangle  # noqa: E402
from texturizer.engine.meshrepair import count_edge_defects, fix_caps  # noqa: E402


def round_hole(X=40.0, Y=30.0, H=20.0, r=1.8, n=32):
    """A box with a round through hole, tessellated as a CAD kernel does:
    the top and bottom annuli share their boundary points with the walls."""
    ang = list(np.linspace(0, 2 * np.pi, n, endpoint=False))
    for sx, sy in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
        ang.append(np.arctan2(sy * Y / 2, sx * X / 2) % (2 * np.pi))
    ang = np.array(sorted(set(np.round(ang, 12))))
    d = np.stack([np.cos(ang), np.sin(ang)], 1)
    cx, cy = X / 2, Y / 2
    C = np.stack([cx + r * d[:, 0], cy + r * d[:, 1]], 1)
    with np.errstate(divide="ignore"):
        t = np.minimum(np.abs((X / 2) / d[:, 0]), np.abs((Y / 2) / d[:, 1]))
    R = np.stack([cx + d[:, 0] * t, cy + d[:, 1] * t], 1)
    tris = []
    for i in range(len(ang)):
        j = (i + 1) % len(ang)
        c0, c1, r0, r1 = C[i], C[j], R[i], R[j]
        tris += [[[*c0, H], [*r0, H], [*r1, H]], [[*c0, H], [*r1, H], [*c1, H]]]
        tris += [[[*c0, 0], [*r1, 0], [*r0, 0]], [[*c0, 0], [*c1, 0], [*r1, 0]]]
        tris += [[[*r0, 0], [*r1, 0], [*r1, H]], [[*r0, 0], [*r1, H], [*r0, H]]]
        tris += [[[*c0, 0], [*c1, H], [*c1, 0]], [[*c0, 0], [*c0, H], [*c1, H]]]
    return np.asarray(tris, np.float32).reshape(-1)


def square_hole(X=40, Y=30, H=20, hx=(18, 22), hy=(13, 17)):
    xs, ys = [0, hx[0], hx[1], X], [0, hy[0], hy[1], Y]
    tris = []

    def q(a, b, c, d):
        tris.extend([[a, b, c], [a, c, d]])
    for z, up in ((H, True), (0, False)):
        for i in range(3):
            for j in range(3):
                if i == 1 and j == 1:
                    continue
                a, b, c, d = [xs[i], ys[j], z], [xs[i + 1], ys[j], z], [xs[i + 1], ys[j + 1], z], [xs[i], ys[j + 1], z]
                q(a, b, c, d) if up else q(a, d, c, b)
    for i in range(3):
        q([xs[i], 0, 0], [xs[i + 1], 0, 0], [xs[i + 1], 0, H], [xs[i], 0, H])
        q([xs[i + 1], Y, 0], [xs[i], Y, 0], [xs[i], Y, H], [xs[i + 1], Y, H])
    for j in range(3):
        q([X, ys[j], 0], [X, ys[j + 1], 0], [X, ys[j + 1], H], [X, ys[j], H])
        q([0, ys[j + 1], 0], [0, ys[j], 0], [0, ys[j], H], [0, ys[j + 1], H])
    a0, a1, b0, b1 = hx[0], hx[1], hy[0], hy[1]
    q([a0, b0, 0], [a0, b0, H], [a1, b0, H], [a1, b0, 0])
    q([a1, b0, 0], [a1, b0, H], [a1, b1, H], [a1, b1, 0])
    q([a1, b1, 0], [a1, b1, H], [a0, b1, H], [a0, b1, 0])
    q([a0, b1, 0], [a0, b1, H], [a0, b0, H], [a0, b0, 0])
    return np.asarray(tris, np.float32).reshape(-1)


def freecad_points_on_edges(pos, tol=1e-3):
    """FreeCAD's MeshEvalPointOnEdge, written plainly (every point against
    every facet): a point that is not a corner of the facet, inside the
    bounding box of one of its edges (MeshGeomEdge::GetBoundBox().IsInBox)
    and within 0.001 mm of it (IsPointOf(point, 0.001F))."""
    from texturizer.engine.meshindex import weld

    P = np.asarray(pos, float).reshape(-1, 3)
    vid, _n, firsts = weld(P, 1e5)
    V, F = P[firsts], vid.reshape(-1, 3)
    out = set()
    for k in range(3):
        a, b = V[F[:, k]], V[F[:, (k + 1) % 3]]
        lo, hi = np.minimum(a, b), np.maximum(a, b)
        ab = b - a
        L2 = (ab * ab).sum(1)
        for v in range(len(V)):
            p = V[v]
            inbox = ((p >= lo) & (p <= hi)).all(1) & (F != v).all(1)
            if not inbox.any():
                continue
            i = np.nonzero(inbox)[0]
            t = ((p - a[i]) * ab[i]).sum(1) / np.maximum(L2[i], 1e-300)
            d = np.linalg.norm(a[i] + ab[i] * t[:, None] - p, axis=1)
            out.update((v, int(f)) for f in i[d <= tol])
    return out


def export(pos):
    d = draft.Draft(part="Body")
    d.set_texture("Knurling")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return engine.printable(engine.texture(pos, handlers.run_settings(d, {}), mode="export"))


class CleanExport(unittest.TestCase):
    def check(self, part):
        self.assertEqual(len(untangle.crossings(part)), 0)   # the input is clean
        res = export(part)
        P = res["positions"]
        self.assertEqual(len(untangle.crossings(P)), 0)        # FreeCAD: selfIntersections
        self.assertEqual(count_edge_defects(P), {"open": 0, "nonManifold": 0, "tris": len(P) // 9})
        self.assertEqual(res["pointsOnEdges"], 0)               # FreeCAD: pointsOnEdges false
        T = np.asarray(P, float).reshape(-1, 3, 3)
        self.assertGreater(np.einsum("ij,ij->i", T[:, 0], np.cross(T[:, 1], T[:, 2])).sum(), 0)   # outward
        return res

    def test_round_hole(self):
        self.check(round_hole())

    def test_square_hole(self):
        self.check(square_hole())

    def test_crossings_finds_a_crossing(self):
        T = np.array([[[0, 0, 0], [2, 0, 0], [0, 2, 0]], [[0.5, 0.5, -1], [0.5, 0.5, 1], [1.5, 0.2, 0]]], float)
        self.assertEqual(untangle.crossings(T).tolist(), [[0, 1]])
        T[1, :, 0] += 5   # apart
        self.assertEqual(len(untangle.crossings(T)), 0)

    def test_untangle_leaves_convex_parts_alone(self):
        # A box's walls never cross: nothing is scaled, BumpMesh's result kept exactly.
        sys.path.insert(0, os.path.dirname(__file__))
        from test_paint import box

        V = np.asarray(box(), float).reshape(-1, 3)
        from texturizer.engine.meshindex import weld

        vid, _n, firsts = weld(V, 1e5)
        W = V[firsts]
        F = vid.reshape(-1, 3)
        N = W - W.mean(0)
        N /= np.linalg.norm(N, axis=1)[:, None]
        self.assertFalse(untangle.concave(W, F, N).any())
        np.testing.assert_array_equal(untangle.untangle(W, N * 0.5, F, N), np.ones(len(W)))

    def test_fix_caps_keeps_it_closed(self):
        # A closed patch with one cap: its corner 70 µm off the opposite edge
        # (an edge at 45°, so the corner is inside the edge's box, as FreeCAD asks).
        a, b, r, s, t = [0, 0, 0], [2, 2, 0], [1, 1.0001, 0], [2, 0, 0], [1, 1, 1]
        tris = [[a, b, r], [b, a, s], [a, r, t], [r, b, t], [b, s, t], [s, a, t]]
        pos = np.asarray(tris, np.float32).reshape(-1)
        self.assertEqual(len(freecad_points_on_edges(pos)), 1)
        out, n = fix_caps(pos)
        self.assertEqual(n, 1)
        self.assertEqual(freecad_points_on_edges(out), set())
        self.assertEqual(count_edge_defects(out)["open"], 0)

    def test_near_edges_is_freecads_criterion(self):
        # On an export with points on edges (before printable), the fast check
        # finds exactly what FreeCAD's rule, written plainly, finds.
        from texturizer.engine.meshindex import weld

        d = self.knurl()
        d.set("refineLength", 2.0)   # small enough for the plain all-pairs check
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            raw = engine.texture(round_hole(r=1.9), handlers.run_settings(d, {}), mode="export")
        P = np.asarray(raw["positions"], float).reshape(-1, 3)
        vid, _n, firsts = weld(P, 1e5)
        fast = {(int(v), int(f)) for v, f, _k in untangle.near_edges(P[firsts], vid.reshape(-1, 3)).tolist()}
        plain = freecad_points_on_edges(raw["positions"])
        self.assertTrue(plain)   # there are some to find
        self.assertEqual(fast, plain)
        res = engine.printable(raw)
        self.assertEqual(freecad_points_on_edges(res["positions"]), set())   # and printable takes them out

    def knurl(self):
        d = draft.Draft(part="Body")
        d.set_texture("Knurling")
        return d

    def test_printable_clears_points_on_edges_and_keeps_it_closed(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            raw = engine.texture(round_hole(r=1.9), handlers.run_settings(self.knurl(), {}), mode="export")
        from texturizer.engine.meshrepair import points_on_edges

        self.assertGreater(points_on_edges(raw["positions"]), 0)   # BumpMesh's output has them
        res = engine.printable(raw)
        self.assertEqual(points_on_edges(res["positions"]), 0)    # FreeCAD's rule (the test above checks it is)
        self.assertEqual(count_edge_defects(res["positions"]), {"open": 0, "nonManifold": 0, "tris": len(res["positions"]) // 9})
        self.assertEqual(len(untangle.crossings(res["positions"])), 0)

if __name__ == "__main__":
    unittest.main()
