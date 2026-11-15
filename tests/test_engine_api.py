# SPDX-License-Identifier: AGPL-3.0-only
"""texturizer.engine's interface (BumpMesh's engine, ported) and the
texture_list / texture_settings / texture_apply handlers, with no FreeCAD:
the body's tessellation is stood in for."""
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import traceback
import types
import unittest
import zipfile
from unittest import mock

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from texturizer import engine, handlers  # noqa: E402
from texturizer.engine import imageio, writers  # noqa: E402

def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


REFS = json.loads(_read(os.path.join(HERE, "reference", "refs.json")))
MESH = {k: np.frombuffer(base64.b64decode(v), np.float32) for k, v in REFS["meshes"].items()}
CUBE, CYL = MESH["cube"], MESH["cylinder"]
FAST = {"refineLength": 2.0, "harvestFlatFaces": False}


class Library(unittest.TestCase):
    def test_entries(self):
        lib = [e for e in engine.library.list() if e["category"] != "custom"]
        self.assertEqual(len(lib), 95)
        names = {e["name"] for e in lib}
        for ff in ("Basket", "Brick", "Bubble", "Crystal", "Leather 2", "Weave 3"):
            self.assertNotIn(ff, names)  # Filter Forge: not redistributable here
        for e in lib:
            self.assertEqual(set(e), {"id", "name", "category", "thumb", "image", "credit", "licence", "source_url", "defaults"})
            self.assertIn(e["category"], engine.library.CATEGORIES)
            self.assertTrue(os.path.isfile(e["thumb"]) and os.path.isfile(e["image"]))
            self.assertTrue(e["licence"])
            self.assertIn("scaleU", e["defaults"])
        hero = [e for e in lib if e["credit"] == "hero"]
        self.assertEqual(len(hero), 29)
        self.assertTrue(all("Steve Schoger" in e["licence"] for e in hero))
        self.assertTrue(all(e["source_url"].startswith("https://") for e in lib if e["credit"] == "cc0"))

    def test_load_is_bumpmeshs_size(self):
        img = engine.library.load("knurling")
        self.assertEqual(max(img.width, img.height), 512)

    def test_custom_dir(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "mine.png"), "wb") as fh:
            fh.write(imageio.encode_png(np.tile(np.arange(64, dtype=np.uint8), (64, 1))))
        engine.library.add_custom_dir(d)
        e = engine.library.get("custom:mine.png")
        self.assertEqual((e["category"], e["credit"]), ("custom", "custom"))
        self.assertEqual(engine.library.load(e).width, 64)

    def test_licences_file_and_privacy_list(self):
        lic = _read(os.path.join(ROOT, "texturizer", "textures", "LICENSES.md"))
        self.assertIn("Hero Patterns by Steve Schoger", lic)
        images = os.path.join(ROOT, ".github", "privacy", "images.txt")
        if not os.path.isfile(images):
            self.skipTest("not a git checkout (installed add-on: no .github)")
        listed = _read(images)
        for e in engine.library.list():
            if e["category"] == "custom":
                continue
            self.assertIn(os.path.relpath(e["thumb"], ROOT), listed)
            self.assertIn(os.path.relpath(e["image"], ROOT), listed)


