# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer. The preview
# tint (teal textured, orange masked) and the Quick look follow BumpMesh's
# js/previewMaterial.js; the Save / Load Project dialogs its index.html.
"""The window side of Texturizer (live.GUI): the Settings sidebar docked on
the right (or where PanelLayout puts it) with the Texture Gallery inside it, both on the one shared draft;
the live preview (PreviewController on BumpMesh's engine) drawn in the 3D view
as Coin nodes (the CPU-displaced mesh and the shader Quick look); file
dialogs; the selection. Every sidebar button runs through panel.run: Agent
Link's runner when hosted (so it lands in Activity), Texturizer's own alone.

Only the GUI thread touches Coin or Qt: the worker's results come back
through a Qt signal (Poster)."""
import os

import numpy as np

from . import gizmoview, handlers, live, paint, paintview, panel, preview, quicklook, sidebar, timing
from .qt import QtCore, QtWidgets

PARAMS = "User parameter:BaseApp/Preferences/Mod/Texturizer"
WORKBENCH = "TexturizerWorkbench"
FILTERS = {"stl": "STL (*.stl)", "3mf": "3MF (*.3mf)", "bumpmesh": "BumpMesh project (*.bumpmesh)",
           "image": "Images (*.png *.jpg *.jpeg *.webp)"}
ORANGE = (0.85, 0.40, 0.15)
STALE = (0.55, 0.57, 0.59)


def runner(tool, args, quiet=False):
    """The sidebar's runner: panel.run picks Agent Link's or the local one at
    call time (the hosted tab may be built after the sidebar)."""
    return panel.run(tool, args, quiet=quiet)


def _poster():
    """post(fn): fn on the GUI thread, from any thread (a queued Qt signal)."""

    class Poster(QtCore.QObject):
        fire = QtCore.Signal(object)

        def __init__(self):
            super().__init__()
            self.fire.connect(lambda fn: fn())

    p = Poster()
    return p, p.fire.emit


def _schedule(seconds, fn):
    t = QtCore.QTimer()
    t.setSingleShot(True)
    t.timeout.connect(fn)
    t.start(int(seconds * 1000))
    return t.stop


def _gui():
    import FreeCADGui

    return FreeCADGui


def _view():
    try:
        d = _gui().ActiveDocument
        return d.ActiveView if d is not None else None
    except Exception:  # noqa: BLE001
        return None


# -- the Quick look's mesh (pure) -------------------------------------------------------
def quicklook_mesh(positions, face_triangles):
    """The part's own tessellation, indexed within each B-rep face (so the
    masks stay sharp and normals smooth inside a face): (points (N, 3),
    triangles (M, 3), normals (N, 3), owner face per triangle)."""
    p = np.asarray(positions, np.float64).reshape(-1, 3, 3)
    n = len(p)
    owner = np.full(n, -1, np.int64)
    for k, (_name, tris) in enumerate(sorted((face_triangles or {}).items())):
        owner[np.asarray(tris, np.int64)] = k
    corners = p.reshape(-1, 3)
    keys = np.column_stack([np.repeat(owner, 3), np.round(corners * 1e5).astype(np.int64)])
    _u, first, inv = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    pts = corners[first]
    tri = inv.reshape(-1, 3)
    fn = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    nrm = np.zeros_like(pts)
    for c in range(3):
        np.add.at(nrm, tri[:, c], fn)
    ln = np.linalg.norm(nrm, axis=1)
    nrm = nrm / np.where(ln == 0, 1, ln)[:, None]
    return pts, tri, nrm


def quicklook_split(positions, face_triangles, layers):
    """When the Precision brush split triangles in one of layers, the Quick
    look draws the tree's leaves, as BumpMesh draws its flattened PaintTree:
    a band finer than a source triangle cannot show on the source's corners.
    (positions, face_triangles on them, layers with per-corner paint), or None."""
    from .engine import _resolve_masks

    got = paint.flatten_masks(positions, _resolve_masks([dict(L, visible=True) for L in layers], face_triangles))
    if got is None:
        return None
    pos, out, parent = got
    ft = {f: np.nonzero(np.isin(parent, np.asarray(ts, np.int64)))[0].tolist() for f, ts in (face_triangles or {}).items()}
    return pos, ft, out


