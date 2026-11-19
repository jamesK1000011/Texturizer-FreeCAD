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
through a Qt signal, and every timer is the window's GuiQueue's
(guithread.py), made, stopped and deleted on the GUI thread only. A call
from another thread (a tool run as Agent Link's job) is handed to the GUI
thread (show_result, cancel_preview, open) or answered without the window
(ask_path, ask_save_options: None, so the tool writes to its default path)."""
import os

import numpy as np

from . import gizmoview, guithread, handlers, live, paint, paintview, panel, preview, quicklook, sidebar, timing
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


def _queue():
    """The window side's GuiQueue (guithread.py): the one owner of its
    timers and its hand-off to the GUI thread; made on the GUI thread the
    first time (None when asked first from another thread, or without Qt)."""
    w = _WINDOW
    q = getattr(w, "_q", None) if w is not None else None
    if q is not None and q.alive():
        return q
    if not guithread.on_gui_thread() or QtCore is None:
        return None
    try:
        import FreeCAD

        log = FreeCAD.Console.PrintLog
    except Exception:  # noqa: BLE001
        log = None
    q = guithread.GuiQueue(QtCore, log=log)
    if w is not None:
        w._q = q
    return q


def _post(fn):
    """fn on the GUI thread, from any thread: at once when already there
    with no queue, else queued."""
    q = _queue() if guithread.on_gui_thread() else getattr(_WINDOW, "_q", None)
    if q is not None:
        return q.post(fn)
    if guithread.on_gui_thread():
        fn()
        return True
    return False   # no window queue yet and not on the GUI thread: dropped, never run on a worker


def _schedule(seconds, fn):
    """A single-shot timer owned by the window's queue (GuiQueue.schedule):
    made, stopped and deleted on the GUI thread only; returns cancel()."""
    q = _queue() if guithread.on_gui_thread() else getattr(_WINDOW, "_q", None)
    if q is None:
        return lambda: None
    return q.schedule(seconds, fn)


def _gui():
    import FreeCADGui

    return FreeCADGui


def _active_view():
    """The active document's view, whichever document it is."""
    try:
        d = _gui().ActiveDocument
        return d.ActiveView if d is not None else None
    except Exception:  # noqa: BLE001
        return None


def _view():
    """The 3D view the preview, the rings and the brush may draw in: the
    active document's, and only while that is the draft's own document
    (live.in_front), else None. A preview made for one document's part once
    stayed in whatever document came to the front (the Tutor's TutorRest
    and BikeStem, window round 4)."""
    try:
        d = _gui().ActiveDocument
        if d is None or not live.in_front(getattr(d, "Document", None)):
            return None
        return d.ActiveView
    except Exception:  # noqa: BLE001
        return None


def _gui_doc():
    """The draft's own document's view side (FreeCADGui's document), or None."""
    doc = live.draft_doc()
    if doc is None:
        return None
    try:
        return _gui().getDocument(doc.Name)
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
            raise RuntimeError("no 3D view of the draft's document in front")
        if v is not self._view:
            self.detach()   # never left in another view's scene graph
            self._view = v
        return v.getSceneGraph()

    def nodes(self):
        """The preview's own nodes now in a scene graph: (view, [nodes])."""
        return self._view, [n for n in (self.cpu, getattr(self.ql, "root", None)) if n is not None]

    def detach(self):
        """The CPU node and the Quick look taken out of the scene graph they
        were added to (made again, on the draft's own view, when next
        shown). True when any was there."""
        v, nodes = self.nodes()
        if v is not None:
            for n in nodes:
                try:
                    v.getSceneGraph().removeChild(n)
                except Exception:  # noqa: BLE001 — a view closed with its document: its graph went with it
                    pass
        self._view, self.cpu, self.ql, self._ql_part, self._ql_key = None, None, None, None, None
        self.face_parent = None
        self._maps, self._sizes = {}, {}
        return bool(nodes)

    def hide(self):
        """The CPU node and the Quick look switched off where they are (in
        the draft's own view): kept, not taken out. Taking them out at the
        end of a sequence (62f0dff) left the draft's view empty from
        texture_apply on (window round 6, 0010-2002), while switching them
        off was what every earlier round did and showed. The nodes go only
        when the view itself goes (detach). True when any was there."""
        _v, nodes = self.nodes()
        for n in nodes:
            try:
                n.whichChild = -1   # SO_SWITCH_NONE
            except Exception:  # noqa: BLE001 — a view closed with its document
                pass
        return bool(nodes)

    def facts(self, view=None):
        """What the preview has in a view's scene graph now (a headless fact
        for texture_apply's answer): its nodes found there by name, and
        whether each is switched on. view: the active view by default."""
        v = view if view is not None else _active_view()
        out = {"own": self._view is not None and v is self._view}
        try:
            g = v.getSceneGraph() if v is not None else None
        except Exception:  # noqa: BLE001
            g = None
        if g is None:
            out["scene"] = None
            return out
        found = {}
        try:
            n = g.getNumChildren()
            kids = [g.getChild(i) for i in range(n)]
        except Exception:  # noqa: BLE001 — a stand-in graph
            kids = list(getattr(g, "children", None) or [])
            n = len(kids)
        for k in kids:
            try:
                name = k.getName() if hasattr(k, "getName") else getattr(k, "name", "")
                name = name.getString() if hasattr(name, "getString") else str(name)
            except Exception:  # noqa: BLE001
                name = ""
            if name in ("TexturizerPreview", "TexturizerQuickLook"):
                on = getattr(k, "whichChild", None)
                on = getattr(on, "getValue", lambda: on)()
                found[name] = found.get(name, []) + [int(on) != -1 if on is not None else None]
        out["scene"] = {"children": n, "previewNodes": found}
        return out

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
        """The draft's part in its own document (never an object of the same
        Name in the document in front: the Tutor's PointBox was hidden)."""
        part = live.draft().part
        doc = _gui_doc()
        if not part or doc is None:
            return None
        try:
            return doc.getObject(part)
        except Exception:  # noqa: BLE001
            return None

    def get_body_visible(self):
        vo = self._body()
        return bool(vo.Visibility) if vo is not None else True

    _results_hid = ()   # (document, name) of Textured results the open preview hid

    def set_results_hidden(self, on):
        """The draft part's shown Textured results hidden while the preview
        is open (on), shown again when it closes (only those it hid; one
        deleted since is skipped). View state, Texturizer's
        own change (linked._show): the source body is not shown for it.
        Returns the names changed. Never raises."""
        from . import linked

        done = []
        try:
            if on:
                doc = live.draft_doc()
                part = live.draft().part
                body = live.find(doc, part) if doc is not None and part else None
                if body is None:
                    return done
                hid = list(self._results_hid)
                for t in linked.results_of(body, doc):
                    if linked.shown(t):
                        linked._show(t, False)
                        hid.append((doc.Name, t.Name))
                        done.append(t.Name)
                self._results_hid = tuple(hid)
                return done
            hid, self._results_hid = self._results_hid, ()
            for doc_name, name in hid:
                doc = live.document_named(doc_name)
                t = doc.getObject(name) if doc is not None else None
                if t is not None and not linked.shown(t):
                    linked._show(t, True)
                    done.append(name)
        except Exception:  # noqa: BLE001 — a closing document: left as it is
            pass
        return done

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


# -- headless facts on what the 3D view can draw (live round 7: the view
# stayed empty after texture_apply with the result shown and the camera on it) --
# The fields read off each node when it has them: what can hide what comes
# after it (a switch, a draw style, a transparency, a light or clip plane
# switched, the depth buffer), as Coin's own text.
NODE_FIELDS = ("whichChild", "style", "transparency", "on", "test", "write", "function", "renderCaching",
               "lightModel", "override")


def node_facts(n):
    """{type, name, children, <field>: Coin's text} of one Coin node."""
    out = {}
    try:
        out["type"] = n.getTypeId().getName().getString()
    except Exception:  # noqa: BLE001 — a stand-in
        out["type"] = type(n).__name__
    try:
        nm = n.getName() if hasattr(n, "getName") else getattr(n, "name", "")
        nm = nm.getString() if hasattr(nm, "getString") else str(nm or "")
        if nm:
            out["name"] = nm
    except Exception:  # noqa: BLE001
        pass
    kids = _kids(n)
    if kids is not None:
        out["children"] = len(kids)
    for f in NODE_FIELDS:
        try:
            fld = n.getField(f) if hasattr(n, "getField") else None
            if fld is not None:
                out[f] = str(fld.get()).strip()[:60]
            elif hasattr(n, f) and not hasattr(n, "getField"):
                v = getattr(n, f)
                out[f] = str(getattr(v, "getValue", lambda: v)())[:60]
        except Exception:  # noqa: BLE001
            pass
    return out


def _kids(n):
    try:
        if hasattr(n, "getNumChildren"):
            return [n.getChild(i) for i in range(n.getNumChildren())]
    except Exception:  # noqa: BLE001 — not a group
        return None
    k = getattr(n, "children", None)
    return list(k) if isinstance(k, (list, tuple)) else None


def scene_walk(root, depth=2, cap=80):
    """The top depth levels of a scene graph, in traversal order: each node's
    node_facts with its path ("3", "3.1"), at most cap nodes (truncated: true
    past it). What sits ahead of the document's objects and hides them shows
    here."""
    out = []

    def walk(path, n, level):
        for i, k in enumerate(_kids(n) or ()):
            if len(out) >= cap:
                return
            f = node_facts(k)
            f["at"] = ".".join(str(x) for x in path + (i,))
            out.append(f)
            if level + 1 < depth:
                walk(path + (i,), k, level + 1)
    walk((), root, 0)
    total = len(_kids(root) or ())
    return {"nodes": out, "truncated": len(out) >= cap, "topChildren": total}


def camera_facts(view):
    """The active view's camera: type, position, near / far distances (an
    auto-clipping gone wrong clips everything though the centre projects
    inside), focal distance and height."""
    cam = view.getCameraNode()
    out = {"type": node_facts(cam).get("type")}
    for f in ("position", "orientation", "nearDistance", "farDistance", "focalDistance", "height", "heightAngle",
              "aspectRatio", "viewportMapping"):
        try:
            fld = cam.getField(f)
            if fld is not None:
                out[f] = str(fld.get()).strip()[:80]
        except Exception:  # noqa: BLE001
            pass
    return out


def view_provider_facts(o):
    """A document object's view side: DisplayMode, Visibility, Transparency,
    its facets / faces, its RootNode (children, and whether the active
    view's scene graph holds it) and its SwitchNode's whichChild (the
    display mode drawn: -1 draws nothing)."""
    vo = getattr(o, "ViewObject", None)
    if vo is None:
        return None
    out = {}
    for k in ("DisplayMode", "Visibility", "Transparency", "Lighting", "ShapeAppearance"):
        try:
            v = getattr(vo, k)
            out[k] = v if isinstance(v, (bool, int, float, str)) else str(v)[:80]
        except Exception:  # noqa: BLE001
            pass
    try:
        out["modes"] = list(vo.listDisplayModes())
    except Exception:  # noqa: BLE001
        pass
    m = getattr(o, "Mesh", None)
    if m is not None:
        out["facets"] = int(getattr(m, "CountFacets", 0) or 0)
    sh = getattr(o, "Shape", None)
    if sh is not None and m is None:
        try:
            out["faces"] = len(sh.Faces)
        except Exception:  # noqa: BLE001
            pass
    root = getattr(vo, "RootNode", None)
    if root is not None:
        out["root"] = node_facts(root)
        try:
            g = _active_view().getSceneGraph()
            out["rootInScene"] = int(g.findChild(root)) if hasattr(g, "findChild") else None
        except Exception:  # noqa: BLE001
            pass
    sw = getattr(vo, "SwitchNode", None)
    if sw is not None:
        out["switch"] = node_facts(sw)
    return out


def viewer_facts(view):
    """The viewer's whole scene (camera, lights, the document's root and
    anything put before it), two levels, and whether events go to the
    scene graph first."""
    vw = view.getViewer()
    out = {}
    try:
        out["redirectedToSceneGraph"] = bool(vw.isRedirectedToSceneGraph())
    except Exception:  # noqa: BLE001
        pass
    root = None
    try:
        root = vw.getSoRenderManager().getSceneGraph()
    except Exception:  # noqa: BLE001
        try:
            root = vw.getSceneGraph()
        except Exception:  # noqa: BLE001
            root = None
    if root is not None:
        out["root"] = node_facts(root)
        out["scene"] = scene_walk(root, depth=2, cap=40)
    return out


class Window:
    """live.GUI: the sidebar, the gallery and the preview of this window."""

    def __init__(self):
        self.sidebar = None
        self.controller = None
        self.view = None
        self._snap = None
        self._q = None   # the GuiQueue: made once on the GUI thread, kept for the window's life
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
        if not d.part or not live.in_front():
            return None   # another document in front: its objects are never the draft's part
        if self._frozen and self._snap is not None and self._snap[0] == d.part and self._snap_ok:
            return self._snap
        try:
            with timing.stage("snapshot (geometry check)"):
                pos, ft = live.mesh_for(d.part, doc=live.draft_doc())
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
        selected body (or the one body shown) into the draft first. From
        another thread it is handed to the GUI thread (the sidebar as it is
        now is returned)."""
        if not guithread.on_gui_thread():
            _post(lambda: self.open(load))
            return self.sidebar
        mw = _gui().getMainWindow()
        if not self._alive():
            d = live.draft()
            self.sidebar = sidebar.Sidebar(d, runner=runner, list_textures=live.list_textures, parent=mw)
            self.sidebar.visibilityChanged.connect(self._visibility)
            if self._unsub:
                self._unsub()
            self._unsub = d.subscribe(self._draft_event)
            old = self.controller
            if old is not None:
                # The sidebar was deleted by Qt and is made again: the old
                # preview stops (its worker, its timer) before a new one
                # takes its place, never left running beside it.
                old.close()
                old.wait(1.0)
            q = _queue()
            self.view = CoinView(self)
            self.controller = preview.PreviewController(live.engine_with(self._mesh_for), d, self.view,
                                                        quicklook_ok=self._detect(), post=_post, schedule=_schedule,
                                                        on_gui=guithread.on_gui_thread)
            self._q = q
            self.paint = paintview.PaintView(self)
            self.gizmo = gizmoview.GizmoView(self)
            self.sidebar.stamp_place = self.gizmo.arm_place
            self.sidebar.on_include_new = self._include_new
            self.sidebar.on_draft_include = self._draft_include
        self.textured_changed()
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
        return quicklook.detect(_active_view(), params, ver, log=log)["ok"]

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
        self._drop_nodes()

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
    def _draft_event(self, ev):
        """The draft's subscriber: the paint, rings and preview follow it on
        the GUI thread only."""
        if guithread.on_gui_thread():
            return self._on_draft(ev)
        _post(lambda: self._on_draft(ev))

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
            self._part_changed()
            self.textured_changed()   # an undo or redo that kept the part reruns only its stage (preview.stage_for)
        elif ev.get("key") in ("refineLength", "maxTriangles"):
            self._estimate()

    # -- the Textured object this draft made ------------------------------------------------
    def _textured(self):
        from . import linked

        doc, d = live._doc(), live.draft()
        o = live.find(doc, live.TARGET["object"])
        if o is None and d.part and doc is not None:
            o = next((x for x in doc.Objects if linked.is_linked(x) and getattr(x, "SourceBody", None) == d.part), None)
        return o

    def textured_changed(self, obj=None):
        """The sidebar's line for the Textured object (the observer calls this
        when its TextureState or NewFaces change)."""
        from . import linked

        if not self._alive():
            return
        o = self._textured()
        if obj is not None and o is not None and getattr(obj, "Name", None) != o.Name:
            return
        if o is None:
            self.sidebar.show_textured(None)
        else:
            self.sidebar.show_textured(linked.state(o) or linked.UP_TO_DATE, linked.new_faces(o), o.Label)

    def draft_follow_changed(self):
        """linked.DraftFollower moved the draft's masks: what it could not
        move and the faces left smooth, in the sidebar."""
        from . import linked

        f = linked.DraftFollower.current()
        if self._alive() and f is not None:
            self.sidebar.show_draft_follow([r["message"] for r in f.report], f.new_faces)

    def _draft_include(self):
        from . import linked

        f = linked.DraftFollower.current()
        if f is not None:
            f.include_new()
            self.draft_follow_changed()

    def _include_new(self):
        from . import linked

        o = self._textured()
        if o is not None:
            linked.include_new(o)
            self.textured_changed()

    _last_geometry = None

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
            elif self._last_geometry != (_part, id(pos)):
                c.request("subdivide")   # another part, or its geometry changed
            # else: the same part and mesh (a visibility restored by Undo, the
            # sidebar reopened): the preview's cache stands, nothing reruns
        self._last_geometry = (_part, id(pos))

    def _part_shown(self):
        """The draft's part counts as shown: its eye is on, or Texturizer
        hid it for its Textured result (linked.behind_result: the preview
        still stands in for it; only the person's hiding holds it back)."""
        o = self.view._body() if self.view is not None else None
        if o is None or live.visible(o):
            return True
        return self._behind_result()

    def _behind_result(self):
        from . import linked

        doc, part = live.draft_doc(), live.draft().part
        body = live.find(doc, part) if doc is not None and part else None
        return linked.behind_result(body, doc)

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
            if self.user_hidden or self._behind_result():
                return   # hidden for its Textured result: Texturizer's, not the person's
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
        """texture_preview's result, shown as the CPU preview (on the GUI
        thread: from another one it is handed there)."""
        if not guithread.on_gui_thread():
            _post(lambda: self.show_result(res))
            return
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

    def cancel_preview(self, wait=2.0):
        """Cancel the preview: its job stopped and its worker thread waited
        for (at most wait seconds), its debounce timer cancelled, its nodes
        taken out of the view. True when no worker runs any more. From
        another thread it is handed to the GUI thread (False: not waited
        for)."""
        c = self.controller
        if c is None:
            return True
        if not guithread.on_gui_thread():
            _post(lambda: self.cancel_preview(0))
            return False
        done = c.cancel(wait=wait)
        self._drop_nodes()
        return done

    # -- the preview belongs to its document --------------------------------------------------
    _aside = False   # stepped aside for another document: shown again when the draft's comes back

    def _drop_nodes(self, remove=False):
        """The preview's nodes switched off in the draft's own view (kept
        there: the CPU mesh shows again at once, stale-tinted, when the
        preview comes back). remove: taken out of the scene graph (its view
        or document is going); the controller then draws its CPU mesh afresh
        when next shown (its stage cache stays)."""
        if self.view is None:
            return False
        if not remove:
            return self.view.hide()
        c = self.controller
        if c is not None and c.has_cpu:
            c.has_cpu = False
            if c.dirty is None:
                c.dirty = "colours"   # the mesh again from the cache's last stage
        return self.view.detach()

    def step_aside(self):
        """Another document came to the front, or the draft's is closing:
        the preview closed (the part shown again in its own document as it
        was), its nodes, the rings and the brush taken out of the draft's
        view, the turntable stopped. True when a preview was showing."""
        c = self.controller
        was = c is not None and c.showing is not None
        if was:
            c.close()
        for t in (self.paint, self.gizmo):
            if t is not None:
                t._detach()
        dropped = self._drop_nodes()
        try:
            from . import turntable

            turntable.shared().stop()
        except Exception:  # noqa: BLE001
            pass
        self._aside = self._aside or was
        return was or dropped

    def document_changed(self, doc=None):
        """FreeCAD's active document changed (doc, the one now in front):
        another document steps the preview aside; the draft's own coming
        back shows it again when it was showing before."""
        if not guithread.on_gui_thread():
            _post(lambda: self.document_changed(doc))
            return
        if not live.in_front(doc):
            self.step_aside()
            return
        if self._aside:
            self._aside = False
            self._snap = None
            if self._alive() and live.draft().part:
                self._part_changed()
        for t in (self.gizmo, self.paint):
            if t is not None:
                t.sync()

    def document_closed(self, doc):
        """doc is being closed: when it held the draft's part, the preview
        and its nodes go, and the draft lets go of the part (posted: the
        document is mid-close)."""
        if not live.document_closed(doc):
            return False
        self.step_aside()
        self._drop_nodes(remove=True)   # its view goes with it: no reference kept to its graph
        self._aside = False
        self._snap, self._last_geometry = None, None
        c = self.controller
        if c is not None:
            c.cache.clear()
            c.dirty = "subdivide"
        d = live.draft()

        def let_go():
            if d.part and live.DRAFT_DOC["name"] == live.CLOSED:
                d.set_part(None)
        _post(let_go)
        return True

    def end_preview(self):
        """The tool sequence ended (texture_apply, texture_export): the
        preview closed and its nodes switched off in the draft's view (kept
        there, see CoinView.hide); the part shown or hidden as the preview
        found it (or as its Textured result wants it); the next draft tool
        (or the sidebar) opens it again. True when one was showing."""
        if not guithread.on_gui_thread():
            _post(self.end_preview)
            return None
        c = self.controller
        was = c is not None and c.showing is not None
        if was:
            c.close()
        self._aside = False
        self._drop_nodes()
        return was

    def build_preview(self, budget, show=True):
        """The CPU preview of the draft built now (texture_preview, and the
        draft tools while it shows): the worker does the maths, its results
        are drawn here as they come, for at most budget seconds
        (PreviewController.build_now). None when there is nothing to show
        it on (another document in front, the part hidden, no part)."""
        if not guithread.on_gui_thread():
            return None
        self.open()
        c = self.controller
        if self.user_hidden and self.snapshot() is not None and self._part_shown():
            self.user_hidden = False   # shown again, or hidden only for its Textured result
        if c is None or self.snapshot() is None or self.user_hidden:
            return None
        if show:
            c.forced = True
        return c.build_now(budget)

    def view_facts(self, doc=None, result=None, source=None):
        """Headless facts on what the 3D view can show now (texture_apply's
        and texture_preview { cancel }'s answers), so one live run tells why
        a view is empty: which document is in front and which is the
        draft's, the preview's state and its nodes in the active view's
        scene graph, the result's and the source's eyes, and whether the
        result's box centre is inside the view (the camera). Never raises;
        {"thread": "job"} off the GUI thread (the view is not read there)."""
        if not guithread.on_gui_thread():
            return {"thread": "job"}
        out = {}

        def step(key, fn):
            try:
                got = fn()
                if got is not None:
                    out[key] = got
            except Exception as e:  # noqa: BLE001 — facts only
                out[key + "Error"] = f"{type(e).__name__}: {e}"

        gd = getattr(_gui(), "ActiveDocument", None)
        step("activeDocument", lambda: getattr(getattr(gd, "Document", None), "Name", None))
        step("draftDocument", lambda: live.DRAFT_DOC["name"])
        step("draftInFront", lambda: bool(live.in_front()))
        c = self.controller
        out["previewShowing"] = c.showing if c is not None else None
        out["userHidden"] = bool(self.user_hidden)
        if self.view is not None:
            step("preview", lambda: self.view.facts())
            # The Textured results the open preview hid (a z-fight with
            # them, review 3): shown again when it closes.
            step("resultsHiddenForPreview", lambda: [n for _d, n in getattr(self.view, "_results_hid", ())])
        doc = doc or live.draft_doc()

        def eye(name):
            o = live.find(doc, name) if doc is not None and name else None
            if o is None:
                return None
            vo = getattr(o, "ViewObject", None)
            return {"name": o.Name, "visible": bool(getattr(vo, "Visibility", getattr(o, "Visibility", True))),
                    "shown": bool(live.visible(o))}

        step("result", lambda: eye(result))
        step("source", lambda: eye(source))
        step("resultInView", lambda: self._in_view(live.find(doc, result) if doc is not None and result else None))
        # Why a view with the result shown and in sight can still draw nothing
        # (live round 7): the result's view side, the camera's clipping, and
        # every node of the scene graph's top two levels (a switch, draw
        # style, transparency, clip plane or depth buffer ahead of the rest).
        av = _active_view()
        step("resultView", lambda: view_provider_facts(live.find(doc, result)) if doc is not None and result else None)
        step("camera", lambda: camera_facts(av) if av is not None else None)
        step("scene", lambda: scene_walk(av.getSceneGraph()) if av is not None else None)
        step("viewer", lambda: viewer_facts(av) if av is not None and hasattr(av, "getViewer") else None)
        return out

    @staticmethod
    def _in_view(o):
        """Whether o's box centre lands inside the active 3D view (the
        camera looks at it), with the pixel; None when it cannot be told."""
        if o is None:
            return None
        bb = None
        for attr in ("Mesh", "Shape"):
            g = getattr(o, attr, None)
            bb = getattr(g, "BoundBox", None) if g is not None else None
            if bb is not None:
                break
        if bb is None:
            return None
        gd = getattr(_gui(), "ActiveDocument", None)
        v = getattr(gd, "ActiveView", None) if gd is not None else None
        if v is None or not hasattr(v, "getPointOnViewport") and not hasattr(v, "getPointOnScreen"):
            return None
        import FreeCAD

        p = FreeCAD.Vector(bb.Center.x, bb.Center.y, bb.Center.z)
        x, y = (v.getPointOnViewport if hasattr(v, "getPointOnViewport") else v.getPointOnScreen)(p)[:2]
        w, h = v.getSize()[:2]
        return {"inside": bool(0 <= x <= w and 0 <= y <= h), "pixel": [int(x), int(y)], "size": [int(w), int(h)],
                "diagonal": round(float(getattr(bb, "DiagonalLength", 0.0)), 3)}

    def preview_showing(self):
        """What the preview shows now: "cpu", "quicklook", "body" or None."""
        c = self.controller
        return c.showing if c is not None else None

    def pending_timers(self):
        """Timers of the window's queue not yet fired or cancelled."""
        q = self._q
        return q.pending() if q is not None else 0

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
        st.setToolTip("The settings are always in the project file.")
        model = QtWidgets.QCheckBox("Model (STL)")
        model.setChecked(True)
        model.setToolTip("Put the model (STL) in the project file.")
        tex = QtWidgets.QCheckBox("Custom texture")
        tex.setChecked(True)
        tex.setToolTip("Put your custom texture in the project file.")
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
        _queue()   # made here, on the GUI thread, before any worker can ask for it
        from . import linked

        # Textured objects rerun on a worker when their body changes, written
        # back here. later's timers are the queue's (a timer only the
        # returned cancel held was deleted at once and never fired).
        linked.Reruns.install(preview._thread, _post, later=lambda fn: _schedule(0.25, fn))
        from . import docdraft

        # Undo's signal can come before its last property: reload on the next
        # turn (a queued call: a single-shot Qt timer asked for on a worker
        # thread was made there, where no event loop ever runs it).
        docdraft.POST[0] = _post
        try:
            import FreeCAD

            FreeCAD.addDocumentObserver(handlers.Observer())
        except Exception:  # noqa: BLE001
            pass
    return _WINDOW