class Interface(unittest.TestCase):
    def test_estimate_and_suggest(self):
        est = engine.estimate(CUBE, {"refineLength": 1.2})
        self.assertEqual(est["inputTriangles"], 12)
        self.assertGreater(est["subdividedTriangles"], 4000)
        self.assertEqual(est["memoryBytes"], est["subdividedTriangles"] * engine.BYTES_PER_TRIANGLE)
        sug = engine.smart_resolution(CUBE, {"texture": "Knurling", "scaleU": 6})
        self.assertGreaterEqual(sug["edge"], 0.05)
        self.assertGreaterEqual(sug["maxTriangles"], 10_000)

    def test_fit_cylinder(self):
        f = engine.fit_cylinder(CYL)
        self.assertAlmostEqual(f["cylinderRadius"], 10, delta=0.05)
        self.assertAlmostEqual(f["cylinderCenterX"], 0, delta=1e-6)

    def test_masks(self):
        m = engine.by_normal(CUBE, (0, 0, 1), 5, "include")
        self.assertEqual(m.triangles.tolist(), [2, 3])
        self.assertEqual(sorted(engine.flood(CUBE, 2, 20).triangles.tolist()), [2, 3])
        self.assertEqual(len(engine.flood(CUBE, 2, 90).triangles), 12)
        self.assertEqual(engine.by_box(CUBE, (-100, -100, 9), (100, 100, 11)).triangles.tolist(), [2, 3])
        self.assertEqual(engine.from_triangles(12, [0, 5]).union(engine.from_triangles(12, [5, 7])).triangles.tolist(), [0, 5, 7])

    def test_frame_round_trip(self):
        r0 = engine.texture(CUBE, {**FAST, "texture": "Dots", "scaleU": 5})
        r1 = engine.texture(CUBE, {**FAST, "texture": "Dots", "scaleU": 5, "frame": {"rotation": [0, 0, 90]}})
        b0 = r0["positions"].reshape(-1, 3)
        b1 = r1["positions"].reshape(-1, 3)
        np.testing.assert_allclose(b1.min(0), CUBE.reshape(-1, 3).min(0) - 0.6, atol=0.7)
        np.testing.assert_allclose(b1.max(0), b0.max(0), atol=0.6)  # back in the part's frame

    def test_frame_matrix_is_threejs_xyz(self):
        R = engine.frame_matrix([90, 0, 90])
        # three.js Euler(90°, 0, 90°, 'XYZ') takes +X to +Z... about Z first: X -> Y, then about X: Y -> Z.
        np.testing.assert_allclose(R @ [1, 0, 0], [0, 0, 1], atol=1e-12)

    def test_abort_and_progress(self):
        seen = []
        self.assertIsNone(engine.texture(CUBE, {**FAST, "texture": "Dots"}, should_abort=lambda: True))
        engine.texture(CUBE, {**FAST, "texture": "Dots"}, progress=lambda s, f, i=None: seen.append((s, f)))
        self.assertEqual([s for s, _ in seen][0], "subdivide1")
        self.assertTrue(all(0 <= f <= 1 for _, f in seen))

    def test_pipeline_reuses_stages(self):
        p = engine.Pipeline(CUBE, {**FAST, "texture": "Dots", "textureHeight": 0.4})
        a = p.run()
        sub = p.results["subdivide"]
        p.settings["textureHeight"] = 0.8
        p.rerun("map")
        p.results["subdivide"] = sub  # a height change keeps the subdivided mesh
        b = p.run()
        self.assertIs(p.results["subdivide"], sub)
        self.assertEqual(a["triangles"], b["triangles"])
        self.assertGreater(np.ptp(b["positions"]), np.ptp(a["positions"]))

    def test_export_stl_and_3mf(self):
        d = tempfile.mkdtemp()
        res = engine.texture(CUBE, {**FAST, "texture": "Bubbles", "scaleU": 5})
        s = engine.export(CUBE, {"texture": "Bubbles"}, None, os.path.join(d, "a.stl"), result=res)
        self.assertEqual(os.path.getsize(s["path"]), 84 + 50 * res["triangles"])
        self.assertIn("Steve Schoger", s["credit"])
        t = engine.export(CUBE, {"texture": "Bubbles"}, None, os.path.join(d, "a.3mf"), result=res)
        with zipfile.ZipFile(t["path"]) as z:
            model = z.read("3D/3dmodel.model").decode()
        self.assertEqual(model.count("<triangle "), res["triangles"])
        self.assertIn("Hero Patterns", model)


