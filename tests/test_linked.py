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
        self.__dict__.setdefault("kinds", {})[prop] = kind
        if kind.startswith("App::PropertyLink"):
            setattr(self, prop, None)

    def getTypeIdOfProperty(self, prop):  # noqa: N802 (FreeCAD)
        return self.kinds[prop]

    def removeProperty(self, prop):  # noqa: N802 (FreeCAD)
        self.PropertiesList.remove(prop)
        del self.kinds[prop]
        delattr(self, prop)


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
        self.assertEqual(obj.getTypeIdOfProperty("Source"), "App::PropertyLinkGlobal")   # the body may sit in an Assembly
        self.assertEqual((obj.SourceBody, obj.TextureState, json.loads(obj.NewFaces)), ("Body", "up to date", []))
        # Made by 50f02ed with a plain PropertyLink: it becomes global, the body kept.
        obj.removeProperty("Source")
        obj.addProperty("App::PropertyLink", "Source", "Texturizer", "")
        obj.Source = self.body
        linked.TexturedFeature().onDocumentRestored(obj)
        self.assertEqual((obj.getTypeIdOfProperty("Source"), obj.Source), ("App::PropertyLinkGlobal", self.body))
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

    def test_apply_yields_the_preview_and_says_what_it_ran(self):
        """Live: an Update after a CAD change took 26 s (5 s fresh), the preview
        subdividing the changed part beside it. The preview steps aside (it
        reruns after), and the answer says what the run was made of."""
        class C:
            running, gen, _pending = 3, 3, False

        class G:
            controller = C()
        live.GUI = G()
        obj = self.textured(include=["Face4"])
        self.assertEqual((G.controller.gen, G.controller._pending), (4, True))
        out = self.ok("texture_apply", {"update": obj.Name})
        e = out["engine"]
        self.assertTrue(e["previewYielded"])
        self.assertGreater(e["subdivided"], 0)
        self.assertEqual(e["output"], out["triangles"])
        self.assertIn("displace", e["ms"])
        self.assertEqual(e["pointsOnEdges"], 0)

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
        from texturizer import timing

        obj = self.textured(include=["Face4"])
        jobs, posted, lines = [], [], []
        linked.Reruns.install(jobs.append, posted.append)

        def drain():
            while posted:
                posted.pop(0)()
        saved = timing.FORCE, timing.SINK
        timing.FORCE, timing.SINK = True, lines.append
        self.addCleanup(lambda: (setattr(timing, "FORCE", saved[0]), setattr(timing, "SINK", saved[1])))
        before = obj.Mesh
        self.body.Shape = Shape(h=10.0)
        self.recompute(obj)
        self.assertEqual((obj.TextureState, obj.Mesh), ("updating…", before))   # execute does not block
        self.recompute(obj)                       # recomputed again, same geometry: the run under way stands
        self.assertEqual(len(jobs), 1)
        self.body.Shape = Shape(h=12.0)
        self.recompute(obj)                       # a newer change: the first run goes stale
        self.assertEqual(len(jobs), 2)
        self.doc.HasPendingTransaction = True     # someone's step is open: wait
        for j in jobs:
            j()
        posted_now = list(posted)
        posted.clear()
        for fn in posted_now:
            fn()
        self.assertIs(obj.Mesh, before)
        self.doc.HasPendingTransaction = False
        drain()
        self.assertIsNot(obj.Mesh, before)
        self.assertEqual((obj.TextureState, obj.SourceSignature), ("up to date", live.signature(self.body)))
        self.assertFalse(getattr(obj, "touched", False))
        text = "\n".join(lines)
        for step in ("queued (run 1)", "skipped: a rebuild for this geometry is already running", "queued (run 2)",
                     "started (run 2)", "skipped (run 1): a newer change replaced it", "finished (run 2)",
                     "waiting (run 2): a transaction is open", "written (run 2)"):
            self.assertIn(step, text)

    def test_a_step_left_open_does_not_hold_it_forever(self):
        obj = self.textured(include=["Face4"])
        jobs, posted = [], []
        linked.Reruns.install(jobs.append, posted.append)
        self.body.Shape = Shape(h=10.0)
        self.recompute(obj)
        self.doc.HasPendingTransaction = True     # Agent Link's own transaction, say, never closed
        jobs.pop()()
        n = 0
        while posted and n < 50:
            posted.pop(0)()
            n += 1
        self.assertEqual(obj.TextureState, "up to date")
        self.assertEqual(obj.SourceSignature, live.signature(self.body))

    def test_a_failed_rebuild_says_why(self):
        obj = self.textured(include=["Face4"])
        jobs, posted = [], []
        linked.Reruns.install(jobs.append, posted.append)
        self.body.Shape = Shape(h=10.0)
        real = linked.Job.run
        linked.Job.run = lambda self, should_abort: (_ for _ in ()).throw(MemoryError("out of memory"))
        try:
            self.recompute(obj)
            jobs.pop()()
        finally:
            linked.Job.run = real
        while posted:
            posted.pop(0)()
        self.assertEqual(obj.TextureState, "update failed: MemoryError: out of memory")
        self.assertTrue(any("update failed" in w for w in self.warned))
        # A write that raises says so too, never "updating…".
        self.body.Shape = Shape(h=11.0)
        real_w = linked.write
        linked.write = lambda *a: (_ for _ in ()).throw(RuntimeError("no Mesh module"))
        try:
            self.recompute(obj)
            jobs.pop()()
            while posted:
                posted.pop(0)()
        finally:
            linked.write = real_w
        self.assertEqual(obj.TextureState, "update failed: RuntimeError: no Mesh module")