def layer_weights(n_tri, tri, n_pts, layers, face_triangles):
    """(N, 4) per-vertex weights, one column per layer slot (0..1): 1 where
    the layer's mask lets the texture through (Exclude: not masked; Include
    Only: masked). A mask may be per-corner paint ({mode, paint}, quicklook_split)."""
    W = np.zeros((n_pts, 4), np.float32)
    for k, L in enumerate(layers[:4]):
        m = L.get("mask") or {}
        include = L.get("includeOnly")
        if m.get("paint") is not None:
            p = np.asarray(m["paint"], np.float32).reshape(-1)
            include = m.get("mode") == "include"
        else:
            hit = np.zeros(n_tri, bool)
            idx = [int(t) for t in m.get("triangles") or []]
            for f in m.get("faces") or []:
                idx.extend(int(t) for t in (face_triangles or {}).get(f, []))
            if idx:
                hit[np.asarray(idx, np.int64)] = True
            p = np.repeat(hit, 3).astype(np.float32)
            soft = m.get("soft")
            if soft:  # the soft brush's coverage per corner
                sc = np.asarray(soft["corners"], np.int64)
                ok = sc < n_tri * 3
                np.maximum.at(p, sc[ok], np.asarray(soft["values"], np.float32)[ok])
        on = p if include else 1 - p
        col = np.zeros(n_pts, np.float32)
        np.maximum.at(col, tri.reshape(-1), on)
        W[:, k] = col
    return W