class Preview(unittest.TestCase):
    DRAFT = {"settings": {"refineLength": 1.5}, "frame": {"rotation": [0, 0, 0], "face": None}, "active": 1,
             "layers": [{"texture": "Knurling", "settings": {"scaleU": 5, "textureHeight": 0.5}, "visible": True,
                         "mask": {"faces": ["Top"], "triangles": []}, "includeOnly": False},
                        {"texture": "Dots", "settings": {"scaleU": 6, "textureHeight": 0.3, "textureSmoothing": 2},
                         "blendAdd": True, "visible": True, "mask": {"faces": [], "triangles": []}}]}

    def inputs(self, start="subdivide", cache=None, budget=200_000, limit=1 << 30):
        return {"draft": self.DRAFT, "rerun_from": start, "cache": cache or {}, "tri_budget": budget,
                "memory_limit": limit, "mesh": {"positions": CUBE, "face_triangles": {"Top": [2, 3]}}}

    def test_contract(self):
        stages = []
        r = engine.preview(self.inputs(), lambda s, f: stages.append(s))
        self.assertEqual(sorted(r["stages"]), sorted(engine.STAGES))
        self.assertEqual(sorted(set(stages)), sorted(engine.STAGES))
        m = r["mesh"]
        n = len(m["vertices"])
        self.assertEqual(m["weights"].shape, (n, 4))
        self.assertEqual(m["normals"].shape, (n, 3))
        self.assertEqual(m["colours"].shape, (n, 3))
        self.assertLessEqual(int(m["faces"].max()), n - 1)
        self.assertEqual([x.dtype for x in m["maps"]], [np.uint8, np.uint8])
        self.assertEqual([x.shape for x in m["maps"]], [(512, 512), (512, 512)])
        # The masked top: layer 1's weight 0 there, layer 2's not.
        top = m["vertices"][:, 2] > 10 - 1e-4
        inner = top & (np.abs(m["vertices"][:, 0] - 3) < 5) & (np.abs(m["vertices"][:, 1] - 10.5) < 4)
        self.assertTrue((m["weights"][inner, 0] == 0).all())
        self.assertTrue((m["weights"][inner, 1] > 0).all())
        self.assertEqual(r["mesh"]["bounds"]["size"], (20.0, 15.0, 10.0))

    def test_rerun_and_abort(self):
        r = engine.preview(self.inputs())
        cache = {k: r["stages"][k] for k in ("subdivide", "map")}
        r2 = engine.preview(self.inputs("displace", cache))
        self.assertEqual(sorted(r2["stages"]), ["colours", "displace"])
        np.testing.assert_array_equal(r2["mesh"]["vertices"], r["mesh"]["vertices"])
        self.assertIsNone(engine.preview(self.inputs(), None, lambda: True))

    def test_memory_note(self):
        r = engine.preview(self.inputs(limit=1 << 20))
        self.assertIn("coarsened", r["note"])

    def test_engine_object(self):
        eng = engine.Engine(mesh_for=lambda part: (CUBE, {"Top": [2, 3]}))
        si = self.inputs()
        si.pop("mesh")
        self.assertIsNotNone(eng.preview(si)["mesh"])


class FakeShape:
    class BB:
        XLength, YLength, ZLength, DiagonalLength = 20.0, 15.0, 10.0, 26.9

    BoundBox = BB()
    Faces = [None] * 6


class FakeBody:
    Label = "Block"
    Name = "Block"
    Shape = FakeShape()


class Ctx:
    def __init__(self):
        self.out_dir = tempfile.mkdtemp()

    def body(self, ref):
        return FakeBody()


