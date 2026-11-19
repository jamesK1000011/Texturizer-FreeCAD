# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The preview belongs to its own document (window round 4: a draft made
for one document's Grip stayed in the Tutor's TutorRest and BikeStem
views, hid their PointBox and broke the Tutor's pointing). Two stand-in
documents, each with an object named Box (Grip in A, PointBox in B), and a
FreeCAD / FreeCADGui stand-in whose active document the test switches:
another document in front never shows the preview or loses an object to
it; the preview's nodes are switched off in the draft's view when it steps
aside and when the sequence ends, and leave it when the view changes or the
document closes; the
tools and the draft's follower find the part in the draft's own document."""
import os
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from texturizer import handlers, linked, live, preview, window  # noqa: E402
import test_linked as tl  # noqa: E402  (not imported by name: its tests would run twice)


class Graph:
    def __init__(self):
        self.children = []

    def addChild(self, n):  # noqa: N802 (Coin)
        self.children.append(n)

    def removeChild(self, n):  # noqa: N802 (Coin)
        self.children.remove(n)


class View:
    def __init__(self):
        self.graph = Graph()

    def getSceneGraph(self):  # noqa: N802 (FreeCAD)
        return self.graph


class VP:
    """A view provider: the eye in the tree."""

    def __init__(self):
        self.Visibility = True


class GuiDoc:
    def __init__(self, doc):
        self.Document = doc
        self.ActiveView = View()
        self.vps = {}

    def getObject(self, name):  # noqa: N802 (FreeCAD)
        if not any(o.Name == name for o in self.Document.Objects):
            return None
        return self.vps.setdefault(name, VP())


class Node:
    def __init__(self, name):
        self.name = name
        self.whichChild = -1


class StandInView(window.CoinView):
    """CoinView with its Coin nodes as stand-ins (no pivy here)."""

    def __init__(self, w):
        super().__init__(w)
        self.shown = []

    def _cpu(self):
        if self.cpu is None:
            self.cpu = Node("TexturizerPreview")
            self._graph().addChild(self.cpu)
        return self.cpu

    def show_cpu(self, mesh):
        self._graph()
        self._cpu()
        self.shown.append(mesh)

    def set_cpu_stale(self, on):
        pass


class Sidebar:
    binds = {}

    def objectName(self):  # noqa: N802 (Qt)
        return "TexturizerSidebar"

    def __getattr__(self, name):
        return lambda *a, **k: None


class Worker:
    def __init__(self):
        self.jobs = []

    def __call__(self, fn):
        self.jobs.append(fn)

    def run_all(self):
        while self.jobs:
            self.jobs.pop(0)()


class Engine:
    def __init__(self):
        self.calls = 0

    def preview(self, inputs, progress, should_abort):
        self.calls += 1
        return {"stages": {s: s for s in preview.STAGES}, "mesh": f"mesh#{self.calls}"}


def doc(name, label, h):
    d = tl.LDoc()
    d.Name = name
    o = tl.LObj(d, "Box", label, tl.Shape(h=h))
    d.Objects.append(o)
    return d


class TwoDocuments(unittest.TestCase):
    def setUp(self):
        live.reset()
        self.A, self.B = doc("GripDoc", "Grip", 8.0), doc("TutorRest", "PointBox", 3.0)
        self.gA, self.gB = GuiDoc(self.A), GuiDoc(self.B)
        fc = types.ModuleType("FreeCAD")
        fc.ActiveDocument = self.A
        fc.listDocuments = lambda: {d.Name: d for d in (self.A, self.B) if d is not None}
        gui = types.ModuleType("FreeCADGui")
        gui.ActiveDocument = self.gA
        gui.getDocument = lambda name: {"GripDoc": self.gA, "TutorRest": self.gB}[name]
        self.fc, self.gui = fc, gui
        self.mods = mock.patch.dict(sys.modules, {"FreeCAD": fc, "FreeCADGui": gui})
        self.mods.start()
        # Posted work runs at once, as without Qt: with PySide here (CI) the
        # window's queue waits for an event loop that never runs.
        self.posts = mock.patch.object(window, "_post", lambda fn: (fn(), True)[1])
        self.posts.start()
        self._saved = (live.mesh_for, live.GUI)
        live.mesh_for = lambda part, doc=None, tol=None: (lambda o: (o.Shape.pos, o.Shape.ft))(
            part if hasattr(part, "Shape") else live.find(doc or live._doc(), part))
        self.d = live.draft()
        live.bind_doc(self.A)
        self.d.set_part("Box")
        self.w = window.Window()
        live.GUI = self.w
        self.w.sidebar = Sidebar()
        self.w.view = StandInView(self.w)
        self.worker = Worker()
        self.c = preview.PreviewController(Engine(), self.d, self.w.view, quicklook_ok=False, run_async=self.worker,
                                           post=lambda fn: fn(), schedule=lambda s, fn: (lambda: None))
        self.w.controller = self.c

    def tearDown(self):
        live.mesh_for, live.GUI = self._saved
        self.posts.stop()
        self.mods.stop()
        live.reset()

    def front(self, which):
        """The person (or the Tutor) brings a document to the front."""
        self.fc.ActiveDocument = which.Document
        self.gui.ActiveDocument = which
        self.w.document_changed(which.Document)

    def showing(self):
        """The preview up on Grip: the CPU node in A's view, the body hidden."""
        self.c.open()
        self.worker.run_all()
        self.assertEqual(self.c.showing, "cpu")
        self.assertIn(self.c.view.cpu, self.gA.ActiveView.graph.children)
        self.assertFalse(self.gA.getObject("Box").Visibility)

    def nodes(self, g):
        return [n for n in g.ActiveView.graph.children if isinstance(n, Node)]

    def on(self, g):
        """Each preview node in g's view: switched on or not."""
        return [n.whichChild != -1 for n in self.nodes(g)]

    # -- the binding ---------------------------------------------------------------------------
    def test_the_draft_is_bound_to_its_document(self):
        self.assertTrue(live.in_front(self.A))
        self.assertFalse(live.in_front(self.B))
        self.assertIs(live.draft_doc(), self.A)
        self.fc.ActiveDocument = self.B
        self.assertIs(live.draft_doc(), self.A)   # not the document in front

    def test_another_document_has_no_view_and_no_part_for_the_preview(self):
        self.assertIs(window._view(), self.gA.ActiveView)
        self.assertEqual(self.w.snapshot()[1].tolist(), self.A.Objects[0].Shape.pos.tolist())
        self.fc.ActiveDocument, self.gui.ActiveDocument = self.B, self.gB
        self.assertIsNone(window._view())
        self.assertIsNone(self.w.snapshot())     # PointBox is never taken for the draft's Box
        self.assertIs(self.w.view._body(), self.gA.getObject("Box"))   # the body is Grip's, in A

    # -- stepping aside and back -----------------------------------------------------------------
    def test_another_document_in_front_steps_the_preview_aside(self):
        self.showing()
        self.front(self.gB)
        self.assertIsNone(self.c.showing)
        self.assertEqual(self.on(self.gA), [False])            # switched off in the draft's own view (kept there)
        self.assertEqual(self.nodes(self.gB), [])              # never in the other one
        self.assertTrue(self.gA.getObject("Box").Visibility)   # Grip shown again, in its document
        self.assertTrue(self.gB.getObject("Box").Visibility)   # PointBox never hidden
        # Something in B asks the preview for a view: none there.
        with self.assertRaises(RuntimeError):
            self.w.view._graph()
        self.front(self.gA)                                    # the draft's own document back
        self.worker.run_all()
        self.assertEqual(self.c.showing, "cpu")
        self.assertEqual(self.on(self.gA), [True])             # the same node, on again
        self.assertEqual(self.nodes(self.gB), [])

    def test_a_document_never_previewed_stays_aside_on_return(self):
        self.front(self.gB)
        self.front(self.gA)
        self.assertIsNone(self.c.showing)   # nothing was showing: nothing comes up on its own

    def test_a_new_view_takes_the_nodes_out_of_the_old_one(self):
        self.showing()
        old = self.gA.ActiveView
        self.gA.ActiveView = View()
        self.w.view._graph()
        self.assertEqual(old.graph.children, [])

    def test_closing_the_drafts_document(self):
        self.showing()
        self.assertTrue(self.w.document_closed(self.A))
        self.assertEqual(self.nodes(self.gA), [])
        self.assertIsNone(self.d.part)               # the part went with its document
        self.assertIsNone(live.DRAFT_DOC["name"])
        self.fc.ActiveDocument, self.gui.ActiveDocument = self.B, self.gB
        self.assertIsNone(self.w.snapshot())
        self.assertFalse(self.w.document_closed(self.B))   # not the draft's

    def test_the_sequence_end_takes_the_preview_away(self):
        self.showing()
        self.assertTrue(self.w.end_preview())
        self.assertIsNone(self.c.showing)
        self.assertEqual(self.on(self.gA), [False])   # off, not taken out (taken out: an empty view, round 6 0010)
        self.assertTrue(self.gA.getObject("Box").Visibility)
        self.assertFalse(self.w.end_preview())       # nothing more to end

    def test_cancel_switches_the_nodes_off(self):
        self.showing()
        self.w.cancel_preview(wait=0)
        self.assertEqual(self.on(self.gA), [False])
        self.assertTrue(self.c.has_cpu)              # the mesh stays in its node: shown again at once, stale-tinted

    def test_apply_and_export_end_the_preview(self):
        self.showing()
        out = handlers._end_preview({})
        self.assertTrue(out["previewClosed"])
        self.assertEqual(self.on(self.gA), [False])

    # -- texture_apply leaves the result in view (window round 6, 0010-2002) ----------------------
    def result(self):
        """A Textured result of Grip in A (Grip_hex), shown; the App
        objects' eyes are the Gui document's view providers."""
        r = tl.LObj(self.A, "Mesh", "Grip_hex", tl.Shape(h=8.4))
        r.PropertiesList.append("SourceBody")
        r.SourceBody = "Box"
        self.A.Objects.append(r)
        for o in self.A.Objects:
            o.ViewObject = self.gA.getObject(o.Name)
        return r

    def eye(self, name):
        return self.gA.getObject(name).Visibility

    def applied(self):
        """The preview up, then texture_apply's end: _after_apply."""
        self.result()
        self.showing()
        out = handlers._after_apply(self.A, {"object": "Mesh", "updated": False})
        return out

    def test_apply_leaves_the_result_shown_and_the_preview_off(self):
        out = self.applied()
        self.assertTrue(out["previewClosed"])
        self.assertIsNone(self.c.showing)
        self.assertEqual(self.on(self.gA), [False])   # kept in the draft's view, switched off
        self.assertTrue(self.eye("Mesh"))              # the result shows
        self.assertFalse(self.eye("Box"))              # its body hidden behind it
        self.assertTrue(out["bodyHidden"])
        self.assertFalse(self.w.user_hidden)

    def test_apply_answers_the_window_facts(self):
        f = self.applied()["window"]
        self.assertEqual((f["activeDocument"], f["draftDocument"], f["draftInFront"]), ("GripDoc", "GripDoc", True))
        self.assertIsNone(f["previewShowing"])
        self.assertEqual(f["preview"]["scene"]["previewNodes"], {"TexturizerPreview": [False]})
        self.assertTrue(f["preview"]["own"])
        self.assertEqual(f["result"], {"name": "Mesh", "visible": True, "shown": True})
        self.assertEqual(f["source"], {"name": "Box", "visible": False, "shown": False})

    def test_a_new_result_left_hidden_is_shown(self):
        self.result()
        self.gA.getObject("Mesh").Visibility = False
        out = handlers._after_apply(self.A, {"object": "Mesh", "updated": False})
        self.assertTrue(out["resultShown"])
        self.assertTrue(self.eye("Mesh"))
        self.assertFalse(self.eye("Box"))

    def test_an_update_keeps_the_persons_eye_on_the_result(self):
        self.result()
        self.gA.getObject("Mesh").Visibility = False
        out = handlers._after_apply(self.A, {"object": "Mesh", "updated": True})
        self.assertNotIn("resultShown", out)
        self.assertFalse(self.eye("Mesh"))
        self.assertTrue(self.eye("Box"))

    def test_a_draft_tool_after_apply_brings_the_preview_back(self):
        self.applied()
        # texture_draft { part: "Grip", reset: true }: the window follows the draft.
        self.w._on_draft({"kind": "reset"})
        self.worker.run_all()
        self.assertFalse(self.w.user_hidden)   # hidden for its result: Texturizer's, not the person's
        self.assertEqual(self.c.showing, "cpu")
        self.assertEqual(self.on(self.gA), [True])
        # The result steps aside while the preview stands on its place
        # (review 3, 17-017, tex2-0-02/03: they z-fought), and comes back.
        self.assertFalse(self.eye("Mesh"))
        self.assertFalse(self.eye("Box"))
        self.w.end_preview()
        self.assertFalse(self.eye("Box"))      # closing leaves the body behind its result
        self.assertTrue(self.eye("Mesh"))

    def test_the_bodys_hiding_for_its_result_is_not_the_persons(self):
        self.applied()
        self.w.part_visibility(self.A.getObject("Box"))
        self.assertFalse(self.w.user_hidden)

    def test_the_person_hiding_the_part_still_holds_the_preview_back(self):
        for o in self.A.Objects:
            o.ViewObject = self.gA.getObject(o.Name)
        self.gA.getObject("Box").Visibility = False   # no result: the person hid it
        self.w._part_changed()
        self.assertTrue(self.w.user_hidden)
        self.assertIsNone(self.c.showing)

    def test_build_preview_lets_go_of_a_stale_hidden_mark(self):
        self.applied()
        self.w.user_hidden = True   # set before the result hid it (an older event)
        self.w.open = lambda *a, **k: None
        got = self.w.build_preview(0.0)
        self.assertIsNotNone(got)
        self.assertFalse(self.w.user_hidden)

    def test_the_facts_say_whether_the_camera_sees_the_result(self):
        r = self.result()
        r.Shape.BoundBox.Center = types.SimpleNamespace(x=10.0, y=5.0, z=4.2)
        self.fc.Vector = lambda x, y, z: (x, y, z)
        v = self.gA.ActiveView
        v.getSize = lambda: (800, 600)
        v.getPointOnViewport = lambda p: (400, 300)
        self.assertEqual(self.w._in_view(r)["inside"], True)
        v.getPointOnViewport = lambda p: (-2000, 300)   # the camera looks elsewhere
        got = self.w.view_facts(self.A, "Mesh", "Box")["resultInView"]
        self.assertEqual((got["inside"], got["pixel"]), (False, [-2000, 300]))

    def test_the_facts_walk_the_scene_and_the_results_view(self):
        """Live round 7: the result shown and in sight, the view empty. The
        facts name each node of the scene's top levels (a switch's
        whichChild), the camera's clipping and the result's view side."""
        r = self.applied() and self.A.getObject("Mesh")
        r.Mesh = types.SimpleNamespace(CountFacets=1234)
        vp = self.gA.getObject("Mesh")
        vp.DisplayMode = "Shaded"
        vp.RootNode = Node("ResultRoot")
        sw = Node("ResultModes")
        sw.whichChild = 0
        vp.SwitchNode = sw

        class Field:
            def __init__(self, v):
                self.v = v

            def get(self):
                return str(self.v)

        class Cam:
            fields = {"nearDistance": 0.1, "farDistance": 500.0, "position": "0 0 100"}

            def getField(self, f):  # noqa: N802 (Coin)
                return Field(self.fields[f]) if f in self.fields else None

        self.gA.ActiveView.getCameraNode = lambda: Cam()
        f = self.w.view_facts(self.A, "Mesh", "Box")
        nodes = f["scene"]["nodes"]
        self.assertEqual([(n.get("name"), n.get("whichChild"), n["at"]) for n in nodes], [("TexturizerPreview", "-1", "0")])
        self.assertEqual(f["scene"]["topChildren"], 1)
        self.assertEqual((f["camera"]["nearDistance"], f["camera"]["farDistance"]), ("0.1", "500.0"))
        rv = f["resultView"]
        self.assertEqual((rv["DisplayMode"], rv["Visibility"], rv["facets"]), ("Shaded", True, 1234))
        self.assertEqual((rv["root"]["name"], rv["switch"]["whichChild"]), ("ResultRoot", "0"))

    def test_the_scene_walk_goes_two_levels_and_stops_at_its_cap(self):
        root = Graph()
        for i in range(3):
            g = Graph()
            g.addChild(Node(f"kid{i}"))
            root.addChild(g)
        got = window.scene_walk(root)
        self.assertEqual([n["at"] for n in got["nodes"]], ["0", "0.0", "1", "1.0", "2", "2.0"])
        self.assertEqual(got["nodes"][1]["name"], "kid0")
        self.assertTrue(window.scene_walk(root, cap=2)["truncated"])

    def test_the_facts_are_not_read_off_the_gui_thread(self):
        import threading

        got = []
        t = threading.Thread(target=lambda: got.append(self.w.view_facts(self.A, "Box", None)))
        t.start()
        t.join()
        self.assertEqual(got, [{"thread": "job"}])

    def test_cancel_answers_the_window_facts(self):
        self.result()
        self.showing()
        live.TARGET["object"] = "Mesh"
        out = handlers.texture_preview(handlers.Context(doc=self.A, out_dir=""), {"cancel": True})
        self.assertTrue(out["cancelled"])
        f = out["window"]
        self.assertEqual(f["preview"]["scene"]["previewNodes"], {"TexturizerPreview": [False]})
        self.assertEqual(f["result"]["name"], "Mesh")
        self.assertEqual(f["source"]["name"], "Box")

    # -- the tools and the follower ----------------------------------------------------------------
    def test_tools_find_the_part_in_the_drafts_document(self):
        live.GUI = None
        ctx = handlers.Context(doc=self.B, out_dir="")
        self.assertIs(handlers._part(ctx, None, self.d), self.A.Objects[0])
        self.assertEqual(handlers._part_word(ctx, self.d), {"part": "Grip"})
        f = linked.DraftFollower.current()
        self.fc.ActiveDocument = self.B
        self.assertIs(f._part(), self.A.Objects[0])

    def test_texture_draft_in_another_document_binds_it(self):
        live.GUI = None
        seen = []
        self.d.subscribe(lambda ev: seen.append(ev.get("kind")))
        run = handlers.local_runner(handlers.Context(doc=self.B, out_dir=""))
        self.fc.ActiveDocument = self.B
        ok, out = run("texture_draft", {"part": "PointBox"})
        self.assertTrue(ok, out)
        self.assertEqual(live.DRAFT_DOC["name"], "TutorRest")
        self.assertEqual(self.d.part, "Box")
        self.assertIn("part", seen)                  # the same Name in another document is another part
        self.assertEqual((out["part"], out["label"]), ("PointBox", "PointBox"))

    def test_a_closed_document_never_hands_its_part_to_another(self):
        live.GUI = None
        live.document_closed(self.A)
        self.assertFalse(live.in_front(self.B))
        self.assertIsNone(handlers._draft_home(self.B))
        ctx = handlers.Context(doc=self.B, out_dir="")
        self.assertEqual(handlers._part_word(ctx, self.d), {"part": "Box"})   # its own name, nothing of B's


