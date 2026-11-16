# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The live path's speed, found slow in FreeCAD 1.1.3 on the Mac (a 12-move
ring drag ~25 s, one Undo ~10 s, a 30-move stroke ~12 s): a drag holds the
CPU-displaced preview and rebuilds it once (on release, or coalesced without
the Quick look), with the same mesh as an unheld change; an Undo reruns once
and only from the stage it touched; the sidebar rebuilds its layer strip
once a refresh; a brush dab rewrites only the overlay colours it changed;
the hover highlight is the brush's alone; the timing probe (timing.py)."""
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from texturizer import draft, live, paint, paintview, preview, timing  # noqa: E402
from test_preview import Clock, Engine, View, Worker  # noqa: E402
from test_wiring import cube  # noqa: E402


def make(ql=True):
    d = draft.Draft("Body")
    d.set("useDisplacement", True)
    clock, worker, view, eng = Clock(), Worker(), View(), Engine()
    c = preview.PreviewController(eng, d, view, quicklook_ok=ql, run_async=worker, schedule=clock.schedule)
    c.open()
    worker.run()
    return c, d, clock, worker, view, eng


class Hold(unittest.TestCase):
    def test_ring_drag_with_quicklook_rebuilds_once_on_release(self):
        c, d, clock, worker, view, eng = make(ql=True)
        n0 = len(eng.calls)
        c.hold(True)
        for k in range(12):   # the live run's 12-move ring drag, each move 2 s apart
            d.set_frame(rotation=[0, 0, 5.0 * (k + 1)], coalesce=("ring", 1))
            clock.advance(2.0)
        self.assertEqual(worker.jobs, [])                      # no rebuild per move
        self.assertEqual(view.log.count(("uniforms", "map")), 12)  # the Quick look follows every move
        self.assertTrue(view.ql and not view.cpu)
        c.hold(False)
        self.assertEqual(len(worker.jobs), 1)                  # one, at once, on release
        worker.run()
        self.assertEqual(len(eng.calls), n0 + 1)
        self.assertEqual(eng.calls[-1]["rerun_from"], "map")
        self.assertEqual(eng.calls[-1]["draft"]["frame"]["rotation"], [0, 0, 60.0])
        self.assertTrue(view.cpu and not view.ql)

    def test_burst_without_quicklook_is_coalesced(self):
        c, d, clock, worker, view, eng = make(ql=False)
        c.hold(True)
        for k in range(10):   # a burst: 10 moves 10 ms apart
            d.set_frame(rotation=[0, 0, float(k + 1)], coalesce=("ring", 1))
            clock.advance(0.01)
        self.assertEqual(worker.jobs, [])
        clock.advance(preview.DRAG_DEBOUNCE_S)
        self.assertEqual(len(worker.jobs), 1)                  # N changes, one rebuild
        worker.run()
        c.hold(False)
        self.assertEqual(worker.jobs, [])                      # nothing new since: no second job

    def test_release_with_nothing_changed_runs_nothing(self):
        c, d, clock, worker, view, eng = make()
        c.hold(True)
        c.hold(False)
        self.assertEqual(worker.jobs, [])

    def test_settled_mesh_is_bit_identical(self):
        """A held drag's one rebuild (from the map stage, on the cache)
        equals a fresh full preview of the same draft."""
        from texturizer import engine

        eng = engine.Engine(mesh_for=lambda part: cube())
        d = draft.Draft("Body")
        d.set("useDisplacement", True)
        d.set("refineLength", 2.0)
        meshes = []
        view = View()
        view.show_cpu = meshes.append
        c = preview.PreviewController(eng, d, view, quicklook_ok=True, run_async=lambda fn: fn(),
                                      schedule=lambda s, fn: (lambda: None))
        c.open()
        c.hold(True)
        for k in range(5):
            d.set_frame(rotation=[0, 3.0 * k, 7.0 * k], coalesce=("ring", 1))
        c.hold(False)
        fresh = engine.preview({"draft": d.to_dict(), "rerun_from": "subdivide"}, mesh=cube()[0],
                               face_triangles=cube()[1])
        got = meshes[-1]
        for k in ("vertices", "faces", "normals", "colours"):
            self.assertTrue(np.array_equal(np.asarray(got[k]), np.asarray(fresh["mesh"][k])), k)