class CoinView:
    """The preview's view (preview.PreviewController's view object): the CPU
    node and the Quick look in the active 3D view's scene graph."""

    def __init__(self, window):
        self.w = window
        self.cpu = None
        self.face_parent = None  # the CPU preview's source triangle per drawn face (the brush's pick)
        self.ql = None
        self._ql_part = self._ql_key = None
        self._view = None
        self._maps, self._sizes = {}, {}

    # -- scene ---------------------------------------------------------------------------
    def _graph(self):
        v = _view()
        if v is None:
            raise RuntimeError("no 3D view")
        if v is not self._view:
            self._view, self.cpu, self.ql, self._ql_part = v, None, None, None
        return v.getSceneGraph()

    def _cpu(self):
        if self.cpu is None:
            from pivy import coin

            root = coin.SoSwitch()
            root.setName("TexturizerPreview")
            sep = coin.SoSeparator()
            root.addChild(sep)
            self.coords, self.normals, self.material = coin.SoCoordinate3(), coin.SoNormal(), coin.SoMaterial()
            nb, mb = coin.SoNormalBinding(), coin.SoMaterialBinding()
            nb.value = coin.SoNormalBinding.PER_VERTEX_INDEXED
            mb.value = coin.SoMaterialBinding.PER_VERTEX_INDEXED
            self.tint = coin.SoSwitch()
            tb, tc = coin.SoMaterialBinding(), coin.SoBaseColor()
            tb.value = coin.SoMaterialBinding.OVERALL
            tc.rgb.setValue(*STALE)
            self.tint.addChild(tb)
            self.tint.addChild(tc)
            self.tint.whichChild = -1
            self.faces = coin.SoIndexedFaceSet()
            pre, post = _render_probe(coin, "render CPU preview")
            for n in (pre, self.coords, self.normals, nb, mb, self.material, self.tint, self.faces, post):
                if n is not None:
                    sep.addChild(n)
            root.whichChild = -1
            self._graph().addChild(root)
            self.cpu = root
        return self.cpu

    # -- the CPU-displaced mesh --------------------------------------------------------------
    def show_cpu(self, mesh):
        self._graph()
        self._cpu()
        V = np.asarray(mesh["vertices"], float).reshape(-1, 3)
        F = np.asarray(mesh["faces"], np.int64).reshape(-1, 3)
        with timing.stage(f"show_cpu coords ({len(V)} points)"):
            self.coords.point.setValues(0, len(V), V.tolist())
            self.coords.point.setNum(len(V))
        with timing.stage("show_cpu normals"):
            N = np.asarray(mesh["normals"], float).reshape(-1, 3)
            self.normals.vector.setValues(0, len(N), N.tolist())
            self.normals.vector.setNum(len(N))
        with timing.stage("show_cpu colours"):
            C = np.asarray(mesh["colours"], float).reshape(-1, 3)
            self.material.diffuseColor.setValues(0, len(C), C.tolist())
            self.material.diffuseColor.setNum(len(C))
        with timing.stage(f"show_cpu faces ({len(F)} triangles)"):
            idx = np.hstack([F, -np.ones((len(F), 1), np.int64)]).ravel()
            self.faces.coordIndex.setValues(0, len(idx), idx.tolist())
            self.faces.coordIndex.setNum(len(idx))
        self.face_parent = mesh.get("face_parent")
        with timing.stage("show_cpu sidebar + overlay (mesh_shown)"):
            self.w.mesh_shown(len(F))

    def set_cpu_visible(self, on):
        if on or self.cpu is not None:
            self._cpu().whichChild = -3 if on else -1

    def set_cpu_stale(self, on):
        if self.cpu is not None:
            self.tint.whichChild = -3 if on else -1

    # -- the Quick look ---------------------------------------------------------------------
    def update_quicklook(self, stage):
        try:
            with timing.stage(f"quicklook {stage}"):
                self._quicklook(stage)
        except Exception as e:  # noqa: BLE001 — the shader falls back, never raises
            c = self.w.controller
            if c is not None:
                c.quicklook_failed(f"Quick look off: {e}")

    def _quicklook(self, stage):
        from .engine import library
        from .engine.maps import prepare_map

        g = self._graph()
        d = live.draft()
        snap = self.w.snapshot()
        if snap is None:
            return
        part, pos, ft = snap
        if self.ql is None:
            self.ql = quicklook.QuickLook()
            g.addChild(self.ql.root)
        if self._ql_part != (part, id(pos)):
            self._ql_part, self._ql_key, self._maps = (part, id(pos)), None, {}
        layers = d.layers
        slots = quicklook.layer_slots(layers, d.active)
        shown = [layers[i] for i, _ in slots]
        split = any((L.get("mask") or {}).get("split") for L in shown)
        key = repr([(L.get("mask"), L.get("includeOnly")) for L in shown]) if split else None
        if self._ql_key != (key,):   # the mesh: the part's, or the Precision brush's leaves
            got = quicklook_split(pos, ft, shown) if split else None
            pts, tri, nrm = quicklook_mesh(*(got[:2] if got else (pos, ft)))
            self._ql = (pts, tri, nrm, got[2] if got else None)
            self.ql.set_mesh(pts, tri, nrm)
            self._ql_key = (key,)
        pts, tri, nrm, flat = self._ql
        sizes = {}
        for k, (i, _shown) in enumerate(slots):
            L = layers[i]
            s = L["settings"]
            key = (L["texture"], s.get("textureSmoothing") or 0, bool(s.get("invertTexture")))
            if self._maps.get(k) != key:
                img = prepare_map(library.load(L["texture"]), key[1], key[2])
                grey = np.ascontiguousarray(img.data[::-1, :, 0])
                self.ql.set_image(k, grey.tobytes(), img.width, img.height)
                self._maps[k] = key
                self._sizes[k] = (img.width, img.height)
            sizes[i] = self._sizes.get(k)
        self.ql.set_weights(layer_weights(len(tri), tri, len(pts), flat or shown, None if flat else ft))
        rot = d.frame.get("rotation") or [0, 0, 0]
        frame = {"rotation": rot, "bounds": quicklook.frame_bounds(pts, {"rotation": rot})}
        self.ql.set_uniforms(quicklook.uniforms_from_layers(layers, frame, d.active, sizes))

    def set_quicklook_visible(self, on):
        if self.ql is not None:
            self.ql.show(on)

    # -- the body and the sidebar --------------------------------------------------------------
    def _body(self):
        snap = self.w.snapshot()
        doc = getattr(_gui(), "ActiveDocument", None)
        if snap is None or doc is None:
            return None
        try:
            return doc.getObject(snap[0])
        except Exception:  # noqa: BLE001
            return None

    def get_body_visible(self):
        vo = self._body()
        return bool(vo.Visibility) if vo is not None else True

    def set_body_visible(self, on):
        vo = self._body()
        if vo is not None and bool(vo.Visibility) != bool(on):
            self.w._own_vis += 1   # the preview's own change: not the person hiding the part
            try:
                with timing.stage("body visibility"):
                    vo.Visibility = bool(on)
            finally:
                self.w._own_vis -= 1

    def progress(self, stage, fraction):
        sb = self.w.sidebar
        if sb is not None:
            sb.progress(stage, fraction)

    def note(self, text):
        if self.w.sidebar is not None:
            self.w.sidebar.say(text)

    def lock_preview(self, on, text):
        sb = self.w.sidebar
        b = sb.binds.get("useDisplacement") if sb is not None else None
        if b is not None:
            b.set(True)
            for w in b.widgets:
                w.setEnabled(not on)
        self.note(text)

    def error(self, text):
        self.note(text)
        try:
            import FreeCAD

            FreeCAD.Console.PrintWarning(f"Texturizer: {text}\n")
        except ImportError:
            pass


