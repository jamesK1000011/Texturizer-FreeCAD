# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The GUI wired to the engine, FreeCAD absent: the agent's draft tools move
the one draft the sidebar shows, texture_apply makes a re-texturable object
that goes out of date with its body and reruns only on Update, the mesh_for
adapter, the rotation order the Quick look and the engine share, the default
texture and favourites we ship."""
import json
import math
import os
import sys
import tempfile
import types
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from texturizer import controls, engine, handlers, live, quicklook  # noqa: E402


def cube(size=10.0):
    """A closed cube, 2 triangles a face, wound outward: positions and Face1..6."""
    s = size
    v = np.array([[0, 0, 0], [s, 0, 0], [s, s, 0], [0, s, 0], [0, 0, s], [s, 0, s], [s, s, s], [0, s, s]], float)
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (0, 4, 7, 3)]
    tris = []
    for a, b, c, d in quads:
        tris += [v[[a, b, c]], v[[a, c, d]]]
    pos = np.asarray(tris, np.float32).reshape(-1)
    return pos, {f"Face{k + 1}": [2 * k, 2 * k + 1] for k in range(6)}


class Box:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeShape:
    def __init__(self, volume=1000.0):
        self.Faces = [object()] * 6
        self.Solids = [object()]
        self.Volume, self.Area = volume, 600.0
        self.BoundBox = Box(XMin=0, YMin=0, ZMin=0, XMax=10, YMax=10, ZMax=10, DiagonalLength=17.32)


class Obj:
    def __init__(self, doc, name, label, shape=None):
        self.Document, self.Name, self.Label, self.Shape = doc, name, label, shape
        self.PropertiesList = []

    def addProperty(self, kind, prop, group, text):  # noqa: N802 (FreeCAD)
        self.PropertiesList.append(prop)
        setattr(self, prop, False if kind == "App::PropertyBool" else "")


class Doc:
    Name = "Unnamed"

    def __init__(self):
        self.Objects = []

    def addObject(self, kind, name):  # noqa: N802 (FreeCAD)
        n, k = name, 1
        while any(o.Name == n for o in self.Objects):
            n, k = f"{name}{k:03d}", k + 1
        o = Obj(self, n, n)
        self.Objects.append(o)
        return o

    def recompute(self):
        pass


class FakeMesh:
    def __init__(self, positions):
        self.positions = np.asarray(positions, np.float32)
        self.CountFacets = len(self.positions) // 9

    @property
    def Topology(self):  # noqa: N802 (FreeCAD)
        p = self.positions.reshape(-1, 3)
        return [Box(x=float(x), y=float(y), z=float(z)) for x, y, z in p], [tuple(range(k, k + 3)) for k in range(0, len(p), 3)]


class Session(unittest.TestCase):
    """A document with one body, the engine's mesh from cube(), no Mesh module."""

    def setUp(self):
        live.reset()
        self.doc = Doc()
        self.body = Obj(self.doc, "Body", "Grip", FakeShape())
        self.doc.Objects.append(self.body)
        self.ctx = handlers.Context(doc=self.doc, out_dir=tempfile.mkdtemp())
        self.run = handlers.local_runner(self.ctx)
        self._saved = (live.mesh_for, handlers._make_mesh, live.GUI)
        live.mesh_for = lambda part, doc=None, tol=None: cube()
        handlers._make_mesh = FakeMesh
        live.GUI = None

    def tearDown(self):
        live.mesh_for, handlers._make_mesh, live.GUI = self._saved
        live.reset()

    def ok(self, tool, args):
        ok, out = self.run(tool, args)
        self.assertTrue(ok, out)
        return out