class UndoOnce(unittest.TestCase):
    def test_undo_stage_is_what_it_changed(self):
        d = draft.Draft("Body")
        d.set_frame(rotation=[0, 0, 30])
        d.set("amplitude", 1.1)
        d.set("refineLength", 2.0)
        d.mask_add(faces=["Face1"])
        events = []
        d.subscribe(events.append)
        want = ["subdivide", "subdivide", "displace", "map"]  # mask (Preserve Untextured on), refine, amplitude, frame
        for w in want:
            d.undo()
            self.assertEqual(preview.stage_for(events[-1], d), w, events[-1]["value"])
        d.redo()
        self.assertEqual(preview.stage_for(events[-1], d), "map")
        self.assertEqual(len(events), 5)   # one event an undo or redo
        d2 = draft.Draft("Body")
        d2.set("rotate", True)             # panel state: not an undo step
        self.assertFalse(d2.can_undo())

    def test_undo_in_the_sidebar_rebuilds_once(self):
        from test_sidebar import fake_library, load_gui, unload_gui

        _gallery, sidebar = load_gui()
        try:
            c, d, clock, worker, view, eng = make()
            n0 = len(eng.calls)
            for k in range(5):     # a slider drag: one undo step
                d.set("scaleU", 10 + k, coalesce=True)
            d.end_coalesce()
            clock.advance(1.0)
            worker.run()
            sb = sidebar.Sidebar(d, runner=lambda t, a: (True, {}), list_textures=fake_library,
                                 confirm=lambda: True, selection=lambda: [])
            strips, reads = [], []
            real = sb.layers.refresh
            sb.layers.refresh = lambda: (strips.append(1), real())
            sb.list_textures = lambda: (reads.append(1), fake_library())[1]
            events = []
            d.subscribe(events.append)
            sb.press("undo")
            self.assertEqual([e["kind"] for e in events], ["undo"])   # the widgets' signals set nothing back
            self.assertEqual(len(strips), 0)   # a Size undo syncs Size: no layer strip rebuild
            self.assertLessEqual(len(reads), 1)   # the library read once at most
            clock.advance(1.0)
            self.assertEqual(len(worker.jobs), 1)                     # one rebuild
            worker.run()
            self.assertEqual(len(eng.calls), n0 + 2)
            self.assertEqual(eng.calls[-1]["rerun_from"], "map")      # Size: from the map stage, not subdivide
            self.assertEqual(eng.calls[-1]["draft"]["layers"][0]["settings"]["scaleU"],
                             draft.layer_defaults()["scaleU"])
        finally:
            unload_gui()


class Overlay(unittest.TestCase):
    def test_a_dab_rewrites_only_the_changed_colours(self):
        col = np.arange(12, dtype=np.int64)
        self.assertIsNone(paintview.overlay_runs(None, ("stroke", 1, 12), col))       # first draw: all
        self.assertIsNone(paintview.overlay_runs((None, col), None, col))             # a mask change: all
        self.assertIsNone(paintview.overlay_runs((("stroke", 1, 12), col), ("stroke", 2, 12), col))  # new stroke
        new = col.copy()
        new[[3, 4, 5, 9]] += 100
        self.assertEqual(paintview.overlay_runs((("stroke", 1, 12), col), ("stroke", 1, 12), new), [(3, 6), (9, 10)])
        self.assertEqual(paintview.overlay_runs((("stroke", 1, 12), col), ("stroke", 1, 12), col), [])


class HoverOnlyWithBrush(unittest.TestCase):
    """Rotate on and Brush off: the view shows no hover highlight (the
    orange and teal seen then are the preview's own tint: teal textured,
    orange masked, BumpMesh's previewMaterial)."""

    def setUp(self):
        live.reset()
        self.saved = live.mesh_for
        live.mesh_for = lambda part, doc=None, tol=None: cube()

    def tearDown(self):
        live.mesh_for = self.saved
        live.reset()

    def test_rotate_alone_draws_no_hover(self):
        d = live.draft()
        d.set_part("Body")
        pos, ft = cube()
        hovers = []
        hit = {"point": [5.0, 5.0, 10.0], "normal": [0, 0, 1], "triangle": 2, "view": [0, 0, -1]}
        tool = paint.PaintTool(d, lambda: (paint.surface_for(pos), ft), lambda x, y: dict(hit),
                               on_hover=lambda faces, erase: hovers.append(faces))
        d.set("rotate", True)
        self.assertFalse(tool.move(10, 10))
        self.assertFalse(tool.button(paint.LEFT, True, 10, 10))
        self.assertEqual(hovers, [])
        d.set("exclusionTool", "brush")     # Brush on (Standard): now the hover shows
        d.set("brushPrecision", False)
        tool.move(10, 10)
        self.assertTrue(hovers and hovers[-1])

    def test_paint_view_lets_go_of_the_view_with_rotate_alone(self):
        class W:
            controller = None

            def snapshot(self):
                return ("Body", cube()[0], cube()[1])

        d = live.draft()
        d.set_part("Body")
        d.set("rotate", True)
        pv = paintview.PaintView(W())
        pv.sync()
        self.assertIsNone(pv.view)    # no event callback, so no hover, cursor or overlay
        self.assertIsNone(pv.hover)


