# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer. The brush
# cursor (a ring with a dashed hardness core) and the paint / erase colours
# follow BumpMesh's index.html and js/main.js.
"""The brush in the 3D view (design 4.1): an SoEventCallback on the active
view while Brush or Fill is on. The left button paints through
paint.PaintTool; middle and right are never taken, so FreeCAD keeps
navigating, and a press off the model navigates too.

Picking: the camera ray against the body's tessellation (paint.ray_hit),
the triangles the brush paints; never a pick on the preview, whose dense
mesh made each move cost a ray pick through every preview triangle. The
brush circle and the active layer's paint (overlay_shown says when) are
drawn as Coin nodes; the paint as orange with its coverage as alpha, so the
preview under it stays in view. GUI thread only."""
import math

import numpy as np

from . import live, paint, timing

PAINT = (0.85, 0.40, 0.15)   # BumpMesh's user-mask orange
ERASE = (0.60, 0.60, 0.60)   # BumpMesh's erase hover grey (0x999999)
HOVER = (1.0, 0.933, 0.0)    # BumpMesh's hover preview yellow (0xffee00), at 45 % opacity
RING = 64

_held = {}


def overlay_shown(stroke, showing, cpu_mode, stale):
    """Whether the paint overlay draws: while a stroke is on (its paint
    reaches the draft on release), and after it while no preview draws the
    paint itself: the body alone, or a CPU preview (or the Quick look standing
    in for it) not rebuilt since the paint changed. The Quick look and a
    current CPU preview tint the paint themselves."""
    if stroke or showing in (None, "body"):
        return True
    return bool(cpu_mode and stale)


def overlay_rgba(a):
    """Packed RGBA per corner: BumpMesh's orange with the paint (0..1) as alpha."""
    r, g, b = (int(round(c * 255)) for c in PAINT)
    alpha = np.clip(np.round(np.asarray(a, float) * 255), 0, 255).astype(np.int64)
    return ((r << 24) | (g << 16) | (b << 8) | alpha).tolist()


def hold_events(view, on):
    """While a tool listens on the view, its events go to the scene graph
    first and on to the navigation style only when no callback handled them:
    without this, Gesture (and other styles) orbit on a left drag even
    after the brush or a ring took it. Counted, as the brush and the
    gizmo may both hold the same view."""
    n = _held.get(id(view), 0) + (1 if on else -1)
    _held[id(view)] = max(n, 0)
    if (on and n == 1) or (not on and n <= 0):
        try:  # on the view's viewer (View3DInventorViewerPy), not the view
            view.getViewer().setRedirectToSceneGraph(bool(on))
        except Exception:  # noqa: BLE001 - a closed view: nothing to give back
            pass


def _line(coin, vv, pt):
    """The view volume's line through a normalised point: (origin, direction).
    pivy wraps projectPointToLine as one argument, returning an SbLine or
    the (near, far) points; the C++ form fills a line passed in."""
    try:
        got = vv.projectPointToLine(pt)
    except TypeError:
        got = coin.SbLine()
        vv.projectPointToLine(pt, got)
    if hasattr(got, "getPosition"):
        return got.getPosition().getValue(), got.getDirection().getValue()
    near, far = (tuple(p.getValue()) if hasattr(p, "getValue") else tuple(p) for p in got[:2])
    d = [b - a for a, b in zip(near, far)]
    n = sum(c * c for c in d) ** 0.5 or 1.0
    return near, tuple(c / n for c in d)


def _told(fn, name=None):
    """An event callback whose errors reach the Report view whole: pivy
    prints only the exception's class ("<class 'TypeError'>"); with the
    timing probe on (timing.py), its time under name too."""
    return timing.told(fn, name)


class _NoFreeze:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _frozen(window):
    """window.frozen() (one geometry check an event), or nothing for a stand-in window."""
    f = getattr(window, "frozen", None)
    return f() if f is not None else _NoFreeze()