class DraftTools(Session):
    def test_tools_move_the_draft_the_sidebar_shows(self):
        events = []
        d = live.draft()
        d.subscribe(events.append)  # the sidebar subscribes the same way (Sidebar.__init__)
        out = self.ok("texture_draft", {})
        self.assertEqual((out["draft"]["part"], out["mesh"]), ("Body", {"triangles": 12, "faces": 6}))
        self.ok("texture_set", {"texture": "Knurling", "settings": {"amplitude": 0.8, "mappingMode": "cubic"}})
        self.assertIs(live.draft(), d)
        self.assertEqual((d.layer()["texture"], d.get("amplitude"), d.get("mappingMode")), ("Knurling", 0.8, 6))
        self.assertEqual(d.get("scaleU"), live_scale("Knurling", 10))  # BumpMesh's tile for a 10 mm part
        out = self.ok("texture_mask", {"mode": "include", "add": ["Face2", "Face3"]})
        self.assertEqual((out["mode"], out["faces"]), ("include", ["Face2", "Face3"]))
        self.ok("texture_layer", {"action": "add"})
        self.ok("texture_layer", {"action": "blend", "layer": 1, "value": "add"})
        kinds = [(e["kind"], e.get("key")) for e in events]
        for want in [("part", None), ("texture", "texture"), ("set", "amplitude"), ("mask", "includeOnly"), ("mask", "add"),
                     ("layers", None), ("layer", None)]:
            self.assertIn(want, kinds)
        self.assertTrue(d.layers[1]["blendAdd"])
        ok, out = self.run("texture_set", {"settings": {"notASetting": 1}})
        self.assertEqual((ok, out["error"]["code"]), (False, "invalid_input"))
        ok, out = self.run("texture_layer", {"action": "blend", "layer": 0, "value": "add"})
        self.assertFalse(ok)
        d.undo()
        self.assertFalse(d.layers[1]["blendAdd"])

    def test_sidebar_actions_are_answered(self):
        from texturizer import sidebar  # the action table only; no Qt needed for it

        self.ok("texture_draft", {})
        handled = set(handlers.HANDLERS)
        for key, (tool, _args) in sidebar.ACTIONS.items():
            self.assertIn(tool, handled, key)
        # Without the window, the buttons that need a pick refuse in words.
        for key in ("placeOnFace", "maskAddSelected", "stampPlace", "loadModel"):
            tool, args = sidebar.ACTIONS[key]
            ok, out = self.run(tool, dict(args, layer=0))
            self.assertFalse(ok, key)
            self.assertIn("window", out["error"]["message"], key)

    def test_window_hooks(self):
        opened = []

        class Gui:
            def open(self, load=False):
                opened.append(load)

            def selected_faces(self):
                return "Body", ["Face5"]

            def selected_object(self):
                return "Body"

        live.GUI = Gui()
        self.ok("texture_draft", {"load": "selection"})
        out = self.ok("texture_mask", {"add": "selected", "layer": 0})
        self.assertEqual(out["faces"], ["Face5"])
        self.assertGreaterEqual(len(opened), 2)  # each draft tool opens the sidebar

    def test_preview_and_suggest(self):
        self.ok("texture_draft", {})
        self.ok("texture_set", {"settings": {"refineLength": 2}})
        out = self.ok("texture_preview", {})
        self.assertGreater(out["triangles"], 12)
        self.assertEqual(out["inputTriangles"], 12)
        out = self.ok("texture_preview", {"suggest": True})
        self.assertEqual(live.draft().get("refineLength"), out["suggest"]["refineLength"])

    def test_export_files(self):
        self.ok("texture_draft", {})
        self.ok("texture_set", {"settings": {"refineLength": 2.5}})
        out = self.ok("texture_export", {"format": "stl", "path": "cube.stl"})
        self.assertTrue(os.path.getsize(out["path"]) > 84 + 50 * 12)
        out = self.ok("texture_export", {"format": "bumpmesh", "path": "cube.bumpmesh"})
        self.assertIn("settings.json", out["files"])
        self.assertIn("model.stl", out["files"])
        from texturizer import draft, project

        d2 = draft.Draft()
        project.load(out["path"], d2, mode="settings")
        self.assertEqual(d2.get("refineLength"), 2.5)


def live_scale(texture, size):
    e = engine.library.get(texture)
    return engine.library.default_scale_mm(e["defaults"]["tileFraction"], {"size": [size, size, size]})