class ArrayMeshEngine(Engine):
    """The engine's preview as the real one hands it: numpy arrays."""

    def preview(self, inputs, progress, should_abort):
        import numpy as np

        self.calls += 1
        m = {"vertices": np.zeros((4, 3), np.float32), "faces": np.array([[0, 1, 2], [0, 2, 3]], np.int32),
             "normals": np.zeros((4, 3), np.float32), "colours": np.zeros((4, 3), np.float32),
             "face_parent": np.array([0, 1], np.int64), "edge": np.float64(0.5)}
        return {"stages": {s: s for s in preview.STAGES}, "mesh": m, "note": None, "coarse": False,
                "provisional": False, "timings": []}


class ArraysAfterApply(TwoDocuments):
    """Live 17 Nov 12:26: after texture_apply, texture_preview { quality:
    "draft" } and texture_set were refused with only "The truth value of an
    array with more than one element is ambiguous": the window's build
    (now reached after an apply) was answered with `mesh["faces"] or ()`."""

    def setUp(self):
        super().setUp()
        self.c.engine = ArrayMeshEngine()
        self.c.run_async = lambda fn: fn()   # the worker's maths at once; its hand-offs still come through build_now
        self.w.open = lambda *a, **k: self.w.sidebar
        self.run = handlers.local_runner(handlers.Context(doc=self.A, out_dir=""))

    def test_preview_and_set_after_apply_with_arrays(self):
        self.applied()
        ok, out = self.run("texture_draft", {"part": "Grip", "reset": True})
        self.assertTrue(ok, out)
        ok, out = self.run("texture_preview", {"quality": "draft"})
        self.assertTrue(ok, out)
        self.assertEqual((out["triangles"], out["vertices"], out["previewEdge"]), (2, 4, 0.5))
        self.assertEqual(self.c.showing, "cpu")
        ok, out = self.run("texture_set", {"texture": "Fine Leather", "settings": {
            "textureHeight": 0.3, "scaleU": 8, "mappingMode": "cubic", "refineLength": 0.8}})
        self.assertTrue(ok, out)
        self.assertEqual(out["preview"]["triangles"], 2)
        ok, out = self.run("texture_preview", {"cancel": True})
        self.assertTrue(ok, out)
        self.assertEqual(out["window"]["preview"]["scene"]["previewNodes"], {"TexturizerPreview": [False]})


