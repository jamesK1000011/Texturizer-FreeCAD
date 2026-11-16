# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The preview's displace stage after a Rotate ring drag (on the Mac, a
40 x 30 x 20 mm box with Knurling took 1.7 s from release): the same result
as the first port's path (displacement.FAST = False), faster.

  - one preview, or the same inputs again: bit-identical (the weld kept from
    the last run is used only when it is this input's weld; the sampling's
    wraps are the fmods' values, without the divisions; the drawable's vertex
    normals sum with bincount in np.add.at's order);
  - a drag's frames: the blend-normal smoothing kept from the last run is
    turned by the frame, not rerun (it averages and normalises, so it turns
    with the frame), so positions sit within float32 rounding of the
    reference, far inside the parity tests' TOL_MM.

TEXTURIZER_BENCH=1 prints the milliseconds against the reference path (which
shares the faster sampling, so it understates the gain over the first port)."""
import os
import sys
import time
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from texturizer import draft, engine  # noqa: E402
from texturizer.engine import displacement as D  # noqa: E402
from texturizer.engine.meshindex import weld  # noqa: E402
from test_paint import BOX_FT, box  # noqa: E402

BOX = box()
FRAMES = [[a, a / 2, 0] for a in range(5, 65, 5)]
TOL_MM = 1e-3   # test_engine_parity's


def start(texture="Knurling"):
    d = draft.Draft(part="Body")
    d.set_texture(texture)
    r = engine.preview({"draft": d.to_dict(), "mesh": {"positions": BOX}, "face_triangles": BOX_FT})
    return d, r["stages"]["subdivide"]


def frame(d, sub, rot, progress=None):
    d.set_frame(rotation=rot)
    si = {"draft": d.to_dict(), "mesh": {"positions": BOX}, "face_triangles": BOX_FT,
          "rerun_from": "map", "cache": {"subdivide": sub}}
    return engine.preview(si, progress)


def drag(fast, texture="Knurling"):
    saved = D.FAST
    D.FAST = fast
    D._KEEP.clear()
    try:
        d, sub = start(texture)
        return [frame(d, sub, rot)["mesh"] for rot in FRAMES]
    finally:
        D.FAST = saved


def reference_sample(R, w, h, u, v):
    """sampleBilinear as first ported (np.fmod wraps)."""
    u = np.fmod(np.fmod(u, 1) + 1, 1)
    v = 1 - np.fmod(np.fmod(v, 1) + 1, 1)
    fx, fy = u * w - 0.5, v * h - 0.5
    x0, y0 = np.floor(fx), np.floor(fy)
    tx, ty = fx - x0, fy - y0
    x0, y0 = x0.astype(np.int64), y0.astype(np.int64)
    x1, y1 = np.fmod(x0 + 1 + w, w), np.fmod(y0 + 1 + h, h)
    x0, y0 = np.fmod(np.fmod(x0, w) + w, w), np.fmod(np.fmod(y0, h) + h, h)
    return (R[y0 * w + x0] * (1 - tx) * (1 - ty) + R[y0 * w + x1] * tx * (1 - ty)
            + R[y1 * w + x0] * (1 - tx) * ty + R[y1 * w + x1] * tx * ty)


class SameResult(unittest.TestCase):
    def test_sampling_is_the_fmod_path(self):
        rng = np.random.default_rng(3)
        for w, h in ((512, 512), (300, 200), (1, 7)):
            R = rng.random(w * h)
            u, v = rng.random(50000) * 8 - 4, rng.random(50000) * 8 - 4
            edge = [0.0, -0.0, 1.0, -1.0, 2.0, -2.5, 0.5 / w, 1 - 0.5 / w, 1 - 1e-17, -1e-300]
            u[:len(edge)], v[:len(edge)] = edge, edge[::-1]
            np.testing.assert_array_equal(D.sample_bilinear(R, w, h, u, v), reference_sample(R, w, h, u, v))

    def test_weld_kept_only_when_it_is_the_weld(self):
        D._KEEP.clear()
        P = np.repeat(np.random.default_rng(4).random((500, 3)) * 40, 3, axis=0)
        for M in (np.eye(3), np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1.0]])):
            Q = P @ M.T
            got, want = D._weld(Q), weld(Q, D.QUANT)
            np.testing.assert_array_equal(got[0], want[0])
            self.assertEqual((got[1], list(got[2])), (want[1], list(want[2])))
        Q = P.copy()
        Q[3] = Q[0] + 2e-6   # two groups now share a cell: the kept weld no longer holds
        np.testing.assert_array_equal(D._weld(Q)[0], weld(Q, D.QUANT)[0])

    def test_one_preview_is_bit_identical(self):
        for texture in ("Knurling", "Dots"):
            got = {}
            for fast in (False, True):
                saved, D.FAST = D.FAST, fast
                D._KEEP.clear()
                try:
                    d = draft.Draft(part="Body")
                    d.set_texture(texture)
                    d.set_frame(rotation=[20, 10, 0])
                    got[fast] = engine.preview({"draft": d.to_dict(), "mesh": {"positions": BOX},
                                                "face_triangles": BOX_FT})["mesh"]
                finally:
                    D.FAST = saved
            for k in ("vertices", "normals", "faces", "weights", "colours"):
                with self.subTest(texture=texture, key=k):
                    np.testing.assert_array_equal(got[False][k], got[True][k])

    def test_same_frame_again_is_bit_identical(self):
        ref = drag(False)[-1]
        d, sub = start()
        D._KEEP.clear()   # the frame's first run computes; the second takes what it kept
        frame(d, sub, FRAMES[-1])
        again = frame(d, sub, FRAMES[-1])["mesh"]   # an amplitude or texture change reruns on it
        for k in ("vertices", "normals", "weights"):
            np.testing.assert_array_equal(ref[k], again[k])

    def test_a_drag_matches_the_reference(self):
        ref, got = drag(False), drag(True)
        for i, (a, b) in enumerate(zip(ref, got)):
            with self.subTest(frame=FRAMES[i]):
                self.assertEqual(a["vertices"].shape, b["vertices"].shape)
                np.testing.assert_array_equal(a["faces"], b["faces"])
                np.testing.assert_array_equal(a["weights"], b["weights"])
                self.assertLess(float(np.abs(a["vertices"] - b["vertices"]).max()), TOL_MM / 10)
                # A vertex normal whose corners nearly cancel is ill-conditioned (a
                # float32 ulp can turn it); measured: at most 0.25 % of vertices
                # differ by over 1e-3 on a frame, none on most.
                off = np.abs(a["normals"] - b["normals"]).max(1) > 1e-3
                self.assertLess(off.mean(), 1e-2)

    def test_frame_change_reuses_the_kept_smoothing(self):
        D._KEEP.clear()
        d, sub = start()
        frame(d, sub, [10, 0, 0])
        kept = D._KEEP["smooth"]
        frame(d, sub, [20, 0, 0])
        self.assertIs(D._KEEP["smooth"], kept)   # turned, not rerun


class SubdivideKept(unittest.TestCase):
    """The subdivide stage depends on the part and its own settings, not on
    the texture or its scale: a texture switch (or an Undo of one) that
    reruns from subdivide takes the kept mesh, the same arrays a new run
    makes."""

    def runs(self):
        calls = []
        real = engine._preview_subdivide

        def counted(*a, **k):
            calls.append(1)
            return real(*a, **k)
        return calls, real, counted

    def test_texture_switch_reuses_and_matches(self):
        calls, real, counted = self.runs()
        engine._SUBDIVIDED.clear()
        engine._preview_subdivide = counted
        try:
            d = draft.Draft(part="Body")
            got = {}
            for tex in ("Knurling", "Hexagons", "Knurling"):
                d.set_texture(tex, defaults={"scaleU": 7.5 if tex == "Knurling" else 50})
                got.setdefault(tex, []).append(engine.preview({"draft": d.to_dict(), "mesh": {"positions": BOX},
                                                               "face_triangles": BOX_FT})["mesh"])
            self.assertEqual(len(calls), 1)
            d.set("refineLength", 2.0)   # its own setting: subdivided again
            engine.preview({"draft": d.to_dict(), "mesh": {"positions": BOX}, "face_triangles": BOX_FT})
            self.assertEqual(len(calls), 2)
            d.mask_add(faces=["Face1"])  # with Preserve Untextured, the faces no layer textures count
            engine.preview({"draft": d.to_dict(), "mesh": {"positions": BOX}, "face_triangles": BOX_FT})
            self.assertEqual(len(calls), 3)
        finally:
            engine._preview_subdivide = real
        engine._SUBDIVIDED.clear()   # a fresh run gives the same arrays
        d = draft.Draft(part="Body")
        d.set_texture("Knurling", defaults={"scaleU": 7.5})
        fresh = engine.preview({"draft": d.to_dict(), "mesh": {"positions": BOX}, "face_triangles": BOX_FT})["mesh"]
        for k in ("vertices", "normals", "faces", "weights"):
            np.testing.assert_array_equal(fresh[k], got["Knurling"][1][k])


@unittest.skipUnless(os.environ.get("TEXTURIZER_BENCH"), "set TEXTURIZER_BENCH=1 to time the displace stage")
class Bench(unittest.TestCase):
    def test_displace_ms(self):
        out = {}
        for fast in (False, True):
            saved = D.FAST
            D.FAST = fast
            D._KEEP.clear()
            try:
                d, sub = start()
                ms = []
                for rot in FRAMES:
                    snap, best = dict(D._KEEP), 1e9
                    for _ in range(3):
                        D._KEEP.clear()
                        D._KEEP.update(snap)
                        marks = []
                        frame(d, sub, rot, lambda s, f: marks.append((s, time.perf_counter())))
                        t = [m for s, m in marks if s == "displace"]
                        best = min(best, (t[-1] - t[0]) * 1000)
                    ms.append(best)
            finally:
                D.FAST = saved
            out[fast] = float(np.median(ms))
        print(f"\ndisplace after a ring move (median of {len(FRAMES)} frames): "
              f"reference {out[False]:.1f} ms, now {out[True]:.1f} ms, {out[False] / out[True]:.2f}x")


if __name__ == "__main__":
    unittest.main()
