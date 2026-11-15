# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""Save / Load Project (.bumpmesh): a round trip through the draft; a
project made by BumpMesh's own Save Project code (main.js, run in node by
tests/tools/bm_js.mjs) read here; a project written here read by BumpMesh's
own import code; BumpMesh's key lists; legacy (pre-layer) projects."""
import base64
import copy
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(HERE, "tools"))

import numpy as np  # noqa: E402

import gen_controls  # noqa: E402
from texturizer import draft, project  # noqa: E402

SRC = os.environ.get("BUMPMESH_SRC", gen_controls.DEFAULT_SRC)
HAVE_JS = os.path.exists(os.path.join(SRC, "js", "main.js")) and shutil.which("node")
BM_JS = os.path.join(HERE, "tools", "bm_js.mjs")
SKIP_JS = "node or the BumpMesh checkout ($BUMPMESH_SRC) is missing"


def run_js(cmd, spec=None):
    with tempfile.TemporaryDirectory() as tmp:
        args = ["node", BM_JS, SRC, cmd]
        if spec is not None:
            path = os.path.join(tmp, "spec.json")
            with open(path, "w") as fh:
                json.dump(spec, fh)
            args.append(path)
        r = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise AssertionError(r.stderr[-2000:])
    return json.loads(r.stdout)


