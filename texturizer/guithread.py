# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The GUI thread's side of Texturizer's threads and timers.

Qt allows a QObject (a timer among them) to be started, stopped and deleted
only on the thread it lives on. A QTimer made, stopped or let go on a worker
thread prints "QObject::startTimer: Timers can only be used with threads
started with QThread" or "Timers cannot be stopped from another thread", and
a timer whose object was deleted on the wrong thread stays in the GUI
thread's timer list: when it fires, Qt delivers the event to freed memory
(found live: FreeCAD 1.1.3 stopped with SIGBUS in
QTimerInfoList::activateTimers, right after texture_preview { cancel } ran
on Agent Link's job thread).

GuiQueue is the one owner of Texturizer's timers. It is made on the GUI
thread and lives as long as the window side (window.Window); from any
thread:

    post(fn)                  fn runs on the GUI thread (a queued signal)
    schedule(seconds, fn)     a single-shot timer, made, started, stopped
                              and deleted on the GUI thread only, parented
                              to the queue's QObject; returns cancel()
    pending()                 timers started and not yet fired or cancelled
    close()                   every timer stopped, the QObject let go

A callback runs only while the queue's QObject is alive (shiboken's
isValid), so nothing is ever delivered to an object that is gone. Plain
Python: QtCore is passed in, so the tests run it with a stand-in."""
import threading


def on_gui_thread():
    """True on FreeCAD's GUI thread (Python's main thread runs FreeCAD's
    window and its event loop)."""
    return threading.current_thread() is threading.main_thread()


def valid(obj):
    """Whether a Qt object's C++ side still exists (shiboken's isValid);
    True when no shiboken can tell (a stand-in)."""
    if obj is None:
        return False
    for name in ("shiboken6", "shiboken2", "shiboken"):
        try:
            mod = __import__(name)
        except ImportError:
            continue
        fn = getattr(mod, "isValid", None)
        if fn is not None:
            try:
                return bool(fn(obj))
            except Exception:  # noqa: BLE001 - not a wrapped object: a stand-in
                return True
    return True


class GuiQueue:
    def __init__(self, QtCore, log=None):
        if not on_gui_thread():
            raise RuntimeError("GuiQueue: make it on the GUI thread (its QObject lives there)")
        self.QtCore = QtCore
        self.log = log
        self._lock = threading.Lock()
        self._timers = {}   # id -> QTimer, started and not yet fired or cancelled
        self._closed = False

        class _Owner(QtCore.QObject):
            fire = QtCore.Signal(object)

        self.owner = _Owner()
        queued = getattr(getattr(QtCore, "Qt", None), "QueuedConnection", None)
        if queued is not None:
            self.owner.fire.connect(self._run, queued)
        else:
            self.owner.fire.connect(self._run)

    # -- the hand-off -----------------------------------------------------------------------
    def alive(self):
        return not self._closed and valid(self.owner)

    def post(self, fn):
        """fn on the GUI thread; dropped when the queue is closed."""
        if not self.alive():
            return False
        try:
            self.owner.fire.emit(fn)
            return True
        except RuntimeError:  # the QObject went between the check and the emit
            return False

    def _run(self, fn):
        if not self.alive():
            return
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - never raise into Qt's event loop
            if self.log is not None:
                try:
                    self.log(f"Texturizer: a queued call failed: {type(e).__name__}: {e}\n")
                except Exception:  # noqa: BLE001
                    pass

    # -- timers ------------------------------------------------------------------------------
    def schedule(self, seconds, fn):
        """fn after seconds, on the GUI thread; returns cancel(), safe from
        any thread. Off the GUI thread the timer is made there (posted)."""
        box = {"cancelled": False, "stop": None}

        def start():
            if box["cancelled"] or not self.alive():
                return
            box["stop"] = self._start(seconds, fn)

        def cancel():
            box["cancelled"] = True
            if not on_gui_thread():
                self.post(cancel)
                return
            stop, box["stop"] = box["stop"], None
            if stop is not None:
                stop()

        if on_gui_thread():
            start()
        else:
            self.post(start)
        return cancel

    def _start(self, seconds, fn):
        QtCore = self.QtCore
        t = QtCore.QTimer(self.owner)   # owned by the queue's QObject: never by a worker, never dropped by Python
        t.setSingleShot(True)
        key = id(t)
        with self._lock:
            self._timers[key] = t

        def fired():
            if self._drop(key) is None:
                return   # cancelled meanwhile
            if valid(t):
                t.deleteLater()
            if self.alive():
                try:
                    fn()
                except Exception as e:  # noqa: BLE001 - never raise into Qt's event loop
                    if self.log is not None:
                        self.log(f"Texturizer: a timer's call failed: {type(e).__name__}: {e}\n")

        def stop():
            got = self._drop(key)
            if got is not None and valid(got):
                got.stop()
                got.deleteLater()

        t.timeout.connect(fired)
        t.start(max(0, int(round(float(seconds) * 1000))))
        return stop

    def _drop(self, key):
        with self._lock:
            return self._timers.pop(key, None)

    def pending(self):
        with self._lock:
            return len(self._timers)

    def close(self):
        """Every timer stopped and deleted, posts dropped from now on; the
        QObject deleted on the GUI thread (later from any other)."""
        if not on_gui_thread():
            self.post(self.close)
            return
        with self._lock:
            timers, self._timers = list(self._timers.values()), {}
        for t in timers:
            if valid(t):
                t.stop()
                t.deleteLater()
        self._closed = True
        if valid(self.owner):
            self.owner.deleteLater()