class Retexture(Session):
    def test_apply_marks_out_of_date_and_update_clears(self):
        self.ok("texture_draft", {})
        self.ok("texture_set", {"texture": "Knurling", "settings": {"refineLength": 2.5}})
        out = self.ok("texture_apply", {})
        obj = live.find(self.doc, out["object"])
        self.assertFalse(out["updated"])
        self.assertEqual((obj.SourceBody, obj.OutOfDate), ("Body", False))
        self.assertEqual(json.loads(obj.TextureDraft)["layers"][0]["texture"], "Knurling")
        self.assertIn("Stefan Hermann (CNC Kitchen)", obj.Credit)
        before = obj.Mesh
        self.assertEqual(handlers.mark_out_of_date(self.doc), [])
        # The body changes: marked, never rerun on its own.
        self.body.Shape = FakeShape(volume=900.0)
        handlers.Observer().slotRecomputedObject(self.body)
        self.assertTrue(obj.OutOfDate)
        self.assertIs(obj.Mesh, before)
        # A second Add to document with the same draft updates the same object.
        out = self.ok("texture_apply", {"update": obj.Name})
        self.assertTrue(out["updated"])
        self.assertFalse(obj.OutOfDate)
        self.assertIsNot(obj.Mesh, before)
        self.assertEqual(obj.SourceSignature, live.signature(self.body))
        self.assertEqual(len(handlers.textured_objects(self.doc)), 1)
        # Textured again: its settings come back into the draft, its body the part.
        live.reset()
        live.mesh_for = lambda part, doc=None, tol=None: cube()
        out = self.ok("texture_draft", {"part": obj.Name})
        self.assertEqual((out["draft"]["part"], out["draft"]["layers"][0]["texture"]), ("Body", "Knurling"))
        self.assertEqual(live.TARGET["object"], obj.Name)
        self.ok("texture_set", {"settings": {"amplitude": 1.0}})
        out = self.ok("texture_apply", {})
        self.assertEqual((out["object"], out["updated"]), (obj.Name, True))
        out = self.ok("texture_apply", {"new": True})
        self.assertNotEqual(out["object"], obj.Name)

    def test_args_apply_updates_too(self):
        pos, _ft = cube()
        saved = handlers.mesh.face_triangles
        handlers.mesh.face_triangles = lambda shape, tol: (pos.reshape(-1, 3, 3).tolist(), {f"Face{k + 1}": [2 * k, 2 * k + 2] for k in range(6)})
        self.addCleanup(setattr, handlers.mesh, "face_triangles", saved)
        out = self.ok("texture_apply", {"texture": "Knurling", "settings": {"refineLength": 2.5}, "body": "Grip"})
        obj = live.find(self.doc, out["object"])
        self.assertEqual(json.loads(obj.TextureArgs)["texture"], "Knurling")
        self.body.Shape = FakeShape(volume=800.0)
        self.assertEqual(handlers.mark_out_of_date(self.doc), [obj.Name])
        out = self.ok("texture_apply", {"update": obj.Name})
        self.assertFalse(obj.OutOfDate)

    def test_check_a_textured_object(self):
        # texture_check from what texture_apply stored: no texture_prepare
        # mask, by object or by the STL texture_export wrote.
        pos, _ft = cube()
        saved = handlers.mesh.face_triangles
        handlers.mesh.face_triangles = lambda shape, tol: (pos.reshape(-1, 3, 3).tolist(), {f"Face{k + 1}": [2 * k, 2 * k + 2] for k in range(6)})
        self.addCleanup(setattr, handlers.mesh, "face_triangles", saved)
        out = self.ok("texture_apply", {"body": "Grip", "faces": ["Face2"], "mode": "include",
                                        "layers": [{"texture": "Knurling", "settings": {"textureHeight": 0.4}}],
                                        "settings": {"refineLength": 1}})
        self.assertIn("texture_check { object: \"%s\" }" % out["object"], out["next"])
        obj = live.find(self.doc, out["object"])
        chk = self.ok("texture_check", {"object": obj.Name})
        self.assertEqual((chk["object"], set(chk["texturedFaces"])), (obj.Name, {"Face2"}))
        self.assertGreater(chk["texturedFaces"]["Face2"]["moved"], 0.5)
        self.assertNotIn("Face1", chk.get("leaked", {}).get("faces", []))
        exp = self.ok("texture_export", {"format": "stl", "object": obj.Name, "path": "tile.stl"})
        self.assertIn("texture_check", exp["next"])
        chk2 = self.ok("texture_check", {"path": exp["path"]})
        self.assertEqual((chk2["object"], chk2["texturedFaces"].keys()), (obj.Name, chk["texturedFaces"].keys()))
        # A whole-body texture: the bottom, under BumpMesh's 5 degree limit, stays plain.
        out = self.ok("texture_apply", {"body": "Grip", "texture": "Knurling", "settings": {"refineLength": 2.5}, "name": "All"})
        chk = self.ok("texture_check", {"object": out["object"]})
        self.assertEqual(chk["angleMasked"], ["Face1"])
        self.assertNotIn("Face1", chk["texturedFaces"])
        # The old route still refuses without its mask, and names the new one.
        ok, err = self.run("texture_check", {"body": "Grip", "path": os.path.join(self.ctx.out_dir, "nothing.stl")})
        self.assertEqual((ok, err["error"]["code"]), (False, "not_found"))
        for bad in ({"object": "Grip"}, {"object": obj.Name, "mask": "x.json"}, {"object": obj.Name, "body": "Nope"}):
            ok, err = self.run("texture_check", bad)
            self.assertFalse(ok, bad)

    def test_bake(self):
        self.ok("texture_draft", {})
        self.ok("texture_set", {"settings": {"refineLength": 2.5}})
        out = self.ok("texture_apply", {"bake": True})
        self.assertEqual(live.draft().part, out["baked"])
        self.assertEqual(len(live.draft().layers), 1)

    def test_bake_masks_just_baked_faces(self):
        # BumpMesh's Mask just-baked faces: a cube with only Face5 (x = 10)
        # textured and baked; the next layer leaves that face as baked.
        self.ok("texture_draft", {})
        self.ok("texture_set", {"settings": {"refineLength": 2.5, "bakeMask": True, "bottomAngleLimit": 0}})
        d = live.draft()
        d.set("includeOnly", True, layer=0)
        d.mask_add(faces=["Face5"], layer=0)
        out = self.ok("texture_apply", {"bake": True})
        obj = live.find(self.doc, out["baked"])
        self.assertTrue(obj.Baked)
        tris = obj.Mesh.positions.reshape(-1, 3, 3)
        mask = d.layers[0]["mask"]
        self.assertFalse(d.layers[0]["includeOnly"])
        self.assertEqual(out["maskedTriangles"], len(mask["triangles"]))
        # The masked triangles are exactly the baked Face5: textured, near x = 10.
        on_face = [i for i, t in enumerate(tris) if t[:, 0].min() > 9.0]
        self.assertEqual(mask["triangles"], on_face)
        self.assertGreater(len(on_face), 2)
        # The next layer leaves it as baked: the engine only moves the vertices
        # its edges share with textured faces (the part stays closed); every
        # triangle from inside the masked face keeps its plane.
        res = engine.texture(obj.Mesh.positions, handlers.run_settings(d, {}), mode="bake")
        out_t = res["positions"].reshape(-1, 3, 3).astype(np.float64)
        parents = res["faceParentId"]
        key = lambda p: tuple(np.round(p, 4))  # noqa: E731
        masked = set(mask["triangles"])
        rest_v = {key(p) for i, t in enumerate(tris) if i not in masked for p in t}
        inner = [i for i in masked if not any(key(p) in rest_v for p in tris[i])]
        self.assertGreater(len(inner), 4)
        moved = 0
        for k in np.nonzero(np.isin(parents, inner))[0]:
            a, b, c = tris[parents[k]].astype(np.float64)
            n = np.cross(b - a, c - a)
            moved += np.abs((out_t[k] - a) @ (n / np.linalg.norm(n))).max() > 1e-4
        self.assertEqual(moved, 0)
        # The rest of the part got the new layer.
        rest = np.nonzero(~np.isin(parents, mask["triangles"]))[0]
        self.assertGreater(np.abs(out_t[rest][:, :, 0] - np.clip(out_t[rest][:, :, 0], 0, 10)).max()
                           + np.abs(out_t[rest][:, :, 1] - np.clip(out_t[rest][:, :, 1], 0, 10)).max(), 0.05)
        # Through texture_apply too: the mask resolves on the baked part.
        live.mesh_for = lambda part, doc=None, tol=None: (obj.Mesh.positions, {})
        self.assertIsNotNone(live.find(self.doc, self.ok("texture_apply", {"new": True})["object"]))

    def test_bake_without_mask(self):
        self.ok("texture_draft", {})
        self.ok("texture_set", {"settings": {"refineLength": 2.5, "bakeMask": False}})
        out = self.ok("texture_apply", {"bake": True})
        self.assertEqual((out["maskedTriangles"], live.draft().mask_count(0)), (0, 0))