class BugValueErrors(unittest.TestCase):
    """A ValueError from a bug is refused as failed with where it came from;
    a ValueError Texturizer raises on purpose stays as it was."""

    def tearDown(self):
        handlers.HANDLERS.pop("texture_test_bug", None)

    def test_an_array_truth_test_names_its_line(self):
        import numpy as np

        def bad(s, a):
            return np.zeros(3) or ()

        line = bad.__code__.co_firstlineno + 1
        fn = handlers.bound(None, {"t": bad})["t"]
        with self.assertRaises(handlers.ToolError) as cm:
            fn({})
        e = cm.exception
        self.assertEqual(e.code, "failed")
        self.assertIn("truth value of an array", e.message)
        self.assertIn(f":{line} in bad", e.message)
        handlers.HANDLERS["texture_test_bug"] = bad
        ok, out = handlers.local_runner(handlers.Context(doc=tl.LDoc(), out_dir=""))("texture_test_bug", {})
        self.assertEqual((ok, out["error"]["code"]), (False, "failed"))

    def test_inside_numpy_it_names_texturizers_line(self):
        from texturizer import window as w

        def bad(s, a):
            return w.quicklook_mesh([1.0, 2.0], None)   # not triangles: numpy's reshape refuses

        fn = handlers.bound(None, {"t": bad})["t"]
        with self.assertRaises(handlers.ToolError) as cm:
            fn({})
        self.assertEqual(cm.exception.code, "failed")
        self.assertIn("texturizer/window.py:", cm.exception.message)
        self.assertIn("in quicklook_mesh", cm.exception.message)
        self.assertNotIn(os.sep + "home" + os.sep, cm.exception.message)

    def test_a_purposeful_value_error_is_unchanged(self):
        def refuse(s, a):
            raise ValueError("Not a PNG file.")

        fn = handlers.bound(None, {"t": refuse})["t"]
        with self.assertRaises(ValueError) as cm:
            fn({})
        self.assertNotIsInstance(cm.exception, handlers.ToolError)
        self.assertEqual(str(cm.exception), "Not a PNG file.")

        def tool_error(s, a):
            raise handlers.ToolError("invalid_input", "settings.x: a number.")

        with self.assertRaises(handlers.ToolError) as cm:
            handlers.bound(None, {"t": tool_error})["t"]({})
        self.assertEqual(cm.exception.code, "invalid_input")