def png(w=2, h=1, seed=0):
    """A small valid greyscale PNG."""
    raw = b"".join(b"\x00" + bytes((seed + x + y) % 256 for x in range(w)) for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def box(sx=20, sy=30, sz=10):
    v = [(0, 0, 0), (sx, 0, 0), (sx, sy, 0), (0, sy, 0), (0, 0, sz), (sx, 0, sz), (sx, sy, sz), (0, sy, sz)]
    f = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return np.asarray([[v[a], v[b], v[c]] for a, b, c in f], dtype=np.float64)


MODEL = box()
PNG_A, PNG_B = png(3, 2, 1), png(2, 2, 9)


def two_layer_draft():
    d = draft.Draft(part="Body")
    d.update({"mappingMode": 3, "scaleU": 12.5, "lockScale": False, "scaleV": 40, "amplitude": 0.8,
              "invertDisplacement": True, "rotation": 30, "cylinderCenterX": 1.5, "cylinderCenterY": -2,
              "cylinderRadius": 9, "boundaryFalloff": 1.5, "boundaryFalloffCurve": "scurve", "stampOnce": True})
    d.update({"stampCenterX": 1.0, "stampCenterY": 2.0, "stampCenterZ": 3.0, "stampSide": -1})
    d.update({"refineLength": 0.4, "maxTriangles": 1200000, "bottomAngleLimit": 12, "topAngleLimit": 3,
              "smoothBottom": False, "harvestTol": 0.01})
    d.set_texture("Knurling")
    d.mask_add(triangles=[0, 1, 5])
    d.mask_add(faces=["Face6"])
    d.add_layer()
    d.set_texture("my-scan")
    d.update({"mappingMode": 0, "textureSmoothing": 4, "capAngle": 33})
    d.set_layer(1, blendAdd=True, solo=True, name="Logo")
    d.mask_add(triangles=[2, 3])
    d.set_frame(rotation=[90, 0, 45])
    d.set_active(1)
    return d


def tex_png(tid):
    return {"my-scan": PNG_A}.get(tid)


def faces(f):
    return {"Face6": [10, 11]}.get(f, [])


class TestRoundTrip(unittest.TestCase):
    def test_all(self):
        d = two_layer_draft()
        names = set(project.build(d, model=MODEL, texture_png=tex_png, face_triangles=faces))
        self.assertEqual(names, {"settings.json", "model.stl", "paint.json", "mask.json", "texture.png", "texture-1.png"})
        buf = io.BytesIO()
        project.save(buf, d, model=MODEL, texture_png=tex_png, face_triangles=faces)
        e = draft.Draft()
        got = {}
        res = project.load(buf.getvalue(), e, "all", import_texture=lambda n, b: got.setdefault(n, b) and "lib:" + n)
        self.assertEqual(res["mode"], "all")
        self.assertEqual(res["warnings"], [])
        np.testing.assert_allclose(res["model"], MODEL, atol=1e-5)
        self.assertEqual(got, {"my-scan": PNG_A})
        self.assertEqual(e.active, 1)
        self.assertEqual(e.settings, d.settings)
        self.assertEqual(e.frame["rotation"], [90, 0, 45])
        for a, b in zip(d.layers, e.layers):
            self.assertEqual(a["settings"], b["settings"])
            for k in ("id", "name", "visible", "solo", "blendAdd", "includeOnly"):
                self.assertEqual(a[k], b[k], k)
        self.assertEqual(e.layers[0]["texture"], "Knurling")
        self.assertEqual(e.layers[1]["texture"], "lib:my-scan")
        self.assertEqual(e.get("stampCenterZ", 0), 3.0)
        self.assertEqual(e.get("stampSide", 0), -1)
        self.assertIsNone(e.get("stampNormalX", 0))
        # B-rep faces come back as faces, their triangles as painted triangles.
        self.assertEqual(e.layers[0]["mask"], {"faces": ["Face6"], "triangles": [0, 1, 5, 10, 11]})
        self.assertEqual(e.layers[1]["mask"], {"faces": [], "triangles": [2, 3]})
        e.undo()  # one undo step back to the fresh draft
        self.assertEqual(len(e.layers), 1)

    def test_options_and_settings_only(self):
        d = two_layer_draft()
        files = project.build(d, model=MODEL, include_model=False, include_texture=False, texture_png=tex_png)
        self.assertEqual(set(files), {"settings.json"})
        self.assertFalse(project.read(_zip(files)).has_model)
        files = project.build(d, model=MODEL, texture_png=tex_png)
        e = two_layer_draft()
        e.set_part("Other")
        res = project.load(_zip(files), e, "settings")
        self.assertIsNone(res["model"])
        self.assertEqual(e.part, "Other")
        self.assertEqual([L["mask"]["triangles"] for L in e.layers], [[], []])  # layered: fresh layers, as BumpMesh
        self.assertEqual(e.layers[0]["settings"], d.layers[0]["settings"])

    def test_not_a_project(self):
        with self.assertRaises(ValueError):
            project.read(b"solid x\nendsolid x\n")

    def test_pose(self):
        for rot in ([0, 0, 0], [90, 0, 45], [10, -20, 30], [0, 90, 0]):
            q = project.euler_to_quat(rot)
            np.testing.assert_allclose(project.euler_to_quat(project.quat_to_euler(q)), q, atol=1e-9)


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in files.items():
            z.writestr(n, b)
    return buf.getvalue()


class TestLegacyAndPaint(unittest.TestCase):
    def test_pre_layer_project(self):
        """A project from before layers (BumpMesh 1.x layout, built by hand
        from importProject's reading of it): top-level settings with relative
        scales, mask.json, texture.png."""
        data = {"version": 1, "mappingMode": 5, "scaleU": 0.5, "scaleV": 0.25, "textureHeight": 1.25,
                "amplitude": -1.25, "invertDisplacement": True, "bottomAngleLimit": 20, "activeMapName": "rock.png"}
        files = {"settings.json": json.dumps(data).encode(), "model.stl": project.stl_bytes(MODEL),
                 "mask.json": json.dumps({"selectionMode": True, "excluded": [4, 2, 99, -1, 2]}).encode(),
                 "texture.png": PNG_B}
        d = draft.Draft()
        res = project.load(_zip(files), d, "all")
        L = d.layer()
        self.assertEqual(L["mask"]["triangles"], [2, 4])
        self.assertTrue(L["includeOnly"])
        self.assertEqual(L["texture"], "rock.png")
        self.assertEqual(res["textures"], {0: ("rock.png", PNG_B)})
        self.assertEqual(L["settings"]["amplitude"], 1.25)
        self.assertEqual(d.settings["bottomAngleLimit"], 20)
        # Relative scales x the model's largest side (30 mm), as _migrateSnapshotScaleToMm.
        self.assertEqual((L["settings"]["scaleU"], L["settings"]["scaleV"]), (15.0, 7.5))
        self.assertFalse(L["settings"]["stampOnce"])

    def test_settings_only_keeps_the_mask_of_a_pre_layer_project(self):
        d = draft.Draft()
        d.mask_add(triangles=[7])
        project.load(_zip({"settings.json": json.dumps({"version": 1, "scaleUnit": "mm", "scaleU": 9}).encode()}), d)
        self.assertEqual(d.layer()["mask"]["triangles"], [7])
        self.assertEqual(d.get("scaleU"), 9)

    def test_decode_split_paint(self):
        """Paint with split triangles (the circle brush): codes depth first,
        a base triangle counts when half its leaves are painted."""
        b = lambda a: base64.b64encode(bytes(a)).decode()
        # Base 0: split in 2 (mask 1), leaves painted 1, 0.  Base 1: split in 4
        # (mask 7) with child 0 split again in 2; leaves 0,0 | 0 | 0 | 1.  Base 2: leaf.
        codes = [1, 0, 0, 7, 1, 0, 0, 0, 0, 0, 0]
        states = [0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 1]
        j = {"version": 1, "baseTriCount": 3, "nodeCount": len(codes), "codes": b(codes), "layerIds": [5],
             "states": [b(states)], "cov": [None]}
        self.assertEqual(project.decode_paint(j), {5: [0, 2]})
        self.assertIsNone(project.decode_paint({"version": 2}))


@unittest.skipUnless(HAVE_JS, SKIP_JS)
class TestBumpMeshInterop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.K = run_js("keys")

    def test_key_lists_match_main_js(self):
        self.assertEqual(list(project.PERSISTED_KEYS), self.K["PERSISTED_KEYS"])
        self.assertEqual(list(project.LAYER_KEYS), self.K["LAYER_KEYS"])
        self.assertEqual(list(project.GLOBAL_KEYS), self.K["GLOBAL_KEYS"])
        self.assertEqual(project.PROJECT_VERSION, self.K["PROJECT_VERSION"])

    def _layer(self, **over):
        s = {k: self.K["settings"][k] for k in self.K["LAYER_KEYS"]}
        s.update(over, scaleUnit="mm")
        if "textureHeight" in over:
            s["amplitude"] = (-1 if s["invertDisplacement"] else 1) * over["textureHeight"]
        return s

    def test_read_a_bumpmesh_project(self):
        """A project written by BumpMesh's own Save Project handler."""
        L0 = self._layer(mappingMode=3, scaleU=14, scaleV=14, textureHeight=0.9, invertDisplacement=True,
                         cylinderCenterX=2.5, cylinderCenterY=-1, cylinderRadius=11, stampOnce=True, stampCenterX=4,
                         stampCenterY=5, stampCenterZ=6, stampSide=1, boundaryFalloffCurve="linear")
        L1 = self._layer(mappingMode=1, scaleU=6, scaleV=9, lockScale=False, textureHeight=0.3, rotation=45,
                         textureSmoothing=2, capAngle=40)
        q = project.euler_to_quat([0, 30, 90])
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "p.bumpmesh")
            spec = {"settings": {"refineLength": 0.3, "maxTriangles": 2000000, "bottomAngleLimit": 9,
                                 "harvestFlatFaces": False, "noDownwardZ": True},
                    "layers": [{"id": 3, "settings": L0, "mapName": "Knurling", "png": None, "includeOnly": False,
                                "visible": True, "blendAdd": False, "paint": [0, 4, 7]},
                               {"id": 8, "settings": L1, "mapName": "scan.png", "png": base64.b64encode(PNG_A).decode(),
                                "includeOnly": True, "visible": False, "blendAdd": True, "paint": [1, 2]}],
                    "activeLayer": 1, "positions": MODEL.reshape(-1).tolist(), "poseRotation": q,
                    "includeModel": True, "includeTexture": True, "out": out}
            r = run_js("export", spec)
            self.assertEqual(r.get("alerts"), [], r)
            with zipfile.ZipFile(out) as z:
                self.assertEqual(set(z.namelist()), {"settings.json", "model.stl", "paint.json", "mask.json",
                                                     "texture.png", "texture-1.png"})
            d = draft.Draft()
            res = project.load(out, d, "all", import_texture=lambda n, b: "custom:" + n)
        self.assertEqual(res["warnings"], [])
        np.testing.assert_allclose(res["model"], MODEL, atol=1e-4)
        self.assertEqual(d.active, 1)
        np.testing.assert_allclose(project.euler_to_quat(d.frame["rotation"]), q, atol=1e-9)
        for i, (L, src) in enumerate(zip(d.layers, spec["layers"])):
            s = src["settings"]
            with self.subTest(layer=i):
                self.assertEqual(L["id"], src["id"])
                for k, v in L["settings"].items():
                    want = s["textureHeight"] if k == "amplitude" else s[k]
                    self.assertEqual(v, want, k)
                for k in project.STAMP_KEYS:
                    self.assertEqual(L["settings"][k], s[k], k)
                self.assertEqual(L["mask"]["triangles"], src["paint"])
                for k in ("includeOnly", "visible", "blendAdd"):
                    self.assertEqual(L[k], src[k], k)
        self.assertEqual(d.layers[0]["texture"], "Knurling")
        self.assertEqual(d.layers[1]["texture"], "custom:scan.png")
        for k, v in spec["settings"].items():
            self.assertEqual(d.settings[k], v, k)

    def test_bumpmesh_reads_our_project(self):
        """A project written here, through BumpMesh's own import code."""
        d = two_layer_draft()
        d.set_layer(0, visible=False)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "ours.bumpmesh")
            project.save(out, d, model=MODEL, texture_png=tex_png, face_triangles=faces)
            r = run_js("import", {"file": out, "mode": "all"})
            # Settings only on a loaded 12-triangle model keeps nothing of our paint.
            r2 = run_js("import", {"file": out, "mode": "settings",
                                   "current": {"positions": MODEL.reshape(-1).tolist(), "layers": [{"id": 1, "paint": [9]}]}})
        self.assertEqual(r["alerts"], [])
        self.assertEqual(r["loadMode"], "all")
        self.assertEqual(r["triCount"], len(MODEL))
        self.assertEqual(r["activeLayer"], 1)
        np.testing.assert_allclose(project.quat_to_euler(r["poseRotation"]), [90, 0, 45], atol=1e-9)
        self.assertEqual(len(r["layers"]), 2)
        for L, B in zip(d.layers, r["layers"]):
            snap = project._layer_snapshot(L)
            with self.subTest(layer=L["id"]):
                for k in self.K["LAYER_KEYS"]:
                    self.assertEqual(B["settings"][k], snap[k], k)
                self.assertEqual(B["settings"]["textureHeight"], L["settings"]["amplitude"])
                want = sorted(set(L["mask"]["triangles"]) | {t for f in L["mask"]["faces"] for t in faces(f)})
                self.assertEqual(B["painted"], want)
                for k in ("includeOnly", "visible", "blendAdd"):
                    self.assertEqual(bool(B[k]), bool(L[k]), k)
        self.assertEqual(r["layers"][0]["mapEntry"], {"name": "Knurling", "custom": False})
        self.assertEqual(r["layers"][1]["mapEntry"], {"name": "my-scan", "custom": True})
        self.assertEqual(r["layers"][0]["settings"]["stampCenterZ"], 3.0)
        self.assertEqual(r["layers"][0]["settings"]["amplitude"], -0.8)
        for k in ("refineLength", "maxTriangles", "bottomAngleLimit", "topAngleLimit", "smoothBottom", "harvestTol"):
            self.assertEqual(r["settings"][k], d.settings[k], k)
        self.assertEqual(r2["loadMode"], "settings")
        self.assertEqual([L["painted"] for L in r2["layers"]], [[], []])


if __name__ == "__main__":
    unittest.main()