def _render_probe(coin, name):
    """Two SoCallback nodes around a group, timing its GL render with the
    probe on (timing.py), or (None, None) when pivy can't wrap a callback.
    The time is the CPU side of the draw call (the GPU may finish later).
    Added only when the probe is on as the preview node is made (a callback
    node stops Coin caching the group), so turn the probe on before opening
    the sidebar to see render times."""
    import time

    if not timing.enabled():
        return None, None

    t = [None]

    def _render(a):
        try:   # pivy calls (userdata, action); the action is the last either way
            return a[-1].isOfType(coin.SoGLRenderAction.getClassTypeId())
        except Exception:  # noqa: BLE001
            return False

    def start(*a):
        if timing.enabled() and _render(a):
            t[0] = time.perf_counter()

    def end(*a):
        if t[0] is not None and _render(a):
            timing.report(name, (time.perf_counter() - t[0]) * 1000.0)
            t[0] = None

    try:
        a, b = coin.SoCallback(), coin.SoCallback()
        a.setCallback(start)
        b.setCallback(end)
        return a, b
    except Exception:  # noqa: BLE001 - an old pivy: no render times
        return None, None


class Window:
    """live.GUI: the sidebar, the gallery and the preview of this window."""

    def __init__(self):
        self.sidebar = None
        self.controller = None
        self.view = None
        self._snap = None
        self._poster = None
        self._unsub = None
        self.paint = None
        self.gizmo = None
        self._frozen = 0
        self._drags = set()
        self._own_vis = 0
        self.user_hidden = False   # the person hid the draft's part: no preview, rings or brush on it

    # -- the part's mesh, read on the GUI thread only ------------------------------------------
    def snapshot(self):
        """(part, positions, face_triangles) of the draft's part, refreshed
        here on the GUI thread; the worker reads only this tuple. Inside
        frozen() (one view event) the first answer stands: the part's
        geometry check (its volume, area and box) runs once an event, not
        once for each of the tool's questions."""
        d = live.draft()
        if not d.part:
            return None
        if self._frozen and self._snap is not None and self._snap[0] == d.part and self._snap_ok:
            return self._snap
        try:
            with timing.stage("snapshot (geometry check)"):
                pos, ft = live.mesh_for(d.part)
        except LookupError:
            self._snap_ok = False
            return None
        self._snap_ok = True
        if self._snap is None or self._snap[0] != d.part or self._snap[1] is not pos:
            self._snap = (d.part, pos, ft)
        return self._snap

    _snap_ok = False

    def frozen(self):
        """with window.frozen(): one view event's snapshot() calls share one geometry check."""
        w = self

        class _F:
            def __enter__(self):
                if not w._frozen:
                    w._snap_ok = False   # the event's first question checks the geometry
                w._frozen += 1

            def __exit__(self, *exc):
                w._frozen -= 1
                return False

        return _F()

    def drag(self, who, on):
        """A drag in the 3D view began or ended (who: "gizmo" or "paint"):
        the preview holds its CPU rebuild while any drag is on (PreviewController.hold)."""
        (self._drags.add if on else self._drags.discard)(who)
        if self.controller is not None:
            self.controller.hold(bool(self._drags))

    def _mesh_for(self, part):
        snap = self._snap
        if snap is None or snap[0] != part:
            raise LookupError(f"part {part!r}: not loaded in the sidebar")
        return snap[1], snap[2]

    # -- opening ---------------------------------------------------------------------------
    def _alive(self):
        if self.sidebar is None:
            return False
        try:
            self.sidebar.objectName()
            return True
        except RuntimeError:  # deleted by Qt
            self.sidebar = None
            return False

    def open(self, load=False):
        """The sidebar where the panel layout setting puts it; load: the
        selected body (or the one body shown) into the draft first."""
        mw = _gui().getMainWindow()
        if not self._alive():
            d = live.draft()
            self.sidebar = sidebar.Sidebar(d, runner=runner, list_textures=live.list_textures, parent=mw)
            self.sidebar.visibilityChanged.connect(self._visibility)
            if self._unsub:
                self._unsub()
            self._unsub = d.subscribe(self._on_draft)
            self._poster, post = _poster()
            self.view = CoinView(self)
            self.controller = preview.PreviewController(live.engine_with(self._mesh_for), d, self.view,
                                                        quicklook_ok=self._detect(), post=post, schedule=_schedule)
            self.paint = paintview.PaintView(self)
            self.gizmo = gizmoview.GizmoView(self)
            self.sidebar.stamp_place = self.gizmo.arm_place
        # Where the panel layout setting puts it (layout.py): right by
        # default, at most 360 px, the window kept at its size; the gallery
        # slides out inside it from its Texture Gallery button.
        from . import layout

        p = layout.placer()
        p.show(sidebar.NAME, self.sidebar, WORKBENCH, sidebar.TITLE)
        p.reveal(sidebar.NAME)
        if load:
            self._load_selection()
        self._part_changed()
        return self.sidebar

    def _detect(self):
        try:
            import FreeCAD

            params = FreeCAD.ParamGet(PARAMS)
            ver = ".".join(FreeCAD.Version()[:3])
            log = FreeCAD.Console.PrintLog
        except Exception:  # noqa: BLE001
            params, ver, log = None, "", None
        return quicklook.detect(_view(), params, ver, log=log)["ok"]

    def _load_selection(self):
        d = live.draft()
        if self.selected_object():
            runner("texture_draft", {"load": "selection"}, quiet=True)  # the window's own, not a click
        elif d.part is None:
            ok, _out = runner("texture_draft", {}, quiet=True)  # the one body shown, if there is one
            if not ok:
                self.sidebar.say("Select a body, then Load Model…")

    def leave(self):
        """The workbench left: the preview closed and the body shown again,
        the brush and rings let go of the view. In a Combo View tab the
        sidebar's own hiding is not seen (_visibility), so the workbench
        says it."""
        c = self.controller
        if c is not None:
            c.close()
        for t in (self.paint, self.gizmo):
            if t is not None:
                t._detach()

    def _visibility(self, visible):
        c = self.controller
        if c is None or self.sidebar is None:
            return
        if self.sidebar.widget() is None:
            # Its content is a Combo View tab or the slide-out (layout.py):
            # the empty dock's own showing and hiding says nothing.
            return
        if not visible and self.sidebar.isHidden():
            c.close()
            if self.paint is not None:
                self.paint._detach()
            if self.gizmo is not None:
                self.gizmo._detach()
        elif visible and c.showing is None and live.draft().part:
            self._part_changed()

    # -- following the draft -------------------------------------------------------------------
    def _on_draft(self, ev):
        if ev.get("kind") in ("part", "load", "reset"):
            d = live.draft()
            if d.part:
                live.remember_part(live._doc(), d.part)
            self.user_hidden = False
        if self.paint is not None:
            self.paint.on_draft(ev)
        if self.gizmo is not None:
            self.gizmo.on_draft(ev)
        moved = ev.get("kind") in ("undo", "redo") and isinstance(ev.get("value"), dict) and ev["value"].get("part")
        if ev.get("kind") in ("part", "load", "reset") or moved:
            self._part_changed()   # an undo or redo that kept the part reruns only its stage (preview.stage_for)
        elif ev.get("key") in ("refineLength", "maxTriangles"):
            self._estimate()

    def _part_changed(self):
        sb = self.sidebar
        snap = self.snapshot()
        if sb is None or snap is None:
            return
        _part, pos, _ft = snap
        p = np.asarray(pos, float).reshape(-1, 3)
        size = (p.max(0) - p.min(0)).tolist() if len(p) else [0, 0, 0]
        sb.set_mesh_info(len(pos) // 9, size, len(pos) * 4 / 2 ** 20)
        self._estimate()
        c = self.controller
        if c is not None and c.showing is None and not self._part_shown():
            self.user_hidden = True   # a hidden part: its preview and rings wait until it is shown
        if self.gizmo is not None:
            self.gizmo.sync()
        if self.paint is not None:
            self.paint.sync()
        if c is not None and not self.user_hidden:
            if c.showing is None:
                c.open()
            else:
                c.request("subdivide")

    def _part_shown(self):
        o = self.view._body() if self.view is not None else None
        return True if o is None else live.visible(o)

    def part_visibility(self, obj):
        """The document observer: an object's Visibility changed. The person
        hiding the draft's part hides its preview, rings and brush with it;
        showing it again brings them back. The preview's own hiding of the
        body (it stands in for it) is not the person's."""
        d = live.draft()
        if self._own_vis or not d.part or getattr(obj, "Name", None) != d.part:
            return
        c = self.controller
        if not live.visible(obj):
            if self.user_hidden:
                return
            self.user_hidden = True
            if c is not None and c.showing is not None:
                c._body = False   # closing leaves it hidden, as the person left it
                c.close()
            for t in (self.gizmo, self.paint):
                if t is not None:
                    t.sync()
        elif self.user_hidden:
            self.user_hidden = False
            self._part_changed()

    def _estimate(self):
        from . import engine

        snap = self.snapshot()
        if self.sidebar is None or snap is None:
            return
        try:
            est = engine.estimate(snap[1], handlers.run_settings(live.draft(), snap[2]))
        except Exception:  # noqa: BLE001
            return
        fm = sidebar.format_m
        self.sidebar.set_estimate(f"{fm(len(snap[1]) // 9)} → {fm(est['subdividedTriangles'])} → {fm(est['outputTriangles'])} "
                                  f"triangles · ~{est['memoryBytes'] / 2 ** 20:.0f} MB")

    def mesh_shown(self, n):
        if self.sidebar is not None:
            self.sidebar.say(f"Preview: {sidebar.format_m(n)} triangles")
        if self.paint is not None:   # the CPU preview now tints the paint: the overlay steps aside
            self.paint.stale = False
            self.paint._draw_overlay()

    # -- what the tools ask of the window -----------------------------------------------------------
    def press(self, key):
        """A toolbar button: the sidebar's own button of the same name."""
        sb = self.open()
        return sb.press(key)

    def show_result(self, res):
        """texture_preview's result, shown as the CPU preview."""
        self.open()
        c = self.controller
        if c is not None:
            c.cache.update({k: v for k, v in (res.get("stages") or {}).items() if k in preview.STAGES})
            c.dirty, c.has_cpu = None, True
            if c.showing is None:
                c.open()
        self.view.show_cpu(res["mesh"])
        if c is not None:
            c._show("cpu")

    def cancel_preview(self):
        if self.controller is not None:
            self.controller.cancel()

    def selected_object(self):
        try:
            for o in _gui().Selection.getSelection():
                if (getattr(getattr(o, "Shape", None), "Faces", None) or getattr(o, "Mesh", None) is not None) \
                        and live.visible(o):   # never a hidden object
                    return o.Name
        except Exception:  # noqa: BLE001
            pass
        return None

    def selected_faces(self):
        try:
            for s in _gui().Selection.getSelectionEx():
                faces = [n for n in s.SubElementNames if n.startswith("Face")]
                if faces:
                    return s.Object.Name, faces
        except Exception:  # noqa: BLE001
            pass
        return None

    def picked_point(self):
        """(point, normal) of the last click on a face."""
        try:
            for s in _gui().Selection.getSelectionEx():
                for name, pt in zip(s.SubElementNames, s.PickedPoints):
                    if name.startswith("Face"):
                        f = s.Object.Shape.getElement(name)
                        u, v = f.Surface.parameter(pt)
                        n = f.normalAt(u, v)
                        return (pt.x, pt.y, pt.z), (n.x, n.y, n.z)
        except Exception:  # noqa: BLE001
            pass
        return None

    def ask_path(self, kind, save=False, default=""):
        mw = _gui().getMainWindow()
        title = {"bumpmesh": "Save Project" if save else "Load Project", "image": "Upload Custom Map",
                 "stl": "Export STL", "3mf": "Export 3MF"}.get(kind, "Texturizer")
        start = default or os.path.expanduser("~")
        fn = QtWidgets.QFileDialog.getSaveFileName if save else QtWidgets.QFileDialog.getOpenFileName
        r = fn(mw, title, start, FILTERS.get(kind, ""))
        path = r[0] if isinstance(r, tuple) else r
        return path or None

    def ask_load_mode(self, has_model):
        """BumpMesh's Load Project choice: Model + settings, or Settings only."""
        if not has_model:
            return "settings"
        box = QtWidgets.QMessageBox(_gui().getMainWindow())
        box.setWindowTitle("Load Project")
        box.setText("Load the project's model with its settings, or the settings only?")
        both = box.addButton("Model + Settings", QtWidgets.QMessageBox.AcceptRole)
        box.addButton("Settings Only", QtWidgets.QMessageBox.AcceptRole)
        cancel = box.addButton(QtWidgets.QMessageBox.Cancel)
        box.exec_() if hasattr(box, "exec_") else box.exec()
        if box.clickedButton() is cancel:
            return None
        return "all" if box.clickedButton() is both else "settings"

    def ask_save_options(self):
        """BumpMesh's Save Project dialog: Settings (always), Model (STL),
        Custom texture."""
        dlg = QtWidgets.QDialog(_gui().getMainWindow())
        dlg.setWindowTitle("Save Project")
        lay = QtWidgets.QVBoxLayout(dlg)
        st = QtWidgets.QCheckBox("Settings")
        st.setChecked(True)
        st.setEnabled(False)
        model = QtWidgets.QCheckBox("Model (STL)")
        model.setChecked(True)
        tex = QtWidgets.QCheckBox("Custom texture")
        tex.setChecked(True)
        for w in (st, model, tex):
            lay.addWidget(w)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.setLayout(lay)
        ok = (dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec())
        return {"model": model.isChecked(), "texture": tex.isChecked()} if ok else None


_WINDOW = None


def get():
    """The window side, made once and set as live.GUI."""
    global _WINDOW
    if _WINDOW is None:
        _WINDOW = Window()
        live.GUI = _WINDOW
        from . import docdraft

        # Undo's signal can come before its last property: reload on the next turn.
        docdraft.POST[0] = lambda fn: QtCore.QTimer.singleShot(0, fn)
        try:
            import FreeCAD

            FreeCAD.addDocumentObserver(handlers.Observer())
        except Exception:  # noqa: BLE001
            pass
    return _WINDOW