# -- mesh_for ---------------------------------------------------------------------------
class V:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z

    def __sub__(self, o):
        return V(self.x - o.x, self.y - o.y, self.z - o.z)

    def cross(self, o):
        return V(self.y * o.z - self.z * o.y, self.z * o.x - self.x * o.z, self.x * o.y - self.y * o.x)

    def dot(self, o):
        return self.x * o.x + self.y * o.y + self.z * o.z

    @property
    def Length(self):  # noqa: N802 (FreeCAD)
        return math.sqrt(self.dot(self))


class Face:
    """A unit square in z = z0 facing n, tessellated in 2 triangles (one wound wrong when flip)."""

    def __init__(self, z0, up):
        self.z0, self.up = z0, up
        self.ParameterRange = (0, 1, 0, 1)

    def tessellate(self, tol):
        pts = [V(0, 0, self.z0), V(1, 0, self.z0), V(1, 1, self.z0), V(0, 1, self.z0)]
        return pts, [(0, 1, 2), (0, 2, 3)]

    def normalAt(self, u, v):  # noqa: N802 (FreeCAD)
        return V(0, 0, 1 if self.up else -1)


class Shape:
    def __init__(self):
        self.Faces = [Face(1, True), Face(0, False)]

    def tessellate(self, tol):
        pass


