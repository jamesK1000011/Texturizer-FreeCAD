# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""Threads and timers (guithread.py, preview.PreviewController): every
QTimer is made, started, stopped and deleted on the GUI thread, a worker
hands its calls over, and a cancelled preview leaves no worker running and
no timer waiting. FreeCAD 1.1.3 stopped (SIGBUS in activateTimers: a timer
fired on a deleted object) after texture_preview { cancel } ran on Agent
Link's job thread. Qt is a stand-in that records the thread of every timer
call and runs queued signals only when the test turns its event loop."""
import os
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from texturizer import guithread, preview  # noqa: E402


class FakeQt:
    """QtCore with QObject, Signal (queued when asked), QTimer and an event
    loop the test turns (turn()). Records (what, thread name) for every
    timer call; a timer call off the main thread is an error, as in Qt."""

    def __init__(self):
        self.loop = []
        self.calls = []
        self.errors = []
        self.timers = []
        qt = self

        class Qt:
            QueuedConnection = "queued"

        class _Bound:
            def __init__(self):
                self.slots = []

            def connect(self, fn, kind=None):
                self.slots.append((fn, kind))

            def emit(self, *a):
                for fn, kind in list(self.slots):
                    if kind == "queued":
                        qt.loop.append(lambda fn=fn, a=a: fn(*a))
                    else:
                        fn(*a)

        class Signal:
            def __init__(self, *types):
                self.name = None

            def __set_name__(self, owner, name):
                self.name = name

            def __get__(self, obj, owner=None):
                if obj is None:
                    return self
                key = "_sig_" + self.name
                if key not in obj.__dict__:
                    obj.__dict__[key] = _Bound()
                return obj.__dict__[key]

        class QObject:
            def __init__(self, parent=None):
                self.parent_ = parent
                self.deleted = False
                self.thread_ = threading.current_thread().name

            def deleteLater(self):  # noqa: N802
                qt.loop.append(lambda: setattr(self, "deleted", True))

        class QTimer(QObject):
            def __init__(self, parent=None):
                super().__init__(parent)
                qt.check("QTimer()")
                self.timeout = _Bound()
                self.active = False
                self.single = False
                qt.timers.append(self)

            def setSingleShot(self, on):  # noqa: N802
                self.single = on

            def start(self, ms=0):
                qt.check("start")
                self.active = True

            def stop(self):
                qt.check("stop")
                self.active = False

            def fire(self):
                if self.active and not self.deleted:
                    self.active = not self.single
                    self.timeout.emit()

        self.QtCore = type("QtCore", (), {"Qt": Qt, "Signal": Signal, "QObject": QObject, "QTimer": QTimer})

    def check(self, what):
        name = threading.current_thread().name
        self.calls.append((what, name))
        if threading.current_thread() is not threading.main_thread():
            self.errors.append(f"{what} on {name}")

    def turn(self, fire=True):
        """One turn of the event loop: queued calls, then due timers."""
        while self.loop:
            self.loop.pop(0)()
        if fire:
            for t in list(self.timers):
                t.fire()
            while self.loop:
                self.loop.pop(0)()

    def live_timers(self):
        return [t for t in self.timers if t.active and not t.deleted]


def on_worker(fn):
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("out", fn()), name="agentlink-job-texture_preview")
    t.start()
    t.join(5)
    return box.get("out")


class Queue(unittest.TestCase):
    def setUp(self):
        self.qt = FakeQt()
        self.q = guithread.GuiQueue(self.qt.QtCore)

    def test_post_from_a_worker_runs_on_the_gui_thread(self):
        seen = []
        on_worker(lambda: self.q.post(lambda: seen.append(threading.current_thread().name)))
        self.assertEqual(seen, [])           # not on the worker
        self.qt.turn()
        self.assertEqual(seen, [threading.main_thread().name])

    def test_schedule_from_a_worker_makes_the_timer_on_the_gui_thread(self):
        ran = []
        on_worker(lambda: self.q.schedule(0.15, lambda: ran.append(1)))
        self.assertEqual(self.qt.timers, [])  # nothing made on the worker
        self.qt.turn(fire=False)
        self.assertEqual(len(self.qt.live_timers()), 1)
        self.assertEqual(self.q.pending(), 1)
        self.qt.turn()
        self.assertEqual(ran, [1])
        self.assertEqual(self.q.pending(), 0)
        self.assertTrue(all(t.deleted for t in self.qt.timers))
        self.assertEqual(self.qt.errors, [])

    def test_cancel_from_a_worker_stops_it_on_the_gui_thread(self):
        ran = []
        cancel = self.q.schedule(0.15, lambda: ran.append(1))
        self.assertEqual(len(self.qt.live_timers()), 1)
        on_worker(cancel)
        self.assertEqual(self.qt.errors, [])  # the worker stopped nothing itself
        self.qt.turn()
        self.assertEqual(ran, [])
        self.assertEqual(self.qt.live_timers(), [])
        self.assertEqual(self.q.pending(), 0)

    def test_cancel_before_the_timer_was_made(self):
        ran = []

        def both():
            c = self.q.schedule(0.15, lambda: ran.append(1))
            c()
        on_worker(both)
        self.qt.turn()
        self.assertEqual((ran, self.q.pending(), self.qt.live_timers()), ([], 0, []))

    def test_close_stops_every_timer_and_drops_later_posts(self):
        ran = []
        self.q.schedule(0.1, lambda: ran.append("t"))
        self.q.close()
        self.qt.turn()
        self.assertEqual(self.q.pending(), 0)
        self.assertTrue(self.q.owner.deleted)
        self.assertFalse(self.q.post(lambda: ran.append("p")))
        self.qt.turn()
        self.assertEqual(ran, [])

    def test_made_on_a_worker_is_refused(self):
        with self.assertRaises(RuntimeError):
            on_worker_raises(lambda: guithread.GuiQueue(self.qt.QtCore))


def on_worker_raises(fn):
    box = {}

    def run():
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            box["e"] = e
    t = threading.Thread(target=run)
    t.start()
    t.join(5)
    if "e" in box:
        raise box["e"]


class SlowEngine:
    """engine.preview that runs until should_abort says stop."""

    def __init__(self):
        self.started = threading.Event()

    def preview(self, inputs, progress, should_abort):
        self.started.set()
        t0 = time.time()
        while not should_abort() and time.time() - t0 < 5:
            time.sleep(0.005)
        return None


class Draft:
    def __init__(self):
        self.subs = []
        self.v = {"useDisplacement": True, "previewQuality": "draft"}

    def subscribe(self, fn):
        self.subs.append(fn)
        return lambda: self.subs.remove(fn)

    def get(self, k):
        return self.v.get(k)

    def to_dict(self):
        return dict(self.v)

    def change(self, key):
        for fn in list(self.subs):
            fn({"kind": "set", "key": key})


class Controller(unittest.TestCase):
    def setUp(self):
        self.qt = FakeQt()
        self.q = guithread.GuiQueue(self.qt.QtCore)
        self.engine = SlowEngine()
        self.draft = Draft()
        self.c = preview.PreviewController(self.engine, self.draft, None, quicklook_ok=False, post=self.q.post,
                                           schedule=self.q.schedule, on_gui=guithread.on_gui_thread)

    def test_cancel_then_destroy_leaves_no_thread_and_no_timer(self):
        self.c.open()                       # starts the CPU job at once (no Quick look)
        self.assertTrue(self.engine.started.wait(2))
        self.draft.change("amplitude")      # a change while it runs: a debounce timer
        self.assertEqual(self.q.pending(), 1)
        self.assertTrue(self.c.cancel(wait=2.0))   # the worker stopped and joined
        self.assertFalse(self.c.worker_alive())
        self.qt.turn(fire=False)
        self.assertEqual(self.q.pending(), 0)
        self.assertEqual(self.qt.live_timers(), [])
        self.c.close()
        self.q.close()                      # the window side let go
        self.qt.turn()                      # every queued call the worker left
        self.assertEqual(self.qt.live_timers(), [])
        self.assertEqual(self.qt.errors, [])
        self.assertEqual([t for t in threading.enumerate() if t.name == "TexturizerPreview"], [])

    def test_a_draft_change_on_a_worker_is_handed_to_the_gui_thread(self):
        self.c.open()
        self.assertTrue(self.engine.started.wait(2))
        on_worker(lambda: self.draft.change("amplitude"))
        self.assertEqual(self.qt.errors, [])
        self.assertEqual(self.qt.timers, [])   # no timer made on the worker
        self.qt.turn(fire=False)
        self.assertEqual(self.q.pending(), 1)   # the debounce, made on the GUI thread
        self.assertTrue(self.c.cancel(wait=2.0))
        self.qt.turn(fire=False)
        self.assertEqual(self.q.pending(), 0)
        self.c.close()

    def test_cancel_from_a_worker_stops_nothing_off_the_gui_thread(self):
        self.c.open()
        self.assertTrue(self.engine.started.wait(2))
        self.draft.change("amplitude")
        on_worker(lambda: self.c._cancel_pending_timer())
        self.assertEqual(self.qt.errors, [])
        self.qt.turn(fire=False)
        self.assertEqual(self.q.pending(), 0)
        self.c.cancel(wait=2.0)
        self.c.close()


class Window(unittest.TestCase):
    """window.py's hand-offs, read from its source (it needs FreeCAD to run)."""

    def test_no_bare_timer_and_no_poster_per_open(self):
        src = open(os.path.join(ROOT, "texturizer", "window.py"), encoding="utf-8").read()
        self.assertNotIn("QtCore.QTimer()", src)
        self.assertNotIn("QTimer.singleShot", src)
        self.assertNotIn("_poster()", src)
        for fn in ("def show_result", "def cancel_preview", "def open"):
            body = src[src.index(fn):]
            body = body[:body.index("\n    def ", 10)]
            self.assertIn("guithread.on_gui_thread()", body, fn)

    def test_dialogs_only_on_the_gui_thread(self):
        from texturizer import handlers, live

        class G:
            def ask_path(self, *a, **k):
                raise AssertionError("a dialog on a worker thread")

        old = live.GUI
        live.GUI = G()
        try:
            self.assertTrue(handlers._asks())
            self.assertFalse(on_worker(handlers._asks))

            class S:
                out_dir = "/tmp/out"
            got = on_worker(lambda: handlers._ask_or_default(S(), {}, "stl", "a.stl"))
            self.assertEqual(got, os.path.join("/tmp/out", "a.stl"))
        finally:
            live.GUI = old


if __name__ == "__main__":
    unittest.main()