def _drag(window, who, on):
    f = getattr(window, "drag", None)
    if f is not None:
        f(who, on)


class PaintView:
    def __init__(self, window):
        self.w = window
        self.view = None
        self._cbs = []
        self.ring = None
        self.overlay = None
        self.hover = None
        self.stale = False   # the paint changed and the CPU preview has not caught up (Window.mesh_shown)
        self._ov = None      # what the overlay's Coin fields hold: (key, packed colours) (_draw_overlay)
        self.tool = paint.PaintTool(live.draft(), self._surface, self._pick, self._cursor, self._hover,
                                    self._draw_overlay)

    # -- on and off -------------------------------------------------------------------------
    def sync(self):
        """Listen on the active view while Brush or Fill is on, else let go."""
        on = self.tool.tool() in ("brush", "bucket") and self.w.snapshot() is not None
        from .window import _view

        v = _view() if on else None
        if v is not self.view:
            self._detach()
            if v is not None:
                self._attach(v)
        self._draw_overlay()

    def _attach(self, v):
        from pivy import coin

        self.view = v
        for kind, fn, name in ((coin.SoMouseButtonEvent, self._button, "brush button event"),
                               (coin.SoLocation2Event, self._move, "brush move event")):
            self._cbs.append((kind.getClassTypeId(), v.addEventCallbackPivy(kind.getClassTypeId(), _told(fn, name))))
        hold_events(v, True)

    def _detach(self):
        self.tool.cancel()
        _drag(self.w, "paint", False)
        v, self.view = self.view, None
        if v is not None:
            hold_events(v, False)
        for t, cb in self._cbs:
            try:
                v.removeEventCallbackPivy(t, cb)
            except Exception:  # noqa: BLE001 — a closed view
                pass
        self._cbs = []
        for name in ("ring", "overlay", "hover"):
            node = getattr(self, name)
            if node is not None:
                try:
                    v.getSceneGraph().removeChild(node)
                except Exception:  # noqa: BLE001
                    pass
                setattr(self, name, None)
        self._ov = None

    def on_draft(self, ev):
        k = ev.get("kind")
        if k in ("mask", "layers", "load", "reset", "undo", "redo"):
            self.stale = True
        if k == "set" and ev.get("key") == "exclusionTool" or k in ("part", "load", "reset", "undo", "redo"):
            self.sync()
        elif k in ("mask", "active", "layers"):
            self._draw_overlay()

    # -- events -------------------------------------------------------------------------------
    def _button(self, node):
        from pivy import coin

        ev = node.getEvent()
        b = {coin.SoMouseButtonEvent.BUTTON1: paint.LEFT, coin.SoMouseButtonEvent.BUTTON2: paint.RIGHT,
             coin.SoMouseButtonEvent.BUTTON3: paint.MIDDLE}.get(ev.getButton())
        if b is None:
            return
        x, y = ev.getPosition().getValue()
        with _frozen(self.w):
            took = self.tool.button(b, ev.getState() == coin.SoButtonEvent.DOWN, x, y, bool(ev.wasShiftDown()))
        _drag(self.w, "paint", self.tool.stroke is not None)
        if took:
            node.setHandled()

    def _move(self, node):
        ev = node.getEvent()
        x, y = ev.getPosition().getValue()
        with _frozen(self.w):
            took = self.tool.move(x, y, bool(ev.wasShiftDown()))
        if took:
            node.setHandled()

    # -- picking --------------------------------------------------------------------------------
    def _surface(self):
        snap = self.w.snapshot()
        if snap is None:
            return None
        return paint.surface_for(snap[1]), snap[2]

    def _camera(self):
        from pivy import coin

        rm = self.view.getViewer().getSoRenderManager()
        return coin, rm, rm.getCamera(), rm.getViewportRegion()

    @timing.timed("brush pick")
    def _pick(self, x, y):
        coin, _rm, cam, vp = self._camera()
        snap = self.w.snapshot()
        if snap is None or cam is None:
            return None
        org, dirn = self._ray(coin, cam, vp, x, y)
        hit = paint.ray_hit(snap[1], org, dirn)
        if hit is None:
            return None
        ortho = cam.isOfType(coin.SoOrthographicCamera.getClassTypeId())
        cdir = cam.orientation.getValue().multVec(coin.SbVec3f(0, 0, -1)).getValue()
        hit["view"] = paint.view_dir(ortho, cam.position.getValue().getValue(), cdir, hit["point"])
        return hit

    @staticmethod
    def _ray(coin, cam, vp, x, y):
        w, h = vp.getViewportSizePixels().getValue()
        a = vp.getViewportAspectRatio()
        vv = cam.getViewVolume(a)
        if 0 < a < 1.0:  # a tall view: Coin widens the volume by 1 / aspect when it draws
            vv.scale(1.0 / a)
        return _line(coin, vv, coin.SbVec2f(x / max(w, 1), y / max(h, 1)))

    # -- drawing ----------------------------------------------------------------------------------
    def _graph(self):
        return self.view.getSceneGraph() if self.view is not None else None

    @timing.timed("brush cursor draw")
    def _cursor(self, hit, brush, erase):
        g = self._graph()
        if g is None:
            return
        from pivy import coin

        if self.ring is None:
            self.ring = coin.SoSwitch()
            ann = coin.SoAnnotation()
            self._xf, self._rc = coin.SoTransform(), coin.SoBaseColor()
            ds = coin.SoDrawStyle()
            ds.lineWidth = 2
            self._rp, self._rl = coin.SoCoordinate3(), coin.SoLineSet()
            for n in (self._xf, self._rc, ds, self._rp, self._rl):
                ann.addChild(n)
            self.ring.addChild(ann)
            g.addChild(self.ring)
        show = hit is not None and brush is not None and brush.circle
        self.ring.whichChild = 0 if show else -1
        if not show:
            return
        r, core = brush.radius, brush.radius * brush.h

        def at(rr, j):
            t = 2 * math.pi * j / RING
            return (rr * math.cos(t), rr * math.sin(t), 0.0)

        pts = [at(r, j) for j in range(RING + 1)]
        nv = [RING + 1]
        if 0 < brush.h < 1:   # the hardness core, dashed as BumpMesh's cursor
            for i in range(0, RING, 2):
                pts += [at(core, i), at(core, i + 1)]
                nv.append(2)
        self._rp.point.setValues(0, len(pts), pts)
        self._rp.point.setNum(len(pts))
        self._rl.numVertices.setValues(0, len(nv), nv)
        self._rl.numVertices.setNum(len(nv))
        self._xf.translation.setValue(*hit["point"])
        self._xf.rotation.setValue(coin.SbRotation(coin.SbVec3f(0, 0, 1), coin.SbVec3f(*[-c for c in hit["view"]])))
        self._rc.rgb.setValue(*(ERASE if erase else PAINT))

    @timing.timed("brush hover draw")
    def _hover(self, faces, erase):
        """BumpMesh's hover preview: the triangles a click would mark, yellow (grey erasing)."""
        g = self._graph()
        snap = self.w.snapshot()
        if g is None or snap is None:
            return
        from pivy import coin

        if self.hover is None:
            self.hover = coin.SoSwitch()
            sep = coin.SoSeparator()
            po = coin.SoPolygonOffset()
            po.factor, po.units = -2.0, -2.0
            self._hm, self._hc, self._hf = coin.SoMaterial(), coin.SoCoordinate3(), coin.SoFaceSet()
            self._hm.transparency = 0.55
            for n in (po, self._hm, self._hc, self._hf):
                sep.addChild(n)
            self.hover.addChild(sep)
            g.addChild(self.hover)
        if not faces:
            self.hover.whichChild = -1
            return
        P = np.asarray(snap[1], np.float64).reshape(-1, 3, 3)[np.asarray(faces, np.int64)].reshape(-1, 3)
        self._hc.point.setValues(0, len(P), P.tolist())
        self._hc.point.setNum(len(P))
        self._hf.numVertices.setValues(0, len(faces), [3] * len(faces))
        self._hf.numVertices.setNum(len(faces))
        self._hm.diffuseColor.setValue(*(ERASE if erase else HOVER))
        self.hover.whichChild = 0

    @timing.timed("brush overlay recolour")
    def _draw_overlay(self):
        """The active layer's paint on the body's tessellation (overlay_shown).
        Mid-stroke (no Precision split) the overlay holds every source
        triangle, its corners set once at the stroke's start, and each dab
        rewrites only the colours that changed (overlay_runs); otherwise the
        painted triangles alone, rebuilt whole (a mask change, not a dab)."""
        g = self._graph()
        snap = self.w.snapshot()
        c = self.w.controller
        k = self.tool.stroke
        show = g is not None and snap is not None and overlay_shown(
            k is not None, c and c.showing, c is not None and c.cpu_mode, self.stale)
        if not show:
            if self.overlay is not None:
                self.overlay.whichChild = -1
            self._ov = None
            return
        from pivy import coin

        if self.overlay is None:
            self.overlay = coin.SoSwitch()
            sep = coin.SoSeparator()
            po = coin.SoPolygonOffset()
            po.factor, po.units = -1.0, -1.0
            mb = coin.SoMaterialBinding()
            mb.value = coin.SoMaterialBinding.PER_VERTEX
            self._oc, self._om, self._of = coin.SoCoordinate3(), coin.SoPackedColor(), coin.SoFaceSet()
            for n in (po, mb, self._oc, self._om, self._of):
                sep.addChild(n)
            self.overlay.addChild(sep)
            g.addChild(self.overlay)
            self._ov = None
        s = paint.surface_for(snap[1])
        # mid-stroke: the stroke's own state, no replay per dab
        mid = k is not None and k["s"] is s
        st = k["state"] if mid else paint.state_from_mask(s, live.draft().mask(), snap[2])
        if mid and not st.split:
            P = np.asarray(s.positions, np.float64).reshape(-1, 3)
            a = st.corners().reshape(-1)
            key = ("stroke", id(k), len(P))
        else:
            P, a = paint.overlay(st)
            key = None
        on = len(P) // 3
        if not on:
            self.overlay.whichChild = -1
            self._ov = None
            return
        col = np.asarray(overlay_rgba(a), np.int64)
        runs = overlay_runs(self._ov, key, col)
        if runs is None:
            self._oc.point.setValues(0, len(P), P.tolist())
            self._oc.point.setNum(len(P))
            self._om.orderedRGBA.setValues(0, len(col), col.tolist())
            self._om.orderedRGBA.setNum(len(col))
            self._of.numVertices.setValues(0, on, [3] * on)
            self._of.numVertices.setNum(on)
        else:
            f = self._om.orderedRGBA
            for i, j in runs:
                f.setValues(int(i), int(j - i), col[i:j].tolist())
        self._ov = (key, col)
        self.overlay.whichChild = 0


def overlay_runs(prev, key, col):
    """The overlay's colour updates for a dab: None when the corners must be
    set again (the first draw, a new stroke, a mask change: key None), else
    the [start, end) runs of corners whose packed colour changed."""
    if prev is None or key is None or prev[0] != key or len(prev[1]) != len(col):
        return None
    ch = np.nonzero(np.asarray(prev[1]) != np.asarray(col))[0]
    if not len(ch):
        return []
    cut = np.nonzero(np.diff(ch) > 1)[0]
    starts = np.concatenate([[ch[0]], ch[cut + 1]])
    ends = np.concatenate([ch[cut], [ch[-1]]]) + 1
    return list(zip(starts.tolist(), ends.tolist()))