class CurvedFace(Face):
    """A face whose normal at the middle of its parameter range points the
    other way from where its triangles are (a hole's cylinder: the middle is
    across the axis); Surface.parameter finds the right place."""

    class _S:
        def parameter(self, p):
            return (0.1, 0.1)

    Surface = _S()

    def normalAt(self, u, v):  # noqa: N802 (FreeCAD)
        return V(0, 0, 1 if (u, v) == (0.1, 0.1) else -1)


class Orientation(unittest.TestCase):
    def fc(self):
        fc = types.ModuleType("FreeCAD")
        fc.Vector = V
        saved = sys.modules.get("FreeCAD")
        sys.modules["FreeCAD"] = fc
        self.addCleanup(lambda: sys.modules.__setitem__("FreeCAD", saved) if saved is not None else sys.modules.pop("FreeCAD"))

    def test_winding_checked_where_the_face_is(self):
        from texturizer import mesh

        self.fc()

        class S:
            Faces = [CurvedFace(1, True)]

            def tessellate(self, tol):
                pass
        tris, _ft = mesh.face_triangles(S(), 0.1)
        T = np.asarray(tris)
        n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
        self.assertTrue((n[:, 2] > 0).all())   # as the face's own normal there, not the far side's

    def test_orient_consistently(self):
        from texturizer import mesh

        pos, _ft = cube()
        T = pos.reshape(-1, 3, 3).astype(float)
        bad = T.copy()
        bad[4:6] = bad[4:6, ::-1]              # one face wound inward
        out = np.asarray(mesh.orient_consistently(bad))
        np.testing.assert_array_equal(out, T)
        inside_out = T[:, ::-1].copy()          # every face inward: turned outward
        np.testing.assert_array_equal(np.asarray(mesh.orient_consistently(inside_out)), T)
        np.testing.assert_array_equal(np.asarray(mesh.orient_consistently(T)), T)   # right stays right


class MeshFor(unittest.TestCase):
    def test_adapter_on_a_fake_shape(self):
        fc = types.ModuleType("FreeCAD")
        fc.Vector = V
        saved = sys.modules.get("FreeCAD")
        sys.modules["FreeCAD"] = fc
        try:
            pos, ft = live.adapter(Shape())
        finally:
            if saved is None:
                sys.modules.pop("FreeCAD")
            else:
                sys.modules["FreeCAD"] = saved
        self.assertEqual(pos.dtype, np.float32)
        self.assertEqual(len(pos), 4 * 9)
        self.assertEqual(ft, {"Face1": [0, 1], "Face2": [2, 3]})
        t = pos.reshape(-1, 3, 3).astype(float)
        n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        self.assertTrue((n[:2, 2] > 0).all() and (n[2:, 2] < 0).all())  # each face wound outward
        # The engine takes it as its mesh (the preview's GUI contract).
        e = live.engine_with(lambda part: (pos, ft))
        self.assertIs(e.mesh_for("Body")[0], pos)

    def test_mesh_for_caches_until_the_geometry_changes(self):
        calls = []
        saved = live.adapter
        live.adapter = lambda shape, tol=live.TOLERANCE: calls.append(1) or cube()
        try:
            doc = Doc()
            b = Obj(doc, "Body", "Grip", FakeShape())
            doc.Objects.append(b)
            live.mesh_for("Grip", doc)
            live.mesh_for("Body", doc)
            self.assertEqual(len(calls), 1)
            b.Shape = FakeShape(volume=5.0)
            live.mesh_for("Body", doc)
            self.assertEqual(len(calls), 2)
        finally:
            live.adapter = saved
            live.reset()