class OneGeometryCheckAnEvent(unittest.TestCase):
    def test_frozen_snapshot(self):
        from texturizer import window

        live.reset()
        saved = live.mesh_for
        calls = []
        pos, ft = cube()
        live.mesh_for = lambda part, doc=None, tol=None: (calls.append(part), (pos, ft))[1]
        try:
            live.draft().set_part("Body")
            w = window.Window()
            with w.frozen():
                for _ in range(4):    # the tool's questions in one move
                    self.assertIs(w.snapshot()[1], pos)
            self.assertEqual(len(calls), 1)
            with w.frozen():          # the next event checks again
                w.snapshot()
            w.snapshot()              # outside an event: every time
            self.assertEqual(len(calls), 3)
        finally:
            live.mesh_for = saved
            live.reset()


class _VO:
    def __init__(self, on=True):
        self.Visibility = on


class _O:
    def __init__(self, name, type_id, shown=True, solid=True, out=(), label=None):
        self.Name, self.Label, self.TypeId = name, label or name, type_id
        self.ViewObject = _VO(shown)
        self.Shape = type("S", (), {"Solids": [1] if solid else [], "Faces": [1] if solid else []})()
        self.OutList, self.InList = list(out), []
        self.Mesh = None
        for c in out:
            c.InList.append(self)


class _Doc:
    def __init__(self, objs, name="Doc"):
        self.Objects, self.Name, self.FileName = objs, name, f"/tmp/{name}.FCStd"


class PartChoice(unittest.TestCase):
    """Found live: after a restart the draft took "Reverse engineer"
    (ScanRulerReverse, a hidden Part::FeaturePython) instead of Body, the
    part it was on and the only visible solid."""

    def setUp(self):
        live._LAST.clear()

    def scene(self):
        pad = _O("Pad", "PartDesign::Pad", shown=False)
        body = _O("Body", "PartDesign::Body", out=[pad])
        rev = _O("ScanRulerReverse", "Part::FeaturePython", shown=False, label="Reverse engineer")
        return body, rev, _Doc([rev, pad, body])

    def test_never_a_hidden_object(self):
        body, rev, doc = self.scene()
        self.assertIs(live.choose_part(doc), body)
        self.assertEqual([o.Name for o in live.candidates(doc)], ["Body"])
        rev.ViewObject.Visibility = True     # shown, but a scripted object: the Body still wins
        self.assertIs(live.choose_part(doc), body)
        body.ViewObject.Visibility = False
        self.assertIs(live.choose_part(doc), rev)

    def test_last_part_then_selection(self):
        a = _O("Box", "Part::Box")
        b = _O("Cylinder", "Part::Cylinder")
        doc = _Doc([a, b])
        self.assertIsNone(live.choose_part(doc))          # two equally good: the person says which
        live.remember_part(doc, "Cylinder")
        self.assertIs(live.choose_part(doc), b)           # the last part this document's draft used
        self.assertIs(live.choose_part(doc, selected="Box"), a)   # the selection first
        b.ViewObject.Visibility = False
        self.assertIs(live.choose_part(doc), a)           # the last one, hidden since: not taken
        self.assertIsNone(live.last_part(_Doc([], name="Other")))

    def test_container_repeating_its_children_and_hidden_groups(self):
        a = _O("Box", "Part::Box")
        fuse = _O("Fusion", "Part::MultiFuse", out=[_O("Cyl", "Part::Cylinder", shown=False), a])
        doc = _Doc([a, fuse])
        self.assertEqual([o.Name for o in live.candidates(doc)], ["Box"])
        grp = _O("Part", "App::Part", solid=False, out=[a])
        grp.ViewObject.Visibility = False
        self.assertFalse(live.visible(a))                 # inside a hidden App::Part

    def test_texture_draft_takes_the_visible_body(self):
        from texturizer import handlers

        body, rev, doc = self.scene()
        live.reset()
        saved = live.mesh_for
        live.mesh_for = lambda part, doc=None, tol=None: cube()
        try:
            run = handlers.local_runner(handlers.Context(doc=doc, out_dir=__import__("tempfile").mkdtemp()))
            ok, out = run("texture_draft", {})
            self.assertTrue(ok, out)
            self.assertEqual(live.draft().part, "Body")

            class Gui:   # Use Selected Part with nothing selected: as a fresh draft picks
                def selected_object(self):
                    return None

                def open(self):
                    pass

            live.draft().set_part(None)
            saved_gui, live.GUI = live.GUI, Gui()
            try:
                ok, out = run("texture_draft", {"load": "selection"})
            finally:
                live.GUI = saved_gui
            self.assertTrue(ok, out)
            self.assertEqual(live.draft().part, "Body")
        finally:
            live.mesh_for = saved
            live.reset()


