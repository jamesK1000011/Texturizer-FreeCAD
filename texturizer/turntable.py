# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# The Turntable of BumpMesh (stlTexturizer) by Stefan Hermann (CNC Kitchen),
# AGPL-3.0, github.com/CNCKitchen/stlTexturizer: js/viewer.js (setTurntable,
# _stepTurntable, the stop on pointerdown / wheel / SpaceMouse) and
# js/textureGallery.js (turntableWanted, the choice that sticks).
"""The Texture Gallery's Turntable: FreeCAD's 3D view spins slowly about the
vertical (Z) axis through the model's centre while the person browses, one
revolution every 24 s, as BumpMesh's camera auto-orbit. The model spins in
place on screen however the view was panned or zoomed. Any press of a mouse
button, the wheel or a SpaceMouse in the view hands control back and ends
the spin (the button pops out; the choice is not changed). The spin starts
when the gallery opens unless the person switched it off, which sticks.

The rotation is plain math (step); Turntable drives it with an injected
timer and a view adapter, so it runs without FreeCAD or Qt in the tests.
CoinAdapter is the FreeCAD side: the active view's camera through pivy.
"""
import math
import time

RAD_PER_S = 2 * math.pi / 24   # viewer.js _TURNTABLE_RAD_PER_S: one revolution every 24 s
MAX_DT = 0.1                   # no jump after the window was in the background
INTERVAL_MS = 16
PARAMS = "User parameter:BaseApp/Preferences/Mod/Texturizer"
KEY = "Turntable"
INPUTS = ("SoMouseButtonEvent", "SoMouseWheelEvent", "SoMotion3Event")


