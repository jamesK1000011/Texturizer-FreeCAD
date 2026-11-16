# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The cold first preview (roadmap v19 item 5). Live on the Mac a new part's
first preview took 20.6 s (40 x 30 x 10 box, M3 hole, Knurling, 103 k
triangles) while warm edits took about 0.2 s: the subdivide stage's sliver
clean-up (regularize) is the cold cost. Now a coarse first look comes first
and the full preview replaces it; a change drops both; a change from a later
stage lets the cold subdivide finish and keep it; the subdivision is kept by
the part's triangles and the edge (Draft and Fine share it) in memory and on
disk; the timing probe names the engine's stages on the preview path. Also:
texture_set takes every setting name the texture guide's examples use, as
texture_apply does."""
import os
import re
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from texturizer import draft, engine, handlers, live, preview, timing  # noqa: E402
from test_preview import Clock, View, Worker  # noqa: E402


class ColdEngine:
    """A fake engine with kept(): cold until a full subdivide has finished."""

    def __init__(self, on_full=None):
        self.calls = []
        self.warm = False
        self.on_full = on_full
        self.subdivide_aborted = []

    def kept(self, inputs):
        return self.warm

    def preview(self, inputs, progress, should_abort):
        self.calls.append(inputs)
        if inputs.get("coarse"):
            return {"stages": {}, "mesh": "coarse", "coarse": True}
        if self.on_full:
            hook, self.on_full = self.on_full, None
            hook()
        stages = {}
        for s in preview.STAGES[preview.STAGES.index(inputs["rerun_from"]):]:
            if s == "subdivide":
                ab = inputs.get("subdivide_abort") or should_abort
                self.subdivide_aborted.append(ab())
                if ab():
                    return None
                self.warm = True
            elif should_abort():
                return None
            progress(s, 1.0)
            stages[s] = f"{s}#{len(self.calls)}"
        return {"stages": stages, "mesh": f"full#{len(self.calls)}"}


def make(eng, ql=False):
    d = draft.Draft("Body")
    d.set("useDisplacement", True)
    clock, worker, view = Clock(), Worker(), View()
    c = preview.PreviewController(eng, d, view, quicklook_ok=ql, run_async=worker, schedule=clock.schedule)
    return c, d, clock, worker, view


class Progressive(unittest.TestCase):
    def test_coarse_first_then_full_replaces_it(self):
        eng = ColdEngine()
        c, d, clock, worker, view = make(eng)
        c.open()
        worker.run()
        self.assertEqual(view.meshes, ["coarse", "full#2"])          # coarse shown first, the full one replaces it
        self.assertEqual([bool(x.get("coarse")) for x in eng.calls], [True, False])
        self.assertIn(("note", preview.COARSE_NOTE), view.log)
        self.assertEqual(c.coarse_shown, 1)
        self.assertTrue(c.current and view.cpu)
        self.assertEqual(c.cache["subdivide"], "subdivide#2")          # only the full run's stages are kept

    def test_warm_part_has_no_coarse_pass(self):
        eng = ColdEngine()
        eng.warm = True
        c, d, clock, worker, view = make(eng)
        c.open()
        worker.run()
        self.assertEqual(view.meshes, ["full#1"])
        self.assertEqual(len(eng.calls), 1)
        d.set("scaleU", 6.0)       # a warm edit: from map, one run, no coarse
        c.commit()
        worker.run()
        self.assertEqual([x["rerun_from"] for x in eng.calls], ["subdivide", "map"])
        self.assertFalse(any(x.get("coarse") for x in eng.calls))

    def test_engine_without_kept_gets_no_coarse_pass(self):
        from test_preview import Engine

        c, d, clock, worker, view = make(Engine())
        c.open()
        worker.run()
        self.assertEqual(view.meshes, ["mesh#1"])

    def test_a_later_stage_change_drops_the_full_but_keeps_its_subdivide(self):
        holder = {}
        eng = ColdEngine(on_full=lambda: holder["d"].set("scaleU", 9.0))
        c, d, clock, worker, view = make(eng)
        holder["d"] = d
        c.open()
        worker.run()                       # coarse shown; the change comes during the full run
        self.assertEqual(view.meshes, ["coarse"])   # the stale full result is never shown
        self.assertEqual(eng.subdivide_aborted, [False])   # its subdivide went on (a map change)
        self.assertTrue(eng.warm)
        clock.advance(preview.DEBOUNCE_S)
        worker.run()                       # warm now: straight to the full preview, with the new scale
        self.assertEqual(view.meshes, ["coarse", f"full#{len(eng.calls)}"])
        self.assertFalse(eng.calls[-1].get("coarse"))
        self.assertEqual(eng.calls[-1]["draft"]["layers"][0]["settings"]["scaleU"], 9.0)

    def test_a_subdivide_change_cancels_the_cold_subdivide(self):
        holder = {}
        eng = ColdEngine(on_full=lambda: holder["d"].set("refineLength", 2.0))
        c, d, clock, worker, view = make(eng)
        holder["d"] = d
        c.open()
        worker.run()
        self.assertEqual(eng.subdivide_aborted, [True])
        self.assertFalse(eng.warm)
        self.assertEqual(view.meshes, ["coarse"])
        clock.advance(preview.DEBOUNCE_S)
        worker.run()                       # still cold: coarse again, then full
        self.assertEqual(view.meshes, ["coarse", "coarse", f"full#{len(eng.calls)}"])

    def test_a_change_during_the_coarse_run_drops_it(self):
        eng = ColdEngine()
        c, d, clock, worker, view = make(eng)
        c.open()
        real = eng.preview

        def changed(inputs, progress, should_abort):
            out = real(inputs, progress, should_abort)
            if inputs.get("coarse"):
                d.set("amplitude", 1.2)    # arrives while the coarse result is on its way
            return out
        eng.preview = changed
        worker.run()
        self.assertEqual(view.meshes, [])  # neither the coarse nor the stale full is shown
        clock.advance(preview.DEBOUNCE_S)
        worker.run()
        self.assertEqual(view.meshes[-1], f"full#{len(eng.calls)}")

    def test_cancel_drops_the_coarse(self):
        eng = ColdEngine()
        c, d, clock, worker, view = make(eng)
        c.open()
        c.cancel()
        worker.run()
        self.assertEqual(view.meshes, [])


class EngineCache(unittest.TestCase):
    def setUp(self):
        engine._SUBDIVIDED.clear()
        self.pos = np.asarray(engine.as_positions({"positions": cube_pos(20.0)}))
        self.d = draft.Draft(part="Body")
        self.d.set_texture("Knurling")
        self.d.set("refineLength", 2.0)

    def si(self, **kw):
        return {"draft": self.d.to_dict(), "mesh": {"positions": self.pos}, "face_triangles": {}, **kw}

    def test_coarse_is_coarser_and_not_kept_as_the_full(self):
        full = engine.preview(self.si())
        engine._SUBDIVIDED.clear()
        co = engine.preview(self.si(coarse=True))
        self.assertTrue(co["coarse"] and co["mesh"]["coarse"])
        self.assertGreater(co["mesh"]["edge"], full["mesh"]["edge"] * 2.9)
        self.assertLess(len(co["mesh"]["faces"]), len(full["mesh"]["faces"]))
        self.assertEqual(co["stages"], {})
        self.assertNotIn("regularize", [n for n, _ in co["timings"]])

    def test_kept_in_memory_and_on_disk_and_across_quality(self):
        cd = tempfile.mkdtemp()
        saved = engine.COARSE_TRIS
        engine.COARSE_TRIS = 10   # this small part counts as slow
        try:
            self.assertFalse(engine.kept(self.si(cache_dir=cd)))
            a = engine.preview(self.si(cache_dir=cd))
            self.assertEqual(engine.LAST_SUBDIVIDE["how"], "miss")
            self.assertTrue(engine.kept(self.si(cache_dir=cd)))
            self.assertTrue(engine.kept(self.si(tri_budget=1_500_000)))   # Fine reaches the same edge: shared
            engine.preview(self.si(tri_budget=1_500_000))
            self.assertEqual(engine.LAST_SUBDIVIDE["how"], "hit")
            engine._SUBDIVIDED.clear()                                     # a restart: the disk copy
            self.assertFalse(engine.kept(self.si()))
            self.assertTrue(engine.kept(self.si(cache_dir=cd)))
            b = engine.preview(self.si(cache_dir=cd))
            self.assertEqual(engine.LAST_SUBDIVIDE["how"], "disk")
            for k in ("vertices", "normals", "faces", "colours"):
                np.testing.assert_array_equal(a["mesh"][k], b["mesh"][k])
            self.d.set("refineLength", 3.0)                                # its own setting: not kept
            self.assertFalse(engine.kept(self.si(cache_dir=cd)))
        finally:
            engine.COARSE_TRIS = saved

    def test_disk_cache_keeps_the_newest(self):
        cd = tempfile.mkdtemp()
        saved = engine.DISK_KEEP
        engine.DISK_KEEP = 2
        try:
            for r in (2.0, 2.5, 3.0):
                self.d.set("refineLength", r)
                engine.preview(self.si(cache_dir=cd))
            self.assertEqual(len([f for f in os.listdir(cd) if f.endswith(".npz")]), 2)
        finally:
            engine.DISK_KEEP = saved

    def test_subdivide_abort_alone_stops_the_subdivide(self):
        # A later stage's abort does not stop the subdivide: it is kept for the rerun.
        n = [0]

        def later():
            n[0] += 1
            return n[0] > 1    # stale once the subdivide stage is past
        self.assertIsNone(engine.preview(self.si(subdivide_abort=lambda: False), should_abort=later))
        self.assertTrue(len(engine._SUBDIVIDED) == 1)
        engine._SUBDIVIDED.clear()
        self.assertIsNone(engine.preview(self.si(subdivide_abort=lambda: True)))
        self.assertEqual(len(engine._SUBDIVIDED), 0)


def cube_pos(s):
    from test_wiring import cube

    return cube(s)[0]


class Probe(unittest.TestCase):
    def setUp(self):
        self.lines = []
        timing.FORCE, timing.SINK = True, self.lines.append
        engine._SUBDIVIDED.clear()

    def tearDown(self):
        timing.FORCE = timing.SINK = None
        engine._SUBDIVIDED.clear()

    def test_preview_path_stage_names(self):
        pos = cube_pos(20.0)
        eng = engine.Engine(mesh_for=lambda part: (pos, {}))
        saved = engine.COARSE_TRIS
        engine.COARSE_TRIS = 10
        try:
            c, d, clock, worker, view = make(eng)
            d.set_texture("Knurling")
            d.set("refineLength", 2.0)
            c.open()
            worker.run()
        finally:
            engine.COARSE_TRIS = saved
        joined = "\n".join(self.lines)
        for name in ("preview coarse subdivide1 (engine)", "preview coarse displace (engine)", "preview coarse (worker, total",
                     "preview subdivide1 (engine)", "preview regularize (engine)", "preview subdivide2 (engine)",
                     "preview subdivide total (miss) (engine)", "preview map (engine)", "preview displace (engine)",
                     "preview colours (engine)", "preview drawable (engine)", "preview show_cpu coarse (Coin mesh rebuild)",
                     "preview from subdivide (worker, total)"):
            self.assertIn(name, joined)
        self.assertEqual(c.coarse_shown, 1)
        for x in self.lines:
            if x.startswith("Texturizer timing: "):
                self.assertGreaterEqual(float(x.rsplit(" ", 1)[1]), 0.0)


class HandlerProbe(unittest.TestCase):
    def test_texture_preview_prints_its_stages(self):
        from test_wiring import Session

        class S(Session):
            def runTest(self):
                pass
        s = S()
        s.setUp()
        lines = []
        timing.FORCE, timing.SINK = True, lines.append
        try:
            s.ok("texture_draft", {})
            s.ok("texture_set", {"texture": "Knurling", "settings": {"refineLength": 2}})
            s.ok("texture_preview", {})
        finally:
            timing.FORCE = timing.SINK = None
            s.tearDown()
        joined = "\n".join(lines)
        for name in ("texture_preview mesh_for", "texture_preview subdivide1 (engine)", "texture_preview displace (engine)",
                     "texture_preview engine (total)", "texture_preview estimate"):
            self.assertIn(name, joined)


class GuideSettingNames(unittest.TestCase):
    """Every setting name the texture guide's (and the tool descriptions')
    examples use is taken by texture_set and texture_apply alike."""

    def names(self):
        with open(os.path.join(ROOT, "texturizer", "guides", "texture.md"), encoding="utf-8") as f:
            text = f.read()
        with open(os.path.join(ROOT, "texturizer", "data", "tools.json"), encoding="utf-8") as f:
            text += f.read()
        got = set()
        for m in re.finditer(r"settings:\s*\{([^}]*)\}", text):       # settings: { name: value, ... }
            got.update(re.findall(r"([A-Za-z]\w*)\s*:", m.group(1)))
        for m in re.finditer(r"BumpMesh settings by name[^\"]*", text):   # the descriptions' "e.g." names
            got.update(re.findall(r"\b([a-z]+[A-Z]\w*)\b", m.group(0)))
        got.update(re.findall(r"`(textureHeight|scaleU|mappingMode|refineLength)`", text))
        return got

    def test_both_tools_take_them(self):
        names = self.names()
        self.assertTrue({"textureHeight", "scaleU", "mappingMode", "refineLength", "maxTriangles", "bottomAngleLimit"} <= names, names)
        from texturizer.engine.index import DEFAULTS

        for k in sorted(names):
            v = "cubic" if k == "mappingMode" else DEFAULTS.get(k, 0.5)
            handlers._numbers({k: v}, "settings")                        # texture_apply's check
            live.reset()
            out = handlers.texture_set(handlers.Context(doc=None, out_dir=tempfile.mkdtemp()), {"settings": {k: v}})
            self.assertIn("draft", out, k)
        live.reset()

    def test_texture_height_sets_the_height_slider(self):
        live.reset()
        d = live.draft()
        handlers.texture_set(handlers.Context(doc=None, out_dir=tempfile.mkdtemp()), {"settings": {"textureHeight": 0.8}})
        self.assertEqual(d.get("amplitude"), 0.8)
        with self.assertRaises(handlers.ToolError):
            handlers.texture_set(handlers.Context(doc=None, out_dir=tempfile.mkdtemp()), {"settings": {"regularizeSlack": 2}})
        live.reset()


if __name__ == "__main__":
    unittest.main()
