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

Picking: an SoRayPickAction hit on the CPU preview node, its SoFaceDetail
face mapped to the source triangle through the preview's face_parent;
with no CPU preview shown, the camera ray against the body's tessellation
(paint.ray_hit). The brush circle and, while the CPU preview is not shown,
the active layer's paint are drawn as Coin nodes. GUI thread only."""
import math

import numpy as np

from . import live, paint

PAINT = (0.85, 0.40, 0.15)   # BumpMesh's user-mask orange
ERASE = (0.60, 0.60, 0.60)   # BumpMesh's erase hover grey (0x999999)
HOVER = (1.0, 0.933, 0.0)    # BumpMesh's hover preview yellow (0xffee00), at 45 % opacity
BODY = (0.80, 0.80, 0.80)
RING = 64


class PaintView:
    def __init__(self, window):
        self.w = window
        self.view = None
        self._cbs = []
        self.ring = None
        self.overlay = None
        self.hover = None
        self.tool = paint.PaintTool(live.draft(), self._surface, self._pick, self._cursor, self._hover)

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
        for kind, fn in ((coin.SoMouseButtonEvent, self._button), (coin.SoLocation2Event, self._move)):
            self._cbs.append((kind.getClassTypeId(), v.addEventCallbackPivy(kind.getClassTypeId(), fn)))

    def _detach(self):
        self.tool.cancel()
        v, self.view = self.view, None
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

    def on_draft(self, ev):
        k = ev.get("kind")
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
        if self.tool.button(b, ev.getState() == coin.SoButtonEvent.DOWN, x, y, bool(ev.wasShiftDown())):
            node.setHandled()

    def _move(self, node):
        ev = node.getEvent()
        x, y = ev.getPosition().getValue()
        if self.tool.move(x, y, bool(ev.wasShiftDown())):
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

    def _pick(self, x, y):
        coin, rm, cam, vp = self._camera()
        snap = self.w.snapshot()
        if snap is None or cam is None:
            return None
        n_src = len(snap[1]) // 9
        hit = None
        cv = self.w.view
        c = self.w.controller
        fp = getattr(cv, "face_parent", None)
        if c is not None and c.showing == "cpu" and cv is not None and cv.cpu is not None and fp is not None:
            rp = coin.SoRayPickAction(vp)
            rp.setPoint(coin.SbVec2s(int(x), int(y)))
            rp.setRadius(2)
            rp.setPickAll(True)
            rp.apply(rm.getSceneGraph())
            for pp in rp.getPickedPointList():
                if not pp.getPath().containsNode(cv.faces):
                    continue
                d = pp.getDetail()
                if d is None or not d.isOfType(coin.SoFaceDetail.getClassTypeId()):
                    continue
                d = coin.cast(d, "SoFaceDetail")
                hit = paint.pick_hit(d.getFaceIndex(), pp.getPoint().getValue(), pp.getNormal().getValue(), fp, n_src)
                break
        else:
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
        vv = cam.getViewVolume(vp.getViewportAspectRatio())
        line = coin.SbLine()
        vv.projectPointToLine(coin.SbVec2f(x / max(w, 1), y / max(h, 1)), line)
        return line.getPosition().getValue(), line.getDirection().getValue()

    # -- drawing ----------------------------------------------------------------------------------
    def _graph(self):
        return self.view.getSceneGraph() if self.view is not None else None

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

    def _draw_overlay(self):
        """The active layer's paint on the body's tessellation, while the CPU preview is not shown."""
        g = self._graph()
        snap = self.w.snapshot()
        c = self.w.controller
        show = g is not None and snap is not None and not (c is not None and c.showing == "cpu")
        if not show:
            if self.overlay is not None:
                self.overlay.whichChild = -1
            return
        from pivy import coin

        if self.overlay is None:
            self.overlay = coin.SoSwitch()
            sep = coin.SoSeparator()
            po = coin.SoPolygonOffset()
            po.factor, po.units = -1.0, -1.0
            mb = coin.SoMaterialBinding()
            mb.value = coin.SoMaterialBinding.PER_VERTEX
            self._oc, self._om, self._of = coin.SoCoordinate3(), coin.SoMaterial(), coin.SoFaceSet()
            for n in (po, mb, self._oc, self._om, self._of):
                sep.addChild(n)
            self.overlay.addChild(sep)
            g.addChild(self.overlay)
        s = paint.surface_for(snap[1])
        k = self.tool.stroke   # mid-stroke: the stroke's own state, no replay per dab
        st = k["state"] if k is not None and k["s"] is s else paint.state_from_mask(s, live.draft().mask(), snap[2])
        P, a = paint.overlay(st)
        on = len(P) // 3
        if not on:
            self.overlay.whichChild = -1
            return
        a = a.reshape(-1, 1)
        col = np.asarray(BODY) * (1 - a) + np.asarray(PAINT) * a
        self._oc.point.setValues(0, len(P), P.tolist())
        self._oc.point.setNum(len(P))
        self._om.diffuseColor.setValues(0, len(col), col.tolist())
        self._om.diffuseColor.setNum(len(col))
        self._of.numVertices.setValues(0, on, [3] * on)
        self._of.numVertices.setNum(on)
        self.overlay.whichChild = 0