def quat_mul(a, b):
    """Hamilton product a * b of (x, y, z, w) quaternions: b first, then a."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def step(position, orientation, pivot, angle):
    """_stepTurntable: the camera (position, orientation (x, y, z, w)) yawed by
    angle about world Z through pivot, its orientation premultiplied."""
    c, s = math.cos(angle), math.sin(angle)
    x, y, z = (position[i] - pivot[i] for i in range(3))
    pos = (pivot[0] + c * x - s * y, pivot[1] + s * x + c * y, pivot[2] + z)
    q = (0.0, 0.0, math.sin(angle / 2), math.cos(angle / 2))
    o = quat_mul(q, orientation)
    n = math.sqrt(sum(v * v for v in o)) or 1.0
    return pos, tuple(v / n for v in o)


class Turntable:
    """setTurntable(on, onStop) on a view adapter: adapter() -> an object with
    pose() -> (position, orientation) or None, set_pose(position, orientation),
    pivot() -> (x, y, z) or None and listen(fn) -> unlisten (fn() on the
    person's input), or None when there is no 3D view. timer(ms, fn) ->
    stop() calls fn every ms."""

    def __init__(self, adapter, timer, clock=time.monotonic):
        self.adapter = adapter
        self.timer = timer
        self.clock = clock
        self.on = None   # {"view", "last", "stop", "unlisten", "on_stop"} while spinning

    @property
    def spinning(self):
        return self.on is not None

    def start(self, on_stop=None):
        """Spin; False when there is no view to spin. on_stop fires once when
        the person takes the view back (not on stop())."""
        if self.on is not None:
            self.on["on_stop"] = on_stop
            return True
        v = self.adapter()
        if v is None or v.pose() is None:
            return False
        self.on = {"view": v, "last": self.clock(), "on_stop": on_stop, "stop": None, "unlisten": None}
        self.on["unlisten"] = v.listen(self.taken)
        self.on["stop"] = self.timer(INTERVAL_MS, self.tick)
        return True

    def stop(self):
        on, self.on = self.on, None
        if on is None:
            return
        for k in ("stop", "unlisten"):
            if on[k] is not None:
                try:
                    on[k]()
                except Exception:  # noqa: BLE001 - a closed view
                    pass

    def taken(self, *_a):
        """The person pressed, wheeled or moved the puck in the view: their view again."""
        if self.on is None:
            return
        fn = self.on["on_stop"]
        self.stop()
        if fn is not None:
            fn()

    def tick(self):
        on = self.on
        if on is None:
            return
        now = self.clock()
        dt = min(now - on["last"], MAX_DT)
        on["last"] = now
        if dt <= 0:
            return
        try:
            pose = on["view"].pose()
            piv = on["view"].pivot()
            if pose is None or piv is None:
                return
            on["view"].set_pose(*step(pose[0], pose[1], piv, dt * RAD_PER_S))
        except Exception:  # noqa: BLE001 - the view went away: stop quietly
            self.stop()


class Prefs:
    """The Turntable choice: FreeCAD's parameters, else kept in memory.
    wanted(): spins by default; switching it off sticks (turntableWanted)."""

    def __init__(self, params=None):
        self.params = params
        self.mem = None
        if params is None:
            try:
                import FreeCAD

                self.params = FreeCAD.ParamGet(PARAMS)
            except Exception:  # noqa: BLE001
                self.params = None

    def wanted(self):
        if self.params is not None:
            try:
                return bool(self.params.GetBool(KEY, True))
            except Exception:  # noqa: BLE001
                pass
        return True if self.mem is None else self.mem

    def set(self, on):
        self.mem = bool(on)
        if self.params is not None:
            try:
                self.params.SetBool(KEY, bool(on))
            except Exception:  # noqa: BLE001
                pass


# -- FreeCAD -------------------------------------------------------------------------------
class CoinAdapter:
    """The active 3D view's camera. pivot_of() -> the model's centre or None
    (then the camera's focal point)."""

    def __init__(self, view, pivot_of=None):
        self.view = view
        self.pivot_of = pivot_of or (lambda: None)

    def _cam(self):
        return self.view.getCameraNode()

    def pose(self):
        cam = self._cam()
        if cam is None:
            return None
        return tuple(cam.position.getValue().getValue()), tuple(cam.orientation.getValue().getValue())

    def set_pose(self, position, orientation):
        cam = self._cam()
        cam.position.setValue(*position)
        cam.orientation.setValue(*orientation)

    def pivot(self):
        p = self.pivot_of()
        if p is not None:
            return tuple(p)
        pos, o = self.pose()
        from pivy import coin

        d = coin.SbRotation(*o).multVec(coin.SbVec3f(0, 0, -1)).getValue()
        f = self._cam().focalDistance.getValue()
        return tuple(pos[i] + d[i] * f for i in range(3))

    def listen(self, fn):
        def cb(info):
            if info.get("Type") == "SoMouseButtonEvent" and info.get("State") != "DOWN":
                return
            fn()

        got = []
        for kind in INPUTS:
            try:
                got.append((kind, self.view.addEventCallback(kind, cb)))
            except Exception:  # noqa: BLE001 - an event type this Coin does not have
                pass

        def unlisten():
            for kind, c in got:
                try:
                    self.view.removeEventCallback(kind, c)
                except Exception:  # noqa: BLE001
                    pass
        return unlisten


def _active_view():
    try:
        import FreeCADGui

        v = FreeCADGui.ActiveDocument.ActiveView
        return v if hasattr(v, "getCameraNode") else None
    except Exception:  # noqa: BLE001
        return None


def _model_centre():
    """The draft's part's bounding-box centre (BumpMesh's pivot: the model's centre)."""
    try:
        import FreeCAD

        from . import live

        o = FreeCAD.ActiveDocument.getObject(live.draft().part or "")
        bb = o.Shape.BoundBox if hasattr(o, "Shape") and not o.Shape.isNull() else o.Mesh.BoundBox
        return (bb.Center.x, bb.Center.y, bb.Center.z)
    except Exception:  # noqa: BLE001
        return None


def _qt_timer(ms, fn):
    from .qt import QtCore

    t = QtCore.QTimer()
    t.setInterval(ms)
    t.timeout.connect(fn)
    t.start()
    return t.stop


def freecad():
    """The Turntable on FreeCAD's active view."""
    def adapter():
        v = _active_view()
        return CoinAdapter(v, _model_centre) if v is not None else None

    return Turntable(adapter, _qt_timer)
