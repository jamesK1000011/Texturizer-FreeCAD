# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The shader Quick look: uvmath against BumpMesh's own mapping.js (run in
node), the GLSL 1.20 sources, uniforms from a draft layer, and detection
without a GL context (never raises)."""
import json
import math
import os
import re
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
from texturizer import draft, quicklook, quicklook_glsl, uvmath  # noqa: E402

SRC = os.environ.get("BUMPMESH_SRC", gen_controls.DEFAULT_SRC)
HAVE_JS = os.path.exists(os.path.join(SRC, "js", "mapping.js")) and shutil.which("node")
GLSLANG = shutil.which("glslangValidator")
UV_JS = os.path.join(HERE, "tools", "uv_js.mjs")

BOUNDS = {"min": [-12.0, -7.0, -3.0], "size": [30.0, 20.0, 40.0]}
BOUNDS["center"] = [m + s / 2 for m, s in zip(BOUNDS["min"], BOUNDS["size"])]

BASE = dict(draft.layer_defaults(), offsetU=0.13, offsetV=-0.4)
VARIANTS = [
    {},
    {"rotation": 30, "scaleU": 7.5, "scaleV": 11},
    {"rotation": 90, "rotateAroundZ": True, "mappingBlend": 0.5, "seamBandWidth": 0.2},
    {"mappingBlend": 0, "textureAspectU": 1.6, "rotation": -135, "rotateAroundZ": True},
    {"cylinderCenterX": 1.5, "cylinderCenterY": -2, "cylinderRadius": 9, "capAngle": 35, "seamBandWidth": 0.9},
]
MODES = [0, 1, 2, 3, 4, 5, 6, 8]


def _points(rng, n):
    lo = np.array(BOUNDS["min"])
    p = lo + rng.random((n, 3)) * np.array(BOUNDS["size"])
    nrm = rng.normal(size=(n, 3))
    # axis faces, 45-degree ties and points near the atan2 seam (-x side)
    axes = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1],
                     [1, 1, 0], [0, -1, 1], [1, 0, 1], [1, 1, 1]], float)
    nrm[: len(axes)] = axes
    c = np.array(BOUNDS["center"])
    k = n // 4
    p[-k:, 0] = c[0] - rng.random(k) * 10 - 0.5
    p[-k:, 1] = c[1] + (rng.random(k) - 0.5) * 2.0
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
    return np.hstack([p, nrm])


def _circ(a, b):
    d = abs(a - b) % 1.0
    return min(d, 1.0 - d)


def _match(js, py, tol):
    """Every JS sample with weight > 1e-9 has a uvmath sample of the same
    weight and UV (UV compared on the unit circle: both are fract'ed), and
    the weights cover the same total."""
    py = [s for s in py if s[2] > 1e-9]
    js = [s for s in js if s[2] > 1e-9]
    if abs(sum(s[2] for s in js) - sum(s[2] for s in py)) > tol:
        return False
    used = set()
    for u, v, w in js:
        for i, (pu, pv, pw) in enumerate(py):
            if i not in used and abs(pw - w) <= tol and _circ(pu, u) <= tol and _circ(pv, v) <= tol:
                used.add(i)
                break
        else:
            return False
    return True


@unittest.skipUnless(HAVE_JS, "node or the BumpMesh checkout ($BUMPMESH_SRC) is missing")
class UvMathAgainstJs(unittest.TestCase):
    def test_modes_match_mapping_js(self):
        rng = np.random.default_rng(7)
        cases = []
        for mode in MODES:
            for var in VARIANTS:
                pts = _points(rng, 40)
                cases.append({"mode": mode, "settings": dict(BASE, **var), "bounds": BOUNDS,
                              "points": pts.tolist()})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cases.json")
            with open(path, "w") as f:
                json.dump(cases, f)
            out = subprocess.run(["node", UV_JS, SRC, path], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-400:])
        js = json.loads(out.stdout)
        per_mode = {}
        for case, res in zip(cases, js):
            p = np.array(case["points"])
            U, V, W = uvmath.compute_uv(p[:, :3], p[:, 3:], case["mode"], case["settings"], BOUNDS)
            for i, samples in enumerate(res):
                py = [(U[i, k], V[i, k], W[i, k]) for k in range(3)]
                ok = _match(samples, py, 1e-6)
                self.assertTrue(ok, f"mode {case['mode']} {case['settings']} point {p[i].tolist()}:"
                                    f" js {samples} py {py}")
                per_mode[case["mode"]] = per_mode.get(case["mode"], 0) + 1
        self.assertTrue(all(n >= 200 for n in per_mode.values()), per_mode)


class UvMathPure(unittest.TestCase):
    def test_weights_sum_to_one_and_uv_tiles(self):
        rng = np.random.default_rng(3)
        p = _points(rng, 64)
        for mode in MODES:
            U, V, W = uvmath.compute_uv(p[:, :3], p[:, 3:], mode, dict(BASE, mappingBlend=0.6), BOUNDS)
            np.testing.assert_allclose(W.sum(axis=1), 1.0, atol=1e-5)
            self.assertTrue(((U >= 0) & (U < 1)).all() and ((V >= 0) & (V < 1)).all())

    def test_planar_is_world_anchored(self):
        s = dict(BASE, scaleU=10, scaleV=10, offsetU=0, offsetV=0)
        p = np.array([[-12.0, -7, 0], [-7.0, -2, 0]])
        U, V, _ = uvmath.compute_uv(p, [[0, 0, 1]] * 2, 0, s, BOUNDS)
        np.testing.assert_allclose(U[:, 0], [0, 0.5], atol=1e-12)
        np.testing.assert_allclose(V[:, 0], [0, 0.5], atol=1e-12)

    def test_blend_helper(self):
        U, V, W = uvmath.compute_uv([[0, 0, 0]], [[0, 0, 1]], 0, BASE, BOUNDS)
        self.assertAlmostEqual(uvmath.blend(U, V, W, lambda u, v: np.ones_like(u))[0], 1.0)


class Glsl(unittest.TestCase):
    def test_glsl_120_only(self):
        for name, text in (("vertex", quicklook_glsl.VERTEX), ("fragment", quicklook_glsl.FRAGMENT)):
            code = quicklook_glsl.strip_comments(text).replace("gl_LightSource[0]", "gl_LightSource")
            self.assertTrue(code.lstrip().startswith("#version 120"), name)
            for bad in (r"\btexture\s*\(", r"\btextureLod\b", r"^\s*(in|out)\s", r"\blayout\b",
                        r"\bflat\b", r"\buint\b", r"\bswitch\b", r"\bprecision\b", r"\bfor\s*\(",
                        r"\bwhile\b", r"\[", r"\bhalf\b", r"\bsmooth\b"):
                self.assertIsNone(re.search(bad, code, re.M), f"{name} uses {bad}")
            if name == "vertex":
                self.assertNotIn("texture2D", code)  # no vertex texture fetch
        frag = quicklook_glsl.FRAGMENT
        for k in range(4):
            self.assertIn(f"uMap{k}", frag)
        for want in ("dFdx", "dFdy", "gl_LightSource[0]", "gl_Color", "gl_FrontFacing"):
            self.assertIn(want, frag + quicklook_glsl.VERTEX)
        self.assertIn("Stefan Hermann", quicklook_glsl.HEADER)
        self.assertIn("CNC Kitchen", quicklook_glsl.HEADER)
        self.assertIn("AGPL", quicklook_glsl.HEADER)

    def test_uniform_names_match(self):
        """Every uniform the builder sets is declared, and the reverse."""
        decl = set(re.findall(r"uniform\s+\w+\s+(\w+)\s*;", quicklook_glsl.FRAGMENT + quicklook_glsl.VERTEX))
        u = quicklook.uniforms_from_layers([draft.Draft().layer()], {"bounds": BOUNDS})
        self.assertEqual(set(u) | {f"uMap{k}" for k in range(4)}, decl)

    @unittest.skipUnless(GLSLANG, "glslangValidator is not installed")
    def test_glslang_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            for ext, text in (("vert", quicklook_glsl.VERTEX), ("frag", quicklook_glsl.FRAGMENT)):
                path = os.path.join(tmp, "ql." + ext)
                with open(path, "w") as f:
                    f.write(text)
                r = subprocess.run([GLSLANG, path], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stdout[-800:])


class Uniforms(unittest.TestCase):
    def test_from_draft_layer(self):
        d = draft.Draft()
        d.set("mappingMode", 3)
        d.set("scaleU", 12)
        d.set("scaleV", 12)
        d.set("rotation", 45)
        d.set("amplitude", 0.8)
        d.set("symmetricDisplacement", True)
        d.set_layer(0, blendAdd=True)
        L = d.layer()
        u = quicklook.uniforms_from_layer(L, {"bounds": BOUNDS}, index=0)
        r = max(BOUNDS["size"][0], BOUNDS["size"][1]) * 0.5
        rel = 12 / (2 * math.pi * r)
        self.assertEqual(u["uMode0"], 3.0)
        self.assertAlmostEqual(u["uScale0"][0], rel)
        self.assertAlmostEqual(u["uScale0"][1], rel)
        self.assertAlmostEqual(u["uRot0"], math.pi / 4)
        self.assertAlmostEqual(u["uAmp0"], 0.8)
        self.assertEqual(u["uSym0"], 1.0)
        self.assertEqual(u["uAdd0"], 0.0)  # the bottom layer never adds (BumpMesh)
        self.assertEqual(quicklook.uniforms_from_layer(L, {"bounds": BOUNDS}, index=1)["uAdd1"], 1.0)
        self.assertEqual(list(u["uCyl0"]), [BOUNDS["center"][0], BOUNDS["center"][1], r])
        self.assertAlmostEqual(u["uSeam0"][0], L["settings"]["mappingBlend"])
        self.assertEqual(u["uSeam0"][2], 20.0)

    def test_outline_wrap_falls_back_and_invert(self):
        d = draft.Draft()
        d.set("mappingMode", 8)
        d.set("invertDisplacement", True)
        u = quicklook.uniforms_from_layer(d.layer(), {"bounds": BOUNDS}, index=1)
        self.assertEqual(u["uMode1"], 3.0)
        self.assertLess(u["uAmp1"], 0)

    def test_layers_frame_and_hidden(self):
        d = draft.Draft()
        d.add_layer()
        d.add_layer()
        d.set_layer(1, visible=False)
        rot = [0, 0, 90]
        u = quicklook.uniforms_from_layers(d.layers, {"bounds": BOUNDS, "rotation": rot}, active=2)
        self.assertEqual(u["uLayerCount"], 2.0)  # the hidden layer is left out
        self.assertEqual(u["uActive"], 1.0)
        m = np.array(u["uFrame"]).reshape(4, 4)
        np.testing.assert_allclose(m[:3, :3] @ [1, 0, 0], [0, 1, 0], atol=1e-12)
        self.assertEqual(u["uAmp2"], 0.0)  # unused slots are inert
        np.testing.assert_allclose(u["uBoundsMin"], BOUNDS["min"])

    def test_solo_wins(self):
        d = draft.Draft()
        d.add_layer()
        d.set_layer(1, solo=True)
        u = quicklook.uniforms_from_layers(d.layers, {"bounds": BOUNDS}, active=d.active)
        self.assertEqual(u["uLayerCount"], 1.0)

    def test_weights_pack(self):
        rgba = quicklook.pack_weights(np.array([[1.0, 0.5, 0.0, 0.25]]))
        self.assertEqual(int(rgba[0]), (255 << 24) | (128 << 16) | (0 << 8) | 64)


class Detect(unittest.TestCase):
    def test_never_raises_headless(self):
        params = {}
        res = quicklook.detect(None, params=params, fc_version="1.1.3")
        self.assertFalse(res["ok"])
        self.assertTrue(res["reason"])
        self.assertEqual(quicklook.detect(None, params=params, fc_version="1.1.3"), res)

    def test_cached_by_version_and_gpu(self):
        calls = []

        def probe(view):
            calls.append(1)
            return {"gl": "2.1 Metal", "glsl": "1.20", "gpu": "Apple M2", "errors": [], "pixels_ok": True}

        params = {}
        a = quicklook.detect("view", params=params, fc_version="1.1.3", probe=probe)
        self.assertTrue(a["ok"], a)
        quicklook.detect("view", params=params, fc_version="1.1.3", probe=probe)
        self.assertEqual(len(calls), 1)
        quicklook.detect("view", params=params, fc_version="1.2.0", probe=probe)
        self.assertEqual(len(calls), 2)

    def test_failures(self):
        cases = [
            ({"gl": "1.4", "glsl": "", "gpu": "x", "errors": [], "pixels_ok": True}, "GL"),
            ({"gl": "2.1", "glsl": "1.10", "gpu": "x", "errors": [], "pixels_ok": True}, "GLSL"),
            ({"gl": "2.1", "glsl": "1.20", "gpu": "x", "errors": ["link failed"], "pixels_ok": True}, "link"),
            ({"gl": "2.1", "glsl": "1.20", "gpu": "x", "errors": [], "pixels_ok": False}, "probe"),
        ]
        for info, word in cases:
            res = quicklook.detect("v", params={}, fc_version="1", probe=lambda v, i=info: i)
            self.assertFalse(res["ok"])
            self.assertIn(word, res["reason"])

        def boom(v):
            raise RuntimeError("no context")

        res = quicklook.detect("v", params={}, fc_version="1", probe=boom)
        self.assertFalse(res["ok"])
        self.assertIn("no context", res["reason"])

    def test_setting_off(self):
        res = quicklook.detect("v", params={"UseQuickLook": False}, fc_version="1",
                               probe=lambda v: self.fail("probed"))
        self.assertFalse(res["ok"])

    def test_default_probe_without_pivy_or_gl(self):
        res = quicklook.detect(object(), params={}, fc_version="1.1.3")
        self.assertFalse(res["ok"])
        self.assertIn("probe failed", res["reason"])

    def test_versions(self):
        self.assertEqual(quicklook.parse_version("2.1 Metal - 88"), (2, 1))
        self.assertEqual(quicklook.parse_version("4.1 INTEL-20.4.11"), (4, 1))
        self.assertEqual(quicklook.parse_version("1.20"), (1, 20))
        self.assertEqual(quicklook.parse_version(""), (0, 0))


class Scene(unittest.TestCase):
    """The Coin builder against a stand-in coin module (pivy is not needed)."""

    def test_builds_and_updates(self):
        from unittest import mock

        coin = mock.MagicMock()
        ql = quicklook.QuickLook(coin)
        self.assertEqual(coin.SoTextureUnit.call_count, 4)
        self.assertEqual(coin.SoTexture2.call_count, 4)
        self.assertEqual(ql.root.whichChild, -1)
        ql.set_mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)], [(0, 0, 1)] * 3)
        ql.faces.coordIndex.setValues.assert_called_with(0, 4, [0, 1, 2, -1])
        ql.set_weights([(1, 0, 0, 0)] * 3)
        u = quicklook.uniforms_from_layers([draft.Draft().layer()], {"bounds": BOUNDS})
        ql.set_uniforms(u)
        self.assertIn("uFrame@vs", ql.params)
        n = coin.SoShaderParameter1f.call_count
        u2 = dict(u, uAmp0=0.9)
        ql.set_uniforms(u2)
        self.assertEqual(coin.SoShaderParameter1f.call_count, n)  # existing nodes reused
        self.assertEqual(ql.params["uAmp0"].value, 0.9)
        ql.set_image(0, b"\x80" * 4, 2, 2)
        ql.show(True)
        self.assertEqual(ql.root.whichChild, -3)


if __name__ == "__main__":
    unittest.main()
