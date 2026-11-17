# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""texture_preview on the window thread with a time budget
(PreviewController.build_now): the maths on a real worker thread, its
hand-offs run by the waiting GUI thread as they come (the window's queue is
not turned while a tool runs), the result drawn before the answer; past the
budget the build goes on, its later hand-offs go to the window's queue, and
the next call waits for that same build. An agent's preview (forced) stays
the CPU one through draft changes until 3D Preview is turned off."""
import os
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from texturizer import draft, preview  # noqa: E402


class Engine:
    """engine.preview on the worker: waits for gate (when given), then
    answers with every stage from rerun_from on."""

    def __init__(self, gate=None):
        self.calls = []
        self.gate = gate
        self.threads = []

    def preview(self, inputs, progress, should_abort):
        self.calls.append(inputs["rerun_from"])
        self.threads.append(threading.current_thread().name)
        if self.gate is not None:
            while not self.gate.wait(0.01):
                if should_abort():
                    return None
        stages = {s: s for s in preview.STAGES[preview.STAGES.index(inputs["rerun_from"]):]}
        progress(inputs["rerun_from"], 1.0)
        return {"stages": stages, "mesh": {"faces": [[0, 1, 2]] * 7, "n": len(self.calls)}, "note": None}


class View:
    def __init__(self):
        self.shown = []        # (mesh, thread name)
        self.state = {}

    def get_body_visible(self):
        return True

    def show_cpu(self, mesh):
        self.shown.append((mesh, threading.current_thread().name))

    def __getattr__(self, name):
        return lambda *a, **k: None


class Queue:
    """The window's queue: posts kept until the test turns it."""

    def __init__(self):
        self.items = []
        self.lock = threading.Lock()

    def __call__(self, fn):
        with self.lock:
            self.items.append(fn)
        return True

    def turn(self):
        with self.lock:
            got, self.items = self.items, []
        for fn in got:
            fn()
        return len(got)


def controller(engine, quicklook=False):
    q = Queue()
    d = draft.Draft(part="Box")
    c = preview.PreviewController(engine, d, View(), quicklook_ok=quicklook, post=q,
                                  schedule=lambda s, fn: (lambda: None), on_gui=lambda: True)
    return c, q, d


class BuildNow(unittest.TestCase):
    def test_done_within_the_budget_is_drawn_here(self):
        e = Engine()
        c, q, _d = controller(e)
        got = c.build_now(5.0)
        self.assertTrue(got["done"], got)
        self.assertEqual(len(got["result"]["mesh"]["faces"]), 7)
        self.assertEqual(c.showing, "cpu")
        self.assertEqual([t for _m, t in c.view.shown], [threading.main_thread().name])   # drawn on this thread
        self.assertNotEqual(e.threads[0], threading.main_thread().name)                    # the maths on a worker
        self.assertEqual(q.items, [])            # nothing left for the window's queue
        self.assertIs(c.post, q)                 # the queue given back

    def test_a_current_preview_answers_at_once(self):
        e = Engine()
        c, _q, _d = controller(e)
        c.build_now(5.0)
        got = c.build_now(5.0)
        self.assertTrue(got["done"] and got["reused"])
        self.assertEqual(len(e.calls), 1)

    def test_a_change_reruns_from_its_stage_only(self):
        e = Engine()
        c, _q, d = controller(e)
        c.build_now(5.0)
        d.set("amplitude", 0.9)
        got = c.build_now(5.0)
        self.assertTrue(got["done"])
        self.assertEqual(e.calls, ["subdivide", "displace"])

    def test_past_the_budget_it_goes_on_and_the_next_call_waits_for_it(self):
        gate = threading.Event()
        e = Engine(gate)
        c, q, _d = controller(e)
        got = c.build_now(0.05)
        self.assertFalse(got["done"])
        self.assertIsNone(got["result"])
        self.assertIsNotNone(c.running)          # still building
        self.assertIs(c.post, q)
        gate.set()
        c.wait(2.0)
        self.assertGreaterEqual(q.turn(), 1)     # its hand-offs went to the window's queue
        self.assertEqual(c.showing, "cpu")
        self.assertEqual(len(c.view.shown), 1)
        got = c.build_now(5.0)                   # the same build, not a second one
        self.assertTrue(got["done"] and got["reused"])
        self.assertEqual(len(e.calls), 1)

    def test_a_second_call_while_it_builds_waits_for_the_same_job(self):
        gate = threading.Event()
        e = Engine(gate)
        c, q, _d = controller(e)
        c.build_now(0.05)
        threading.Timer(0.1, gate.set).start()
        got = c.build_now(5.0)
        self.assertTrue(got["done"], got)
        self.assertEqual(len(e.calls), 1)
        q.turn()
        self.assertEqual(len(c.view.shown), 1)

    def test_an_engine_error_is_answered(self):
        class Bad(Engine):
            def preview(self, inputs, progress, should_abort):
                raise MemoryError("1 GB")

        c, _q, _d = controller(Bad())
        got = c.build_now(5.0)
        self.assertFalse(got["done"])
        self.assertIn("1 GB", got["error"])
        self.assertIsNone(c.showing)


class Forced(unittest.TestCase):
    def test_an_agents_preview_stays_the_cpu_one_until_3d_preview_is_turned_off(self):
        e = Engine()
        c, q, d = controller(e, quicklook=True)
        c.open()                                 # the Quick look only: no CPU job
        self.assertEqual(e.calls, [])
        c.forced = True                          # texture_preview
        self.assertTrue(c.build_now(5.0)["done"])
        self.assertEqual(c.showing, "cpu")
        d.set("amplitude", 0.9)                  # a draft change rebuilds the CPU preview (debounced)
        self.assertTrue(c.cpu_mode)
        d.set("useDisplacement", True)
        d.set("useDisplacement", False)          # the person turns 3D Preview off: that wins
        self.assertFalse(c.forced)
        self.assertEqual(c.showing, "quicklook")

    def test_close_and_cancel_end_it(self):
        c, _q, _d = controller(Engine(), quicklook=True)
        c.forced = True
        c.close()
        self.assertFalse(c.forced)
        c.forced = True
        c.cancel()
        self.assertFalse(c.forced)

    def test_open_after_cancel_subscribes_once(self):
        c, _q, d = controller(Engine())
        c.open()
        c.cancel()
        c.open()
        self.assertEqual(sum(1 for f in d._subs if getattr(f, "__self__", None) is c), 1)


if __name__ == "__main__":
    unittest.main()
