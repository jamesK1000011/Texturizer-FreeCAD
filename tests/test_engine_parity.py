# SPDX-License-Identifier: AGPL-3.0-only
"""The Python engine against BumpMesh's own (tests/reference/refs.json, made by
make_refs.mjs from stlTexturizer's engine/index.js under Node).

Every case is expected bit-identical (the sha256 of the float32 output). If
one is not, the digest is still checked within stated tolerances so a small
drift shows as a number rather than a hash: triangle count exact, bounds and
mean within TOL_MM, sampled vertices within TOL_MM."""
import base64
import hashlib
import json
import os
import time
import unittest

import numpy as np

from texturizer import engine
from texturizer.engine.exclusion import from_triangles

HERE = os.path.dirname(os.path.abspath(__file__))
REFS = os.path.join(HERE, "reference", "refs.json")
TOL_MM = 1e-3
EXACT = True  # every case matched to the bit when this was written


def _load():
    with open(REFS, encoding="utf-8") as fh:
        return json.load(fh)


class EngineParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.refs = _load()
        cls.meshes = {k: np.frombuffer(base64.b64decode(v), np.float32) for k, v in cls.refs["meshes"].items()}

    def run_case(self, case):
        pos = self.meshes[case["mesh"]]
        n = len(pos) // 9
        layers = []
        for L in case["layers"]:
            m = L.get("mask")
            layers.append({"texture": L["texture"], "settings": L["settings"], "blendAdd": L["blendAdd"],
                           "mask": None if not m else from_triangles(n, m["tris"], m["mode"])})
        return engine.texture(pos, {**case["settings"], "layers": layers})

    def test_cases(self):
        for case in self.refs["cases"]:
            with self.subTest(case=case["name"]):
                t0 = time.time()
                r = self.run_case(case)
                ref = case["result"]
                p = np.asarray(r["positions"], np.float32)
                self.assertEqual(r["triangles"], ref["triangles"])
                self.assertEqual(r["inputTriangles"], ref["inputTriangles"])
                self.assertEqual(r["repair"], ref["repair"])
                P = p.reshape(-1, 3).astype(np.float64)
                np.testing.assert_allclose(P.min(0), ref["min"], atol=TOL_MM)
                np.testing.assert_allclose(P.max(0), ref["max"], atol=TOL_MM)
                np.testing.assert_allclose(P.mean(0), ref["mean"], atol=TOL_MM)
                got = p.reshape(-1, 9)[ref["sampleIdx"]]
                np.testing.assert_allclose(got, np.array(ref["sample"]), atol=TOL_MM)
                if EXACT:
                    self.assertEqual(hashlib.sha256(p.tobytes()).hexdigest(), ref["sha256"], "not bit-identical")
                self.assertLess(time.time() - t0, 120)


if __name__ == "__main__":
    unittest.main()