def pad40(hole=False):
    """The Mac's case: a 40 x 30 x 20 Pad, its top Face6; a PartDesign Hole
    (here a 4 x 4 square through the top, 6 deep) renumbers it to Face5 and
    adds the hole's walls and floor."""
    X, Y, H = 40.0, 30.0, 20.0
    g = {"bottom": quad([0, 0, 0], [0, Y, 0], [X, Y, 0], [X, 0, 0]),
         "-y": quad([0, 0, 0], [X, 0, 0], [X, 0, H], [0, 0, H]),
         "+x": quad([X, 0, 0], [X, Y, 0], [X, Y, H], [X, 0, H]),
         "+y": quad([X, Y, 0], [0, Y, 0], [0, Y, H], [X, Y, H]),
         "-x": quad([0, Y, 0], [0, 0, 0], [0, 0, H], [0, Y, H])}
    if not hole:
        g["top"] = quad([0, 0, H], [X, 0, H], [X, Y, H], [0, Y, H])
        order = ["-x", "+x", "-y", "+y", "bottom", "top"]                 # top = Face6
    else:
        g["top"] = ring(H, (0, 0, X, Y), (18, 13, 22, 17))
        b = H - 6
        g["w1"] = quad([18, 13, b], [18, 13, H], [22, 13, H], [22, 13, b])
        g["w2"] = quad([22, 13, b], [22, 13, H], [22, 17, H], [22, 17, b])
        g["w3"] = quad([22, 17, b], [22, 17, H], [18, 17, H], [18, 17, b])
        g["w4"] = quad([18, 17, b], [18, 17, H], [18, 13, H], [18, 13, b])
        g["floor"] = quad([18, 13, b], [22, 13, b], [22, 17, b], [18, 17, b])
        order = ["-x", "+x", "-y", "bottom", "top", "w1", "w2", "w3", "w4", "floor", "+y"]   # top = Face5
    tris, ft = [], {}
    for k, key in enumerate(order):
        ft[f"Face{k + 1}"] = list(range(len(tris), len(tris) + len(g[key])))
        tris += g[key]
    sh = Shape()
    sh.pos, sh.ft = np.asarray(tris, np.float32).reshape(-1), ft
    sh.Faces = [object()] * len(ft)
    sh.Volume = 24000.0 - (96.0 if hole else 0)
    sh.BoundBox = Box(XMin=0, YMin=0, ZMin=0, XMax=X, YMax=Y, ZMax=H, DiagonalLength=55.7)
    return sh


