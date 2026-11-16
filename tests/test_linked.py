# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The Textured object follows its body (docs/linked-texture.md), on a
stand-in document: a pad whose length changes textures again with its masks
mapped; a masked face that is gone is flagged, never misapplied; faces a
later pocket makes stay smooth and are listed until Include them; renaming
the body keeps the link; an old document's Textured object converts; the
background rerun writes only the newest result, outside any transaction."""
import json
import os
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from texturizer import handlers, linked, live  # noqa: E402
from test_wiring import Box, Doc, FakeMesh, Obj  # noqa: E402


def quad(a, b, c, d):
    return [[a, b, c], [a, c, d]]


def ring(z, outer, inner, up=True):
    """The planar region between two axis-aligned rectangles at height z."""
    (x0, y0, x1, y1), (u0, v0, u1, v1) = outer, inner
    O = [[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]]
    I_ = [[u0, v0, z], [u1, v0, z], [u1, v1, z], [u0, v1, z]]
    out = []
    for k in range(4):
        j = (k + 1) % 4
        out += quad(O[k], O[j], I_[j], I_[k]) if up else quad(O[k], I_[k], I_[j], O[j])
    return out


def pad(h=8.0, pocket=None, top_name="Face2"):
    """A 20 x 10 pad h high, face by face; pocket (depth): a 6 x 4 pocket in
    the top, its walls and floor new faces. Returns (triangles, faces)."""
    X, Y = 20.0, 10.0
    faces = {}
    faces["Face1"] = quad([0, 0, 0], [0, Y, 0], [X, Y, 0], [X, 0, 0])                     # bottom
    if pocket is None:
        faces[top_name] = quad([0, 0, h], [X, 0, h], [X, Y, h], [0, Y, h])                # top
    else:
        faces[top_name] = ring(h, (0, 0, X, Y), (7, 3, 13, 7))
    faces["Face3"] = quad([0, 0, 0], [X, 0, 0], [X, 0, h], [0, 0, h])                     # -Y
    faces["Face4"] = quad([X, 0, 0], [X, Y, 0], [X, Y, h], [X, 0, h])                     # +X
    faces["Face5"] = quad([X, Y, 0], [0, Y, 0], [0, Y, h], [X, Y, h])                     # +Y
    faces["Face6"] = quad([0, Y, 0], [0, 0, 0], [0, 0, h], [0, Y, h])                     # -X
    if pocket is not None:
        b = h - pocket
        faces["Face7"] = quad([7, 3, b], [13, 3, b], [13, 7, b], [7, 7, b])                # floor
        faces["Face8"] = quad([7, 3, b], [7, 3, h], [13, 3, h], [13, 3, b])
        faces["Face9"] = quad([13, 3, b], [13, 3, h], [13, 7, h], [13, 7, b])
        faces["Face10"] = quad([13, 7, b], [13, 7, h], [7, 7, h], [7, 7, b])
        faces["Face11"] = quad([7, 7, b], [7, 7, h], [7, 3, h], [7, 3, b])
    tris, ft = [], {}
    for name in sorted(faces, key=lambda f: int(f[4:])):
        ft[name] = list(range(len(tris), len(tris) + len(faces[name])))
        tris += faces[name]
    return np.asarray(tris, np.float32).reshape(-1), ft


class Shape:
    def __init__(self, h=8.0, pocket=None, maps=None):
        self.pos, self.ft = pad(h, pocket)
        self.Faces = [object()] * len(self.ft)
        self.Solids = [object()]
        self.Volume = 200.0 * h - (24.0 * pocket if pocket else 0)
        self.Area = 400.0 + 60.0 * h
        self.BoundBox = Box(XMin=0, YMin=0, ZMin=0, XMax=20, YMax=10, ZMax=h, DiagonalLength=22.0)
        if maps is not None:   # FreeCAD 1.x's mapped (toponaming) names
            self.ElementReverseMap = dict(maps)
            self.ElementMap = {v: k for k, v in maps.items()}


class LDoc(Doc):
    def __init__(self):
        super().__init__()
        self.HasPendingTransaction = False

    def removeObject(self, name):  # noqa: N802 (FreeCAD)
        self.Objects = [o for o in self.Objects if o.Name != name]

    def getObject(self, name):  # noqa: N802 (FreeCAD)
        return next((o for o in self.Objects if o.Name == name), None)


class LObj(Obj):
    def purgeTouched(self):  # noqa: N802 (FreeCAD)
        self.touched = False

    def addProperty(self, kind, prop, group, text):  # noqa: N802 (FreeCAD)
        super().addProperty(kind, prop, group, text)
        if kind == "App::PropertyLink":
            setattr(self, prop, None)


class Linked(unittest.TestCase):
    def setUp(self):
        live.reset()
        self.doc = LDoc()
        self.doc.addObject = self._add
        self.body = LObj(self.doc, "Body", "Grip", Shape())
        self.doc.Objects.append(self.body)
        self.ctx = handlers.Context(doc=self.doc, out_dir=tempfile.mkdtemp())
        self.run = handlers.local_runner(self.ctx)
        self._saved = (live.mesh_for, handlers._make_mesh, live.GUI, linked.Reruns._current)
        live.mesh_for = lambda part, doc=None, tol=None: (lambda o: (o.Shape.pos, o.Shape.ft))(
            part if hasattr(part, "Shape") else live.find(self.doc, part))
        handlers._make_mesh = FakeMesh
        live.GUI = None
        linked.Reruns._current = None
        self.warned = []
        self._warn, linked._warn = linked._warn, self.warned.append

    def _add(self, kind, name):
        n, k = name, 1
        while any(o.Name == n for o in self.doc.Objects):
            n, k = f"{name}{k:03d}", k + 1
        o = LObj(self.doc, n, n)
        o.TypeId = kind
        self.doc.Objects.append(o)
        return o

    def tearDown(self):
        live.mesh_for, handlers._make_mesh, live.GUI, linked.Reruns._current = self._saved
        linked._warn = self._warn
        live.reset()

    def ok(self, tool, args):
        ok, out = self.run(tool, args)
        self.assertTrue(ok, out)
        return out

    def textured(self, include=None, exclude=None):
        self.ok("texture_draft", {})
        self.ok("texture_set", {"texture": "Knurling", "settings": {"refineLength": 2.5, "bottomAngleLimit": 0}})
        d = live.draft()
        if include:
            d.set("includeOnly", True, layer=0)
            d.mask_add(faces=include, layer=0)
        if exclude:
            d.mask_add(faces=exclude, layer=0)
        out = self.ok("texture_apply", {})
        return live.find(self.doc, out["object"])

    def recompute(self, obj):
        """What FreeCAD does when the linked body changed: the feature's execute."""
        linked.TexturedFeature().execute(obj)

    def mask_of(self, obj):
        return json.loads(obj.TextureDraft)["layers"][0]["mask"]

    # -- the feature ---------------------------------------------------------------------------
    def test_a_new_textured_object_is_a_linked_feature(self):
        obj = self.textured(include=["Face4"])
        self.assertEqual(obj.TypeId, "Mesh::FeaturePython")
        self.assertIsInstance(obj.Proxy, linked.TexturedFeature)
        self.assertIs(obj.Source, self.body)
        self.assertEqual((obj.SourceBody, obj.TextureState, json.loads(obj.NewFaces)), ("Body", "up to date", []))
        a = json.loads(obj.MaskAnchors)
        self.assertEqual([f["name"] for f in a["layers"][0]["faces"]], ["Face4"])
        self.assertEqual(len(a["known"]), 6)
        before = obj.Mesh
        self.recompute(obj)   # nothing changed: nothing reruns
        self.assertIs(obj.Mesh, before)

    def test_pad_length_changes_and_masks_follow(self):
        # +X side by its surface (no element map here); the top by its mapped name.
        self.body.Shape = Shape(maps={"Face2": "PadTop;:H1", "Face4": "PadSide;:H4"})
        obj = self.textured(include=["Face2", "Face4"])
        before = obj.Mesh
        # A longer pad, and an earlier step renumbers the faces: the top is Face2 -> Face2
        # by name, the side found on its plane even though it grew.
        self.body.Shape = Shape(h=12.0, maps={"Face2": "PadTop;:H1"})
        self.recompute(obj)
        self.assertIsNot(obj.Mesh, before)
        self.assertEqual(self.mask_of(obj)["faces"], ["Face2", "Face4"])
        self.assertEqual((obj.TextureState, obj.SourceSignature, obj.OutOfDate),
                         ("up to date", live.signature(self.body), False))
        self.assertEqual(self.warned, [])

    def test_mapped_name_moves_with_renumbering(self):
        self.body.Shape = Shape(maps={"Face2": "PadTop;:H1"})
        obj = self.textured(include=["Face2"])
        sh = Shape(h=15.0)   # the top far from where it was: only its name finds it
        sh.ElementReverseMap = {"Face2": "PadTop;:H1"}
        sh.ElementMap = {"PadTop;:H1": "Face2"}
        self.body.Shape = sh
        self.recompute(obj)
        self.assertEqual(self.mask_of(obj)["faces"], ["Face2"])

    def test_a_masked_face_that_is_gone_is_flagged_not_misapplied(self):
        obj = self.textured(include=["Face2", "Face4", "Face5"])
        self.body.Shape = Shape(h=14.0)   # no element map: the top moved away, nothing on its surface now
        self.recompute(obj)
        self.assertEqual(self.mask_of(obj)["faces"], ["Face4", "Face5"])   # left out, not moved elsewhere
        self.assertTrue(obj.TextureState.startswith("mask needs re-pick"))
        self.assertIn("Layer 1: 1 of 3 masked faces no longer exist; re-pick them.", obj.TextureState)
        self.assertTrue(any("1 of 3 masked faces no longer exist" in w for w in self.warned))
        # Update answers the same.
        out = self.ok("texture_apply", {"update": obj.Name})
        self.assertEqual(out["state"], obj.TextureState)

    def test_a_pocket_stays_smooth_until_included(self):
        obj = self.textured(exclude=["Face1"])
        self.body.Shape = Shape(pocket=3.0)
        job = linked.prepare(obj, self.body)
        self.assertEqual(job.new_faces, ["Face7", "Face8", "Face9", "Face10", "Face11"])
        smooth = set(job.run_settings["layers"][0]["mask"]["triangles"])
        ft = self.body.Shape.ft
        self.assertTrue(all(t in smooth for f in job.new_faces for t in ft[f]))   # excluded whatever the mode
        self.assertTrue(not any(t in smooth for t in ft["Face2"] + ft["Face4"]))
        self.recompute(obj)
        self.assertEqual(json.loads(obj.NewFaces), job.new_faces)
        self.assertEqual(self.mask_of(obj)["faces"], ["Face1"])   # the person's mask, unchanged
        self.assertTrue(any("5 new faces since texturing" in w for w in self.warned))
        # Still new on the next change (the pocket deepens).
        self.body.Shape = Shape(pocket=4.0)
        self.recompute(obj)
        self.assertEqual(json.loads(obj.NewFaces), job.new_faces)
        # Include them: textured, and known from now on.
        out = self.ok("texture_apply", {"update": obj.Name, "include_new": True})
        self.assertEqual(out["included"], job.new_faces)
        self.assertEqual(json.loads(obj.NewFaces), [])
        job = linked.prepare(obj, self.body)
        self.assertEqual(job.new_faces, [])
        smooth = set(job.run_settings["layers"][0]["mask"]["triangles"])
        self.assertFalse(any(t in smooth for f in ("Face7", "Face8") for t in ft[f]))

    def test_include_layer_never_takes_new_faces_unasked(self):
        obj = self.textured(include=["Face2"])
        self.body.Shape = Shape(pocket=3.0)
        job = linked.prepare(obj, self.body)
        self.assertEqual(job.run_settings["layers"][0]["mask"]["mode"], "include")
        textured = set(job.run_settings["layers"][0]["mask"]["triangles"])   # include: the triangles it textures
        self.assertEqual(textured, set(self.body.Shape.ft["Face2"]))
        self.recompute(obj)
        linked.include_new(obj, sync=True)
        self.assertIn("Face7", self.mask_of(obj)["faces"])

    def test_painted_triangles_follow_and_lost_paint_is_flagged(self):
        obj = self.textured()
        d = json.loads(obj.TextureDraft)
        ft = self.body.Shape.ft
        d["layers"][0]["mask"]["triangles"] = ft["Face4"] + ft["Face2"][:1]
        obj.TextureDraft = json.dumps(d)
        obj.MaskAnchors = json.dumps(linked.anchors(d, self.body.Shape, self.body.Shape.pos, ft))
        self.body.Shape = Shape(h=8.0001)   # same tessellation where it was painted: the paint stays
        self.recompute(obj)
        self.assertEqual(sorted(self.mask_of(obj)["triangles"]), sorted(ft["Face4"] + ft["Face2"][:1]))
        self.body.Shape = Shape(h=16.0)     # the top moved off: its paint is lost; the side keeps it
        self.recompute(obj)
        new_ft = self.body.Shape.ft
        # Paint follows the surface it was on: the side's lower triangle (its
        # centroid on the old painted side), not the new length above it.
        self.assertEqual(sorted(self.mask_of(obj)["triangles"]), new_ft["Face4"][:1])
        self.assertIn("Layer 1: 1 of 3 painted triangles have no surface under them now; re-paint them.", obj.TextureState)

    def test_renaming_the_body_keeps_the_link(self):
        obj = self.textured(include=["Face4"])
        self.body.Label = "Handle"
        self.body.Shape = Shape(h=9.0)
        self.recompute(obj)
        self.assertIs(obj.Source, self.body)
        self.assertEqual(obj.TextureState, "up to date")
        out = self.ok("texture_apply", {"update": obj.Name})
        self.assertEqual(out["body"], "Handle")

    def test_an_old_document_converts(self):
        obj = self.textured(include=["Face4"])
        draft_json, mesh = obj.TextureDraft, obj.Mesh
        self.doc.removeObject(obj.Name)
        old = self._add("Mesh::Feature", "Textured")
        old.Label = "Grip_textured"
        old.Mesh = mesh
        for prop, kind, text in handlers.PROPS:
            old.addProperty(kind, prop, "Texturizer", text)
        old.SourceBody, old.TextureDraft, old.SourceSignature = "Body", draft_json, live.signature(self.body)
        linked.Observer().slotFinishRestoreDocument(self.doc)
        new = [o for o in self.doc.Objects if o.Label == "Grip_textured"]
        self.assertEqual(len(new), 1)
        new = new[0]
        self.assertNotEqual(new.Name, old.Name)
        self.assertNotIn(old, self.doc.Objects)
        self.assertEqual((new.TypeId, new.Source, new.Mesh, new.TextureDraft), ("Mesh::FeaturePython", self.body, mesh, draft_json))
        self.assertEqual(new.TextureState, "up to date")
        self.body.Shape = Shape(h=11.0)
        self.recompute(new)
        self.assertEqual(self.mask_of(new)["faces"], ["Face4"])
        # Out of date when converted: its names may be wrong already, so it says so.
        old2 = self._add("Mesh::Feature", "Textured")
        for prop, kind, text in handlers.PROPS:
            old2.addProperty(kind, prop, "Texturizer", text)
        old2.Label, old2.SourceBody, old2.TextureDraft, old2.OutOfDate = "Stale", "Body", draft_json, True
        (conv,) = linked.convert(self.doc)
        self.assertTrue(conv.TextureState.startswith("mask needs re-pick"))
        self.assertEqual(conv.MaskAnchors, "")

    def test_background_rerun_writes_the_newest_outside_transactions(self):
        obj = self.textured(include=["Face4"])
        jobs, posted = [], []
        linked.Reruns.install(jobs.append, posted.append)
        before = obj.Mesh
        self.body.Shape = Shape(h=10.0)
        self.recompute(obj)
        self.assertEqual((obj.TextureState, obj.Mesh), ("updating…", before))   # execute does not block
        self.body.Shape = Shape(h=12.0)
        self.recompute(obj)                       # a newer change: the first run goes stale
        for j in jobs:
            j()
        self.assertEqual(len(posted), 2)
        self.doc.HasPendingTransaction = True     # someone's step is open: wait
        posted[0]()
        posted[1]()
        self.assertIs(obj.Mesh, before)
        self.doc.HasPendingTransaction = False
        while len(posted) > 2:
            posted.pop(2)()
        self.assertIsNot(obj.Mesh, before)
        self.assertEqual((obj.TextureState, obj.SourceSignature), ("up to date", live.signature(self.body)))
        self.assertFalse(getattr(obj, "touched", False))


if __name__ == "__main__":
    unittest.main()
