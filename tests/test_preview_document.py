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
it; the preview's nodes leave the draft's view when it steps aside, when
the view changes, when the document closes and when the sequence ends; the
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
        self.assertEqual(self.nodes(self.gA), [])              # out of the draft's own view
        self.assertEqual(self.nodes(self.gB), [])              # never in the other one
        self.assertTrue(self.gA.getObject("Box").Visibility)   # Grip shown again, in its document
        self.assertTrue(self.gB.getObject("Box").Visibility)   # PointBox never hidden
        # Something in B asks the preview for a view: none there.
        with self.assertRaises(RuntimeError):
            self.w.view._graph()
        self.front(self.gA)                                    # the draft's own document back
        self.worker.run_all()
        self.assertEqual(self.c.showing, "cpu")
        self.assertEqual(len(self.nodes(self.gA)), 1)
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
        self.assertEqual(self.nodes(self.gA), [])
        self.assertTrue(self.gA.getObject("Box").Visibility)
        self.assertFalse(self.w.end_preview())       # nothing more to end

    def test_cancel_takes_the_nodes_out(self):
        self.showing()
        self.w.cancel_preview(wait=0)
        self.assertEqual(self.nodes(self.gA), [])
        self.assertFalse(self.c.has_cpu)
        self.assertEqual(self.c.dirty, "colours")    # drawn again from the cache when shown again

    def test_apply_and_export_end_the_preview(self):
        self.showing()
        out = handlers._end_preview({})
        self.assertTrue(out["previewClosed"])
        self.assertEqual(self.nodes(self.gA), [])

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
