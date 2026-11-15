# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The preview controller with a fake engine, a fake clock and a worker the
test runs by hand: debounce, stale results dropped, cancel, the stage-cache
rerun points, the Quick look / CPU swap and the fallback without the shader."""
import os
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from texturizer import draft, preview  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 0.0
        self.timers = []

    def schedule(self, seconds, fn):
        t = [self.now + seconds, fn, True]
        self.timers.append(t)

        def cancel():
            t[2] = False

        return cancel

    def advance(self, seconds):
        self.now += seconds
        for t in list(self.timers):
            if t[2] and t[0] <= self.now + 1e-9:
                t[2] = False
                t[1]()


class Worker:
    """run_async stand-in: jobs wait until the test runs them."""

    def __init__(self):
        self.jobs = []

    def __call__(self, fn):
        self.jobs.append(fn)

    def run(self):
        fn = self.jobs.pop(0)
        fn()


class Engine:
    def __init__(self, abort_check=True, fail=False):
        self.calls = []
        self.abort_check = abort_check
        self.fail = fail

    def preview(self, inputs, progress, should_abort):
        self.calls.append(inputs)
        if self.fail:
            raise RuntimeError("out of memory")
        stages = {}
        for s in preview.STAGES[preview.STAGES.index(inputs["rerun_from"]):]:
            if self.abort_check and should_abort():
                return None
            progress(s, 1.0)
            stages[s] = f"{s}#{len(self.calls)}"
        return {"stages": stages, "mesh": f"mesh#{len(self.calls)}"}


class View:
    def __init__(self, body=True):
        self.body = body
        self.log = []
        self.ql = self.cpu = False
        self.stale = False
        self.meshes = []

    def get_body_visible(self):
        return self.body

    def set_body_visible(self, on):
        self.body = on

    def set_quicklook_visible(self, on):
        self.ql = on

    def set_cpu_visible(self, on):
        self.cpu = on

    def set_cpu_stale(self, on):
        self.stale = on

    def show_cpu(self, mesh):
        self.meshes.append(mesh)

    def update_quicklook(self, stage):
        self.log.append(("uniforms", stage))

    def lock_preview(self, on, text):
        self.log.append(("lock", on, text))

    def progress(self, stage, fraction):
        self.log.append(("progress", stage, fraction))

    def note(self, text):
        self.log.append(("note", text))

    def error(self, text):
        self.log.append(("error", text))


def make(ql=True, d3=True, engine=None, body=True):
    d = draft.Draft("Body")
    if d3:
        d.set("useDisplacement", True)
    clock, worker, view = Clock(), Worker(), View(body)
    eng = engine or Engine()
    c = preview.PreviewController(eng, d, view, quicklook_ok=ql, run_async=worker, schedule=clock.schedule)
    return c, d, clock, worker, view, eng


class StageTable(unittest.TestCase):
    def test_design_table(self):
        d = draft.Draft()
        cases = [({"kind": "part"}, "subdivide"), ({"kind": "set", "key": "refineLength"}, "subdivide"),
                 ({"kind": "set", "key": "previewQuality"}, "subdivide"),
                 ({"kind": "set", "key": "mappingMode"}, "map"), ({"kind": "frame"}, "map"),
                 ({"kind": "set", "key": "scaleU"}, "map"), ({"kind": "set", "key": "rotation"}, "map"),
                 ({"kind": "set", "key": "amplitude"}, "displace"),
                 ({"kind": "set", "key": "invertDisplacement"}, "displace"),
                 ({"kind": "set", "key": "textureSmoothing"}, "displace"),
                 ({"kind": "set", "key": "boundaryFalloff"}, "displace"), ({"kind": "layer"}, "displace"),
                 ({"kind": "active"}, "colours"), ({"kind": "set", "key": "brushSize"}, None),
                 ({"kind": "set", "key": "lockScale"}, None), ({"kind": "undo"}, "subdivide")]
        for ev, want in cases:
            self.assertEqual(preview.stage_for(ev, d), want, ev)
        self.assertEqual(preview.stage_for({"kind": "mask"}, d), "subdivide")  # Preserve Untextured on
        d.set("preserveUntextured", False)
        self.assertEqual(preview.stage_for({"kind": "mask"}, d), "displace")


class Controller(unittest.TestCase):
    def test_open_runs_first_job_and_swaps(self):
        c, d, clock, worker, view, eng = make()
        c.open()
        self.assertTrue(view.ql and not view.cpu and not view.body)  # Quick look at once, body hidden
        self.assertEqual(len(worker.jobs), 1)
        worker.run()
        self.assertEqual(eng.calls[0]["rerun_from"], "subdivide")
        self.assertEqual(eng.calls[0]["tri_budget"], 500_000)
        self.assertTrue(view.cpu and not view.ql and not view.body)
        self.assertEqual(view.meshes, ["mesh#1"])

    def test_debounce(self):
        c, d, clock, worker, view, eng = make()
        c.open()
        worker.run()
        for i in range(5):  # a slider drag: every 50 ms
            clock.advance(0.05)
            d.set("scaleU", 10 + i, coalesce=True)
        self.assertEqual(worker.jobs, [])
        self.assertTrue(view.ql and not view.cpu)  # Quick look while dragging, stale CPU node hidden
        self.assertGreaterEqual(view.log.count(("uniforms", "map")), 5)  # Lock Scale moves V too
        clock.advance(0.149)
        self.assertEqual(worker.jobs, [])
        clock.advance(0.002)
        self.assertEqual(len(worker.jobs), 1)
        worker.run()
        self.assertEqual(eng.calls[-1]["rerun_from"], "map")
        self.assertTrue(view.cpu and not view.ql)

    def test_commit_skips_debounce(self):
        c, d, clock, worker, view, eng = make()
        c.open()
        worker.run()
        d.set("amplitude", 1.2)
        c.commit()
        self.assertEqual(len(worker.jobs), 1)

    def test_rerun_points(self):
        c, d, clock, worker, view, eng = make()
        c.open()
        worker.run()
        for change, want, cached in [(lambda: d.set("amplitude", 0.9), "displace", ["subdivide", "map"]),
                                     (lambda: d.set("offsetU", 0.3), "map", ["subdivide"]),
                                     (lambda: d.set("refineLength", 0.5), "subdivide", []),
                                     (lambda: d.set_active(0) or c.on_draft({"kind": "active"}), "colours",
                                      ["subdivide", "map", "displace"])]:
            change()
            clock.advance(0.2)
            worker.run()
            self.assertEqual(eng.calls[-1]["rerun_from"], want)
            self.assertEqual(sorted(eng.calls[-1]["cache"]), sorted(cached))
        # the cache passes the earlier stages' own results on
        d.set("amplitude", 0.4)
        clock.advance(0.2)
        worker.run()
        self.assertTrue(eng.calls[-1]["cache"]["subdivide"].startswith("subdivide#"))

    def test_two_changes_rerun_from_the_earlier(self):
        c, d, clock, worker, view, eng = make()
        c.open()
        worker.run()
        d.set("amplitude", 0.9)
        d.set("scaleU", 5)
        clock.advance(0.2)
        worker.run()
        self.assertEqual(eng.calls[-1]["rerun_from"], "map")

    def test_stale_result_dropped(self):
        c, d, clock, worker, view, eng = make(engine=Engine(abort_check=False))
        c.open()
        worker.run()
        d.set("amplitude", 0.9)
        clock.advance(0.2)
        self.assertEqual(len(worker.jobs), 1)
        d.set("amplitude", 1.1)  # while the job runs
        clock.advance(0.2)  # the debounce fires while the worker is busy: waits
        self.assertEqual(len(worker.jobs), 1)
        worker.run()  # the stale job finishes: dropped, the waiting job starts
        self.assertEqual(c.dropped, 1)
        self.assertEqual(view.meshes, ["mesh#1"])
        self.assertTrue(view.ql and not view.cpu)
        self.assertEqual(len(worker.jobs), 1)
        worker.run()
        self.assertEqual(view.meshes, ["mesh#1", "mesh#3"])
        self.assertEqual(eng.calls[-1]["draft"]["layers"][0]["settings"]["amplitude"], 1.1)

    def test_should_abort_stops_engine(self):
        c, d, clock, worker, view, eng = make()
        c.open()
        d.set("rotation", 10)  # stale before the first job runs
        worker.run()
        self.assertEqual(c.dropped, 1)
        self.assertEqual(view.meshes, [])

    def test_progress_only_for_current(self):
        c, d, clock, worker, view, eng = make()
        c.open()
        worker.run()
        self.assertIn(("progress", "subdivide", 1.0), view.log)

    def test_cancel_restores_body(self):
        c, d, clock, worker, view, eng = make()
        c.open()
        self.assertFalse(view.body)
        c.cancel()
        self.assertTrue(view.body and not view.ql and not view.cpu)
        worker.run()  # the cancelled job's result is dropped
        self.assertEqual(view.meshes, [])
        self.assertTrue(view.body)
        d.set("amplitude", 0.7)  # a change resumes the preview
        self.assertTrue(view.ql and not view.body)

    def test_close_restores_hidden_body_and_unsubscribes(self):
        c, d, clock, worker, view, eng = make(body=False)
        c.open()
        worker.run()
        c.close()
        self.assertFalse(view.body)  # it was hidden before: stays hidden
        n = len(view.log)
        d.set("amplitude", 0.2)
        self.assertEqual(len(view.log), n)

    def test_error_restores_body(self):
        c, d, clock, worker, view, eng = make(engine=Engine(fail=True))
        c.open()
        worker.run()
        self.assertTrue(view.body and not view.ql and not view.cpu)
        self.assertTrue(any(e[0] == "error" and "out of memory" in e[1] for e in view.log))

    def test_colours_skip_the_swap(self):
        c, d, clock, worker, view, eng = make()
        c.open()
        worker.run()
        c.on_draft({"kind": "active"})
        self.assertTrue(view.cpu and not view.ql)
        clock.advance(0.2)
        worker.run()
        self.assertTrue(view.cpu and not view.ql)

    def test_3d_preview_off_is_quicklook_only(self):
        c, d, clock, worker, view, eng = make(d3=False)
        c.open()
        self.assertEqual(worker.jobs, [])
        self.assertTrue(view.ql and not view.body)
        d.set("scaleU", 7)
        clock.advance(0.5)
        self.assertEqual(worker.jobs, [])
        self.assertIn(("uniforms", "map"), view.log)
        d.set("useDisplacement", True)  # on: a job at once, Quick look until it lands
        self.assertEqual(len(worker.jobs), 1)
        worker.run()
        self.assertTrue(view.cpu and not view.ql)
        d.set("useDisplacement", False)
        self.assertTrue(view.ql and not view.cpu)
        d.set("useDisplacement", True)  # nothing changed: the CPU node is current
        self.assertEqual(worker.jobs, [])
        self.assertTrue(view.cpu)

    def test_fine_budget(self):
        c, d, clock, worker, view, eng = make()
        d.set("previewQuality", "fine")
        c.open()
        worker.run()
        self.assertEqual(eng.calls[0]["tri_budget"], 1_500_000)


class Fallback(unittest.TestCase):
    def test_no_shader(self):
        c, d, clock, worker, view, eng = make(ql=False, d3=False)
        c.open()
        self.assertIn(("lock", True, preview.NO_QUICKLOOK), view.log)
        self.assertTrue(view.body and not view.ql)  # plain body while the first job runs
        self.assertEqual(len(worker.jobs), 1)  # 3D Preview off, yet the CPU preview runs
        worker.run()
        self.assertTrue(view.cpu and not view.body)
        d.set("scaleU", 9)  # dragging: the last CPU preview stays, grey
        self.assertTrue(view.cpu and view.stale and not view.ql)
        self.assertNotIn(("uniforms", "map"), view.log)
        clock.advance(0.2)
        worker.run()
        self.assertFalse(view.stale)

    def test_shader_fails_later(self):
        c, d, clock, worker, view, eng = make(ql=True, d3=False)
        c.open()
        self.assertTrue(view.ql)
        c.quicklook_failed("GLSL link error")
        self.assertIn(("lock", True, preview.NO_QUICKLOOK), view.log)
        self.assertTrue(view.body and not view.ql)
        self.assertEqual(len(worker.jobs), 1)
        worker.run()
        self.assertTrue(view.cpu)


class RealThreads(unittest.TestCase):
    def test_default_worker_and_timer(self):
        d = draft.Draft("Body")
        d.set("useDisplacement", True)
        done = threading.Event()
        view = View()
        view.show_cpu = lambda mesh: done.set()
        c = preview.PreviewController(Engine(), d, view, quicklook_ok=True, debounce=0.01)
        c.open()
        self.assertTrue(done.wait(5))
        done.clear()
        d.set("amplitude", 0.3)
        self.assertTrue(done.wait(5))
        c.close()


if __name__ == "__main__":
    unittest.main()