# -- rotation order ------------------------------------------------------------------------
class RotationOrder(unittest.TestCase):
    def test_quicklook_and_gui_use_the_engines_frame(self):
        for rot in ([30, 0, 0], [0, 40, 0], [0, 0, 50], [30, 40, 50], [-90, 15, 120]):
            m = np.array(quicklook.frame_matrix(rot)).reshape(4, 4)
            R = engine.frame_matrix(rot)
            np.testing.assert_allclose(m[:3, :3], R, atol=1e-12)
            np.testing.assert_allclose(m[:3, 3], 0)
        # three.js Euler 'XYZ': R = Rx·Ry·Rz, so Z turns first: X 90 then Z 90 differs from Z then X.
        x, z = engine.frame_matrix([90, 0, 0]), engine.frame_matrix([0, 0, 90])
        np.testing.assert_allclose(engine.frame_matrix([90, 0, 90]), x @ z, atol=1e-12)
        self.assertFalse(np.allclose(x @ z, z @ x))
        # The sidebar's Rotate X / Y / Z land in the draft's frame, which the engine reads as is.
        from texturizer import draft

        d = draft.Draft()
        d.set_frame(rotation=[30, 40, 50])
        s = handlers.run_settings(d, {})
        np.testing.assert_allclose(engine._rotation_of(s), engine.frame_matrix([30, 40, 50]))


# -- the window's pure parts ---------------------------------------------------------------------
class QuickLookMesh(unittest.TestCase):
    def test_indexed_per_face_with_mask_weights(self):
        from texturizer import window

        pos, ft = cube()
        pts, tri, nrm = window.quicklook_mesh(pos, ft)
        self.assertEqual((len(pts), len(tri)), (24, 12))  # 4 corners a face: sharp edges, exact masks
        np.testing.assert_allclose(np.linalg.norm(nrm, axis=1), 1)
        W = window.layer_weights(len(tri), tri, len(pts), [{"mask": {"faces": ["Face1"], "triangles": []}, "includeOnly": True},
                                                           {"mask": {"faces": ["Face1"], "triangles": []}}], ft)
        on = W[tri[:, 0], 0]
        self.assertEqual(on.tolist(), [1, 1] + [0] * 10)  # include only Face1
        self.assertEqual(W[tri[:, 0], 1].tolist(), [0, 0] + [1] * 10)  # exclude Face1
        self.assertEqual(W[:, 2:].max(), 0)


# -- library and defaults -----------------------------------------------------------------------
class Library(unittest.TestCase):
    def test_default_texture_and_favourites_are_shipped(self):
        names = {e["name"] for e in engine.library.list()}
        self.assertIn(controls.DEFAULT_TEXTURE, names)
        self.assertEqual(controls.DEFAULT_TEXTURE, "Knurling")
        self.assertTrue(set(controls.DEFAULT_FAVOURITES) <= names)
        for gone in ("Crystal", "Brick", "Leather 2"):
            self.assertIn(gone, controls.NOT_SHIPPED)
            self.assertNotIn(gone, controls.DEFAULT_FAVOURITES)

    def test_gallery_reads_the_engines_library(self):
        from texturizer import gallery

        entries = live.list_textures()
        cats = {c["id"] for c in controls.CATEGORIES}
        self.assertTrue({e["category"] for e in entries} <= cats | {gallery.CUSTOM})
        badges = {gallery.badge(e)[0] for e in entries}
        self.assertTrue({"HP", "CC0", "CNC Kitchen"} <= badges)
        ck = next(e for e in entries if e["credit"] == "CNC Kitchen (BumpMesh)")
        self.assertEqual(gallery.badge(ck)[0], "CNC Kitchen")
        self.assertEqual(gallery.badge({"id": "custom:x.png", "category": "custom", "credit": "custom"})[0], "Yours")
        for e in entries:
            self.assertTrue(e["thumb"].endswith(".png") and os.path.isfile(e["thumb"]), e["id"])
        fav = gallery.Favourites(path=None, entries=entries)
        self.assertEqual(fav.shown(entries), [n for n in controls.DEFAULT_FAVOURITES])


if __name__ == "__main__":
    unittest.main()