class Handlers(unittest.TestCase):
    def setUp(self):
        tris = CUBE.reshape(-1, 3, 3).tolist()
        per = {f"Face{i + 1}": [2 * i, 2 * i + 2] for i in range(6)}
        self._ft = handlers.mesh.face_triangles
        handlers.mesh.face_triangles = lambda shape, tol: (tris, per)

    def tearDown(self):
        handlers.mesh.face_triangles = self._ft

    def test_list_and_settings(self):
        out = handlers.texture_list(Ctx(), {"category": "grip"})
        self.assertTrue(out["count"] >= 3 and all(t["category"] == "grip" for t in out["textures"]))
        with self.assertRaises(handlers.ToolError):
            handlers.texture_list(Ctx(), {"category": "nope"})
        st = handlers.texture_settings(Ctx(), {})
        self.assertEqual(st["mappingModes"][8], "outlineWrap")
        st = handlers.texture_settings(Ctx(), {"body": "Block", "texture": "Knurling", "settings": {"scaleU": 5}})
        self.assertGreater(st["body"]["estimate"]["subdividedTriangles"], 0)
        self.assertIn("refineLength", st["body"]["suggest"])

    APPLY = {"texture": "Knurling", "faces": ["Face2"], "mode": "exclude",
             "settings": {"refineLength": 2, "scaleU": 5, "harvestFlatFaces": False}, "export": "block.stl"}

    def apply(self, ctx, a):
        # The traceback in the failure message: the bake-off's log keeps only
        # the summary lines.
        try:
            return handlers.texture_apply(ctx, a)
        except Exception:  # noqa: BLE001
            self.fail(traceback.format_exc())

    def test_apply(self):
        ctx = Ctx()
        out = self.apply(ctx, self.APPLY)
        self.assertEqual(out["layers"][0], {"layer": 1, "mode": "exclude", "faces": ["Face2"]})
        self.assertGreater(out["triangles"], 12)
        self.assertTrue(os.path.isfile(out["export"]["path"]))
        self.assertIsNone(out["object"])  # the body is in no document: no mesh object, FreeCAD or not
        self.assertIs(handlers.LAST["applied"], out)
        for bad in ({"texture": "nope"}, {"texture": "Dots", "settings": {"bogus": 1}},
                    {"texture": "Dots", "settings": {"mappingMode": 12}}, {"layers": [{}] * 5}):
            with self.assertRaises(handlers.ToolError, msg=str(bad)):
                handlers.texture_apply(ctx, bad)
        with self.assertRaises(handlers.ToolError):
            handlers.texture_apply(ctx, {"texture": "Dots", "settings": {"refineLength": 0.001}})


    def test_apply_layers_take_top_level_faces_and_mode(self):
        # The live check: layers given with top-level faces / mode textured
        # the whole body. A layer without them takes them; a layer that
        # differs, or a setting outside settings, is refused.
        base = {"faces": ["Face6"], "mode": "include", "settings": {"refineLength": 2, "harvestFlatFaces": False}}
        out = self.apply(Ctx(), {**base, "layers": [{"texture": "Knurling", "settings": {"textureHeight": 0.4}}]})
        self.assertEqual(out["layers"], [{"layer": 1, "mode": "include", "faces": ["Face6"]}])
        out = self.apply(Ctx(), {**base, "layers": [{"texture": "Knurling", "faces": "Face6", "mode": "include"}]})
        self.assertEqual(out["layers"][0]["faces"], ["Face6"])
        out = self.apply(Ctx(), {"texture": "Knurling", "settings": {"refineLength": 2}, "layers": [{"faces": ["Face1"]}]})
        self.assertEqual(out["layers"][0], {"layer": 1, "mode": "exclude", "faces": ["Face1"]})
        for bad, word in (({**base, "layers": [{"texture": "Knurling", "faces": ["Face2"]}]}, "faces"),
                          ({**base, "layers": [{"texture": "Knurling", "mode": "exclude"}]}, "mode"),
                          ({"texture": "Dots", "layers": [{"texture": "Knurling"}]}, "texture"),
                          ({**base, "layers": [{"texture": "Knurling", "textureHeight": 0.4}]}, "layers.0.settings"),
                          ({**base, "layers": [{"texture": "Knurling", "bogus": 1}]}, "not a layer argument")):
            with self.assertRaises(handlers.ToolError, msg=str(bad)) as e:
                handlers.texture_apply(Ctx(), bad)
            self.assertEqual(e.exception.code, "invalid_input")
            self.assertIn(word, e.exception.message if hasattr(e.exception, "message") else str(e.exception))

    def test_apply_with_freecads_mesh_module(self):
        # As in the bake-off, where FreeCAD's Mesh imports (another test
        # module imported FreeCAD): a body in no document still gives no
        # object, and never asks it for a Document it does not have.
        made = []
        fake = types.ModuleType("Mesh")
        fake.Mesh = lambda path: made.append(path) or object()
        with mock.patch.dict(sys.modules, {"Mesh": fake}):
            out = self.apply(Ctx(), self.APPLY)
        self.assertIsNone(out["object"])
        self.assertTrue(os.path.isfile(out["export"]["path"]))


class Plain(unittest.TestCase):
    def test_engine_imports_no_freecad_or_agent(self):
        code = ("import sys; sys.path.insert(0, %r); import texturizer.engine as e; e.library.list(); "
                "print(sorted(m for m in sys.modules if m.split('.')[0] in ('FreeCAD', 'FreeCADGui', 'agentlink', 'PySide', 'PIL')))" % ROOT)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "[]")

    def test_headers_credit_bumpmesh(self):
        d = os.path.join(ROOT, "texturizer", "engine")
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".py"):
                head = _read(os.path.join(d, fn))[:600]
                self.assertIn("BumpMesh by Stefan Hermann (CNC Kitchen)", head, fn)
                self.assertIn("AGPL-3.0", head, fn)


if __name__ == "__main__":
    unittest.main()