class AppliedTextureInTheDraft(unittest.TestCase):
    """texture_apply with its own texture: the draft and so the sidebar take
    it (round 4 0010: the panel kept Knurling beside a Hexagon result)."""

    def setUp(self):
        live.reset()
        self.doc = doc("Unnamed", "Grip", 8.0)
        self.ctx = handlers.Context(doc=self.doc, out_dir="")

    def tearDown(self):
        live.reset()

    def test_one_texture(self):
        d = live.draft()
        out = {"layers": [{"layer": 0, "mode": None, "faces": []}]}
        handlers._draft_follows_args(self.ctx, {"body": "Grip", "texture": "Bamboo",
                                                "settings": {"textureHeight": -0.6, "mappingMode": "cylindrical"}}, d, out)
        self.assertEqual(d.part, "Box")
        self.assertEqual(d.layers[0]["texture"], "Bamboo")
        self.assertAlmostEqual(d.layers[0]["settings"]["amplitude"], 0.6)
        self.assertEqual(out["draft"]["layers"][0]["texture"], "Bamboo")
        self.assertEqual(live.DRAFT_DOC["name"], "Unnamed")

    def test_layers_with_faces(self):
        d = live.draft()
        out = {"layers": [{"layer": 0, "mode": None, "faces": []}, {"layer": 1, "mode": "include", "faces": ["Face6"]}]}
        handlers._draft_follows_args(self.ctx, {"body": "Grip", "layers": [{"texture": "Bamboo"},
                                                                           {"texture": "Bark", "blendAdd": True}]}, d, out)
        self.assertEqual([L["texture"] for L in d.layers], ["Bamboo", "Bark"])
        self.assertEqual((d.layers[1]["includeOnly"], d.layers[1]["mask"]["faces"], d.layers[1]["blendAdd"]),
                         (True, ["Face6"], True))