class DraftFollows(Linked):
    """The sidebar's draft follows its body too (linked.DraftFollower)."""

    def test_hole_renumbers_the_top_and_the_draft_follows(self):
        self.body.Shape = pad40()
        self.ok("texture_draft", {})
        d = live.draft()
        d.set("includeOnly", True, layer=0)
        d.mask_add(faces=["Face6"], layer=0)         # the top
        self.body.Shape = pad40(hole=True)           # PartDesign Hole: the top is Face5 now
        obs = handlers.Observer()
        obs.slotRecomputedObject(self.body)
        obs.slotRecomputedDocument(self.doc)
        self.assertEqual(d.layer(0)["mask"]["faces"], ["Face5"])
        f = linked.DraftFollower.current()
        self.assertEqual(f.new_faces, ["Face6", "Face7", "Face8", "Face9", "Face10"])   # the hole's, smooth
        self.assertEqual(f.report, [])
        # Add to Document now textures the new top, not Face6 (a hole wall).
        out = self.ok("texture_apply", {})
        self.assertEqual(out["layers"][0]["faces"], ["Face5"])
        # Include Them: the hole's faces join the include layer.
        f.include_new()
        self.assertEqual(d.layer(0)["mask"]["faces"], ["Face5", "Face6", "Face7", "Face8", "Face9", "Face10"])
        self.assertEqual(f.new_faces, [])

    def test_exclude_layer_keeps_new_faces_smooth_and_a_gone_face_is_flagged(self):
        self.body.Shape = pad40()
        self.ok("texture_draft", {})
        d = live.draft()
        d.mask_add(faces=["Face6", "Face2"], layer=0)    # exclude the top and +x
        sh = pad40(hole=True)
        # +x is cut away (its surface gone): a stand-in for a step that removes a face.
        keep = {f: t for f, t in sh.ft.items() if f != "Face2"}
        tris = sh.pos.reshape(-1, 9)
        idx = sorted(t for ts in keep.values() for t in ts)
        sh.ft = {}
        renum = {old: new for new, old in enumerate(idx)}
        for k, (name, ts) in enumerate(sorted(keep.items(), key=lambda kv: kv[1][0])):
            sh.ft[f"Face{k + 1}"] = [renum[t] for t in ts]
        sh.pos = tris[idx].reshape(-1)
        sh.Faces = [object()] * len(sh.ft)
        sh.Volume = 1.0
        self.body.Shape = sh
        res = linked.follow_draft()                    # a tool's call catches it too
        self.assertIsNotNone(res)
        report, new = res
        self.assertEqual([r["message"] for r in report], ["Layer 1: 1 of 2 masked faces no longer exist; re-pick them."])
        top = [f for f, ts in sh.ft.items() if np.allclose(sh.pos.reshape(-1, 3, 3)[ts[0]][:, 2], 20) and len(ts) == 8]
        self.assertEqual(len(top), 1)
        faces = d.layer(0)["mask"]["faces"]
        self.assertIn(top[0], faces)                   # the top, renumbered
        self.assertTrue(set(new) <= set(faces))        # new faces excluded: smooth
        self.assertEqual(len(new), 5)
        self.assertTrue(any("1 of 2 masked faces no longer exist" in w for w in self.warned))

    def test_nothing_changes_nothing(self):
        self.body.Shape = pad40()
        self.ok("texture_draft", {})
        live.draft().mask_add(faces=["Face6"], layer=0)
        self.assertIsNone(linked.follow_draft())
        self.assertEqual(live.draft().layer(0)["mask"]["faces"], ["Face6"])


if __name__ == "__main__":
    unittest.main()
