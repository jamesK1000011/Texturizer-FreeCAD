# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer. The rings'
# colours and size follow BumpMesh's js/viewer.js.
"""The projection gizmos in FreeCAD's 3D view (gizmo.py): an SoEventCallback
on the active view while the sidebar has a part, the Rotate rings as Coin
lines drawn over the model, and a Qt event filter on the view's viewport
that takes the wheel only while the stamp is armed (FreeCAD's navigation
sees the wheel before the scene graph does). The left button is taken only
on a ring, the stamp's patch or an armed placement; everything else,
middle and right always, navigates as usual. GUI thread only."""
from . import gizmo, live
from .paintview import hold_events
from .qt import QtCore

RING_WIDTH = 3


class GizmoView:
    def __init__(self, window):
        self.w = window
        self.view = None
        self._cbs = []
        self.rings = None
        self._filter = None
        lo, hi = _scale_range()
        self.tool = gizmo.GizmoTool(live.draft(), self._positions, self._ray, self._draw, self._armed, (lo, hi))

    # -- on and off -------------------------------------------------------------------------
    def sync(self):
        from .window import _view

        v = _view() if self.w.snapshot() is not None else None
        if v is not self.view:
            self._detach()
            if v is not None:
                self._attach(v)
        self.tool.draw()

    def _attach(self, v):
        from pivy import coin

        self.view = v
        for kind, fn in ((coin.SoMouseButtonEvent, self._button), (coin.SoLocation2Event, self._move)):
            self._cbs.append((kind.getClassTypeId(), v.addEventCallbackPivy(kind.getClassTypeId(), fn)))
        hold_events(v, True)
        try:
            vp = v.graphicsView().viewport()
            self._filter = _WheelFilter(self.tool)
            vp.installEventFilter(self._filter)
            self._vp = vp
        except Exception:  # noqa: BLE001 - no graphicsView: the wheel zooms as ever
            self._filter = None

    def _detach(self):
        self.tool.cancel()
        v, self.view = self.view, None
        if v is not None:
            hold_events(v, False)
        for t, cb in self._cbs:
            try:
                v.removeEventCallbackPivy(t, cb)
            except Exception:  # noqa: BLE001 - a closed view
                pass
        self._cbs = []
        if self._filter is not None:
            try:
                self._vp.removeEventFilter(self._filter)
            except Exception:  # noqa: BLE001
                pass
            self._filter = None
        if self.rings is not None:
            try:
                v.getSceneGraph().removeChild(self.rings)
            except Exception:  # noqa: BLE001
                pass
            self.rings = None

    def on_draft(self, ev):
        k = ev.get("kind")
        if k in ("part", "load", "reset"):
            self.tool.cancel()
            self.sync()
        elif k == "set" and ev.get("key") in ("rotate", "exclusionTool"):
            if ev.get("key") == "exclusionTool" and ev.get("value"):
                self.tool.cancel()   # BumpMesh: a tool ends placement
            self.sync()
        elif k in ("undo", "redo"):
            self.tool.draw()

    def arm_place(self):
        """The sidebar's Position on model."""
        self.sync()
        return self.view is not None and self.tool.arm_place()

    # -- events -----------------------------------------------------------------------------
    def _button(self, node):
        from pivy import coin

        ev = node.getEvent()
        b = {coin.SoMouseButtonEvent.BUTTON1: gizmo.LEFT, coin.SoMouseButtonEvent.BUTTON2: 3,
             coin.SoMouseButtonEvent.BUTTON3: 2}.get(ev.getButton())
        if b is None:
            return
        x, y = ev.getPosition().getValue()
        if self.tool.button(b, ev.getState() == coin.SoButtonEvent.DOWN, x, y):
            node.setHandled()

    def _move(self, node):
        x, y = node.getEvent().getPosition().getValue()
        if self.tool.move(x, y):
            node.setHandled()

    # -- the part and the camera --------------------------------------------------------------
    def _positions(self):
        snap = self.w.snapshot()
        return None if snap is None else snap[1]

    def _ray(self, x, y):
        from pivy import coin

        from .paintview import PaintView

        rm = self.view.getViewer().getSoRenderManager()
        return PaintView._ray(coin, rm.getCamera(), rm.getViewportRegion(), x, y)

    # -- drawing ------------------------------------------------------------------------------
    def _draw(self, g):
        if self.view is None:
            return
        from pivy import coin

        if self.rings is None:
            self.rings = coin.SoSwitch()
            ann = coin.SoAnnotation()   # over the model, as BumpMesh's depthTest: false
            ds = coin.SoDrawStyle()
            ds.lineWidth = RING_WIDTH
            ann.addChild(ds)
            self._ring = {}
            for axis in ("x", "y", "z"):
                sep = coin.SoSeparator()
                col, pts, ls = coin.SoBaseColor(), coin.SoCoordinate3(), coin.SoLineSet()
                for n in (col, pts, ls):
                    sep.addChild(n)
                ann.addChild(sep)
                self._ring[axis] = (col, pts, ls)
            self.rings.addChild(ann)
            self.view.getSceneGraph().addChild(self.rings)
        if g is None:
            self.rings.whichChild = -1
            return
        for axis, (col, pts, ls) in self._ring.items():
            P = gizmo.ring_points(g["centre"], g["radius"], axis).tolist()
            pts.point.setValues(0, len(P), P)
            pts.point.setNum(len(P))
            ls.numVertices.setValue(len(P))
            lit = axis in (g.get("hover"), g.get("drag"))
            col.rgb.setValue(*(gizmo.HOVER_COLOURS if lit else gizmo.COLOURS)[axis])
        self.rings.whichChild = 0

    def _armed(self, on):
        sb = self.w.sidebar
        if sb is not None:
            from . import controls

            sb.say(controls.string("ui.stampHint") if on else "")


def _scale_range():
    from . import controls

    b = controls.BY_KEY["scaleU"].get("box") or {}
    return float(b.get("min", 0.0)), float(b.get("max", 1e9))


class _WheelFilter(QtCore.QObject if QtCore is not None else object):
    """The viewport's wheel to the stamp while armed (one notch, 120 eighths of
    a degree, as a browser's 100 px deltaY; up grows it)."""

    def __init__(self, tool):
        super().__init__()
        self.tool = tool

    def eventFilter(self, obj, ev):  # noqa: N802
        try:
            if ev.type() == QtCore.QEvent.Wheel:
                dy = -ev.angleDelta().y() / 120.0 * gizmo.WHEEL_PX_PER_STEP
                return bool(dy) and self.tool.wheel(dy)
        except Exception:  # noqa: BLE001 - never let a filter break the view
            return False
        return False