class FollowPreview(unittest.TestCase):
    """texture_set / texture_mask / texture_layer wait for a shown CPU
    preview to follow the draft (round 4 0015-0019)."""

    def setUp(self):
        self.saved = live.GUI

    def tearDown(self):
        live.GUI = self.saved

    def gui(self, showing, cpu, got):
        calls = []

        class G:
            controller = types.SimpleNamespace(showing=showing, cpu_mode=cpu)

            def build_preview(self, budget, show=True):
                calls.append((budget, show))
                return got

        live.GUI = G()
        return calls

    def test_a_shown_cpu_preview_is_rebuilt_before_the_answer(self):
        calls = self.gui("cpu", True, {"done": True, "result": {"mesh": {"faces": [0, 1, 2]}}, "ms": 12})
        out = handlers._follow_preview({})
        self.assertEqual(out["preview"], {"triangles": 3, "ms": 12})
        self.assertEqual(calls, [(handlers.FOLLOW_BUDGET_S, False)])

    def test_past_the_budget_it_says_so(self):
        self.gui("quicklook", True, {"done": False, "ms": 20000})
        self.assertTrue(handlers._follow_preview({})["preview"]["building"])

    def test_the_quick_look_follows_by_itself(self):
        calls = self.gui("quicklook", False, None)
        self.assertEqual(handlers._follow_preview({}), {})
        calls = self.gui(None, True, None)
        self.assertEqual(handlers._follow_preview({}), {})
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