class HiddenPart(unittest.TestCase):
    """The person hides the draft's part: its preview and rings go with it."""

    def test_hide_and_show(self):
        from texturizer import window

        live.reset()
        saved = live.mesh_for
        live.mesh_for = lambda part, doc=None, tol=None: cube()
        try:
            d = live.draft()
            d.set_part("Body")
            body = _O("Body", "PartDesign::Body")
            w = window.Window()
            closed, synced, opened = [], [], []

            class C:
                showing = "quicklook"
                _body = True

                def close(self):
                    closed.append(self._body)
                    self.showing = None

                def open(self):
                    opened.append(1)
                    self.showing = "quicklook"

                def request(self, stage):
                    pass

            class T:
                def sync(self):
                    synced.append(w.user_hidden)

            class V:
                def _body(self):
                    return body

            class SB:
                def set_mesh_info(self, *a):
                    pass

                def set_estimate(self, *a):
                    pass

            w.controller, w.gizmo, w.paint, w.view, w.sidebar = C(), T(), T(), V(), SB()
            w._own_vis = 1
            body.ViewObject.Visibility = False
            w.part_visibility(body)        # the preview's own hiding
            self.assertEqual(closed, [])
            w._own_vis = 0
            w.part_visibility(body)        # the person's
            self.assertTrue(w.user_hidden)
            self.assertEqual(closed, [False])   # closed, the part left hidden
            self.assertEqual(synced, [True, True])
            body.ViewObject.Visibility = True
            w.part_visibility(body)
            self.assertFalse(w.user_hidden)
            self.assertEqual(opened, [1])
        finally:
            live.mesh_for = saved
            live.reset()


class Probe(unittest.TestCase):
    def setUp(self):
        self.lines = []
        timing.FORCE, timing.SINK = True, self.lines.append

    def tearDown(self):
        timing.FORCE = timing.SINK = None

    def test_off_prints_nothing(self):
        timing.FORCE = False
        d = draft.Draft("Body")
        d.subscribe(lambda ev: None)
        d.set("amplitude", 1.2)
        with timing.stage("x"):
            pass
        self.assertEqual(self.lines, [])

    def test_lines_per_stage(self):
        d = draft.Draft("Body")
        d.subscribe(lambda ev: None)
        d.set_frame(rotation=[0, 0, 10])
        self.assertTrue(any(x.startswith("Texturizer timing: draft frame ") for x in self.lines), self.lines)
        self.assertTrue(any("draft frame -> " in x for x in self.lines), self.lines)
        for x in self.lines:
            ms = float(x.rsplit(" ", 1)[1])
            self.assertGreaterEqual(ms, 0.0)

    def test_worker_stages_reported(self):
        c, d, clock, worker, view, eng = make()
        del self.lines[:]
        d.set("amplitude", 1.3)
        c.commit()
        worker.run()
        joined = "\n".join(self.lines)
        self.assertIn("preview displace (worker)", joined)
        self.assertIn("preview from displace (worker, total)", joined)
        self.assertIn("preview show_cpu (Coin mesh rebuild)", joined)
        self.assertIn("preview quicklook update (displace)", joined)

    def test_told_reports_the_whole_traceback(self):
        def bad(node):
            raise TypeError("projectPointToLine() takes 2 arguments")

        out = paintview._told(bad, "brush move event")(object())
        self.assertIsNone(out)
        text = "\n".join(self.lines)
        self.assertIn("Traceback", text)
        self.assertIn("TypeError: projectPointToLine() takes 2 arguments", text)

    def test_reads_the_parameter(self):
        import types

        timing.FORCE = None
        fc = types.ModuleType("FreeCAD")

        class P:
            def GetBool(self, key, default):  # noqa: N802
                return key == "TimingProbe"

        fc.ParamGet = lambda path: P() if path.endswith("Mod/Texturizer") else None
        saved = sys.modules.get("FreeCAD")
        sys.modules["FreeCAD"] = fc
        try:
            timing._cache[:] = [None, -1e9]
            self.assertTrue(timing.enabled())
        finally:
            if saved is None:
                sys.modules.pop("FreeCAD", None)
            else:
                sys.modules["FreeCAD"] = saved
            timing._cache[:] = [None, -1e9]


if __name__ == "__main__":
    unittest.main()
