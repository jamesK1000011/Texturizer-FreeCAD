# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The Textured object as a FreeCAD feature that follows its body
(docs/linked-texture.md):

    TexturedFeature   the Mesh::FeaturePython proxy: execute() reruns the
                      stored draft when Source's geometry changed, in the
                      background with the window, in place headless
    anchors(...)      a draft's masks as geometry (mapped face names, sample
                      points, the painted tessellation)
    remap(...)        those anchors on a new tessellation: the draft with its
                      masks moved, and what could not be moved (never guessed)
    convert(doc)      old documents' Mesh::Feature Textured objects (a string
                      SourceBody) as linked features
    Reruns            the background queue: a newer change makes an older run
                      stale

No Qt import here; FreeCAD only inside the functions that need it.
"""
import base64
import json
import types

import numpy as np

from . import live

KIND = "Mesh::FeaturePython"
GROUP = "Texturizer"
UP_TO_DATE, UPDATING, REPICK, NO_SOURCE = "up to date", "updating…", "mask needs re-pick", "source missing"
SAMPLES = 24          # sample points kept per masked face
NORMAL_COS = 0.8      # a sample lands on a triangle facing within ~37° of it
# PropertyLinkGlobal: the body may sit in another container (an Assembly, an
# App::Part) than the Textured object; a plain PropertyLink warns on every
# recompute that it goes out of its allowed scope.
LINK_KIND = "App::PropertyLinkGlobal"
LINK_PROPS = (("Source", LINK_KIND, "The body it textures: a change to it textures it again"),
              ("MaskAnchors", "App::PropertyString", "The masks as geometry (JSON): how they follow the body"),
              ("TextureState", "App::PropertyString", "up to date, updating…, or what mask needs re-picking"),
              ("NewFaces", "App::PropertyString", "Faces made after the texture was set up (JSON): left smooth until included"))


# -- geometry ------------------------------------------------------------------------
def _tris(pos):
    return np.asarray(pos, np.float64).reshape(-1, 3, 3)


def _normals(T):
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    ln = np.linalg.norm(n, axis=1)
    return n / np.where(ln > 0, ln, 1)[:, None]


def tolerance(T):
    """How far a point may sit from a surface and still be on it: twice
    the tessellation's chord error, at least a ten-thousandth of the part."""
    if not len(T):
        return 4 * live.TOLERANCE
    p = T.reshape(-1, 3)
    diag = float(np.linalg.norm(p.max(0) - p.min(0)))
    return max(4 * live.TOLERANCE, 1e-4 * diag)


from .engine.untangle import closest_on_triangles  # noqa: E402 (shared with the engine)


def _pack(a):
    return base64.b64encode(np.ascontiguousarray(a, np.float32).tobytes()).decode("ascii")


def _unpack(s, cols):
    return np.frombuffer(base64.b64decode(s), np.float32).astype(np.float64).reshape(-1, cols)


# -- mapped (topological naming) names -------------------------------------------------------
def mapped_name(shape, name):
    """FreeCAD 1.x's mapped name of an element ("Face6"), or None."""
    try:
        rev = shape.ElementReverseMap
    except Exception:  # noqa: BLE001 — older FreeCAD, a mesh, a stand-in
        return None
    got = rev.get(name) if isinstance(rev, dict) else None
    if isinstance(got, (list, tuple)):
        got = got[0] if got else None
    return got or None


def indexed_name(shape, mapped):
    """The element ("Face9") a mapped name stands for now, or None."""
    if not mapped:
        return None
    try:
        m = shape.ElementMap
    except Exception:  # noqa: BLE001
        return None
    got = m.get(mapped) if isinstance(m, dict) else None
    return got if isinstance(got, str) and got.startswith("Face") else None


def _face_no(f):
    return int(f[4:]) if f[4:].isdigit() else 0


# -- anchors -------------------------------------------------------------------------------
def _face_samples(T, N, tris):
    tris = list(tris)
    if len(tris) > SAMPLES:
        tris = [tris[int(k)] for k in np.linspace(0, len(tris) - 1, SAMPLES)]
    return [[*T[t].mean(0).round(6).tolist(), *N[t].round(6).tolist()] for t in tris]


def anchors(draft, shape, pos, ft, known=None):
    """MaskAnchors for draft (a to_dict()) on this tessellation: per layer
    its faces as {name, mapped, samples}; "known", every face the texture
    was set up (or last confirmed) with (known: their names, default all),
    so a face a later CAD step makes is told apart; the tessellation itself
    when any layer paints triangles (brush, Fill, a mesh part)."""
    T = _tris(pos)
    N = _normals(T)
    names = sorted((ft or {}), key=_face_no) if known is None else [f for f in sorted(ft or {}, key=_face_no) if f in set(known)]
    out = {"version": 1, "layers": [],
           "known": [{"name": f, "mapped": mapped_name(shape, f) if shape is not None else None,
                      "samples": _face_samples(T, N, (ft or {}).get(f, ()))} for f in names]}
    painted = False
    for L in draft.get("layers") or []:
        m = L.get("mask") or {}
        faces = []
        for f in m.get("faces") or []:
            faces.append({"name": f, "mapped": mapped_name(shape, f) if shape is not None else None,
                          "samples": _face_samples(T, N, (ft or {}).get(f, ()))})
        painted = painted or bool(m.get("triangles") or m.get("soft") or m.get("split"))
        out["layers"].append({"faces": faces})
    if painted:
        out["tess"] = _pack(T.reshape(-1, 9))
    return out


# -- remapping ------------------------------------------------------------------------------
def _map_face(a, shape, T, N, face_of, tol):
    """The new faces an anchored face is now: by its mapped name, else the
    faces its sample points lie on; [] when it is gone."""
    got = indexed_name(shape, a.get("mapped")) if shape is not None else None
    if got is not None:
        return [got]
    hits = set()
    C = T.mean(1)
    R = np.linalg.norm(T - C[:, None, :], axis=2).max(1)
    for s in a.get("samples") or ():
        p, n = np.asarray(s[:3]), np.asarray(s[3:6])
        near = np.nonzero((np.linalg.norm(C - p, axis=1) <= R + tol) & ((N @ n) >= NORMAL_COS))[0]
        if not len(near):
            continue
        _q, d = closest_on_triangles(p, T[near])
        k = int(d.argmin())
        if d[k] <= tol:
            f = face_of.get(int(near[k]))
            if f is not None:
                hits.add(f)
    return sorted(hits, key=_face_no)


def _nearest_k(points, refs, k=8):
    """For each point, its k nearest ref points (indices, nearest first)."""
    points, refs = np.asarray(points, np.float64).reshape(-1, 3), np.asarray(refs, np.float64).reshape(-1, 3)
    k = max(1, min(k, len(refs)))
    if not len(points) or not len(refs):
        return np.zeros((len(points), k), np.int64)
    try:
        from scipy.spatial import cKDTree

        _d, i = cKDTree(refs).query(points, k=k)
        return np.asarray(i, np.int64).reshape(len(points), k)
    except ImportError:
        out = np.empty((len(points), k), np.int64)
        step = max(1, 4_000_000 // len(refs))
        for a in range(0, len(points), step):
            d2 = ((points[a:a + step, None, :] - refs[None, :, :]) ** 2).sum(2)
            part = np.argpartition(d2, k - 1, axis=1)[:, :k]
            order = np.take_along_axis(d2, part, 1).argsort(1)
            out[a:a + step] = np.take_along_axis(part, order, 1)
        return out


def _on_surface(points, normals, T, tol):
    """For each point, a triangle of T it lies on (within tol, facing within
    NORMAL_COS of its normal; among its nearest by centroid), or -1."""
    TN = _normals(T)
    cand = _nearest_k(points, T.mean(1))
    out = np.full(len(points), -1, np.int64)
    for j in range(cand.shape[1]):
        todo = out < 0
        if not todo.any():
            break
        c = cand[todo, j]
        _q, d = closest_on_triangles(points[todo], T[c])
        ok = (d <= tol) & ((TN[c] * normals[todo]).sum(1) >= NORMAL_COS)
        idx = np.nonzero(todo)[0]
        out[idx[ok]] = c[ok]
    return out


def _match_triangles(old_T, new_T):
    """Each new triangle's old triangle (the one its centroid lies on,
    facing the same way), or -1."""
    return _on_surface(new_T.mean(1), _normals(new_T), old_T, tolerance(new_T))


def remap(draft, anchored, shape, pos, ft):
    """(draft with its masks on the new tessellation, report, new faces):
    report is a list of {"layer", "what", "lost", "of", "message"} for what
    could not be carried over, which is left out; new faces are the faces
    no face the texture was set up with became (a pocket's, a hole's)."""
    draft = json.loads(json.dumps(draft))
    T = _tris(pos)
    N = _normals(T)
    tol = tolerance(T)
    face_of = {}
    for f, tris in (ft or {}).items():
        for t in tris:
            face_of[int(t)] = f
    report = []
    old_T = _unpack(anchored["tess"], 9).reshape(-1, 3, 3) if anchored.get("tess") else None
    match = _match_triangles(old_T, T) if old_T is not None else None
    for li, L in enumerate(draft.get("layers") or []):
        m = L.get("mask") or {}
        A = (anchored.get("layers") or [])[li] if li < len(anchored.get("layers") or []) else {"faces": []}
        # Faces.
        if m.get("faces"):
            by_name = {a["name"]: a for a in A.get("faces") or ()}
            new, lost = [], 0
            for f in m["faces"]:
                a = by_name.get(f)
                got = _map_face(a, shape, T, N, face_of, tol) if a is not None else []
                if not got:
                    lost += 1
                new.extend(g for g in got if g not in new)
            m["faces"] = new
            if lost:
                n = len(by_name) or len(m["faces"]) + lost
                report.append({"layer": li + 1, "what": "faces", "lost": lost, "of": n,
                               "message": f"Layer {li + 1}: {lost} of {n} masked "
                                          f"{'face no longer exists' if n == 1 else 'faces no longer exist'}; "
                                          "re-pick them."})
        # Painted triangles, soft paint, the Precision brush's splits.
        if old_T is not None and (m.get("triangles") or m.get("soft") or m.get("split")):
            painted = np.zeros(len(old_T), bool)
            painted[[int(t) for t in m.get("triangles") or () if 0 <= int(t) < len(old_T)]] = True
            if m.get("triangles"):
                new_t = np.nonzero((match >= 0) & painted[np.maximum(match, 0)])[0]
                # An old painted triangle with no surface under it now is lost.
                lost = int((_on_surface(old_T[painted].mean(1), _normals(old_T[painted]), T, tol) < 0).sum())
                m["triangles"] = new_t.tolist()
                if lost:
                    n = int(painted.sum())
                    report.append({"layer": li + 1, "what": "triangles", "lost": lost, "of": n,
                                   "message": f"Layer {li + 1}: {lost} of {n} painted triangles have no surface "
                                              "under them now; re-paint them."})
            if m.get("soft"):
                m["soft"] = _remap_soft(m["soft"], old_T, T, match)
            if m.get("split"):
                rows, dropped = _remap_split(m["split"], old_T, T, match)
                m["split"] = rows
                if dropped:
                    report.append({"layer": li + 1, "what": "split", "lost": dropped, "of": dropped + len(rows),
                                   "message": f"Layer {li + 1}: brush detail on {dropped} triangles could not be "
                                              "carried over; re-paint them."})
        L["mask"] = m
    new_faces = []
    if anchored.get("known") is not None:
        seen = set()
        for a in anchored["known"]:
            seen.update(_map_face(a, shape, T, N, face_of, tol))
        new_faces = [f for f in sorted(ft or {}, key=_face_no) if f not in seen]
    return draft, report, new_faces


def without_new(draft, new_faces):
    """The draft as it runs: faces a later CAD step made stay smooth, whatever
    the layer's mode (an exclude layer excludes them too; an include layer
    never lists them)."""
    if not new_faces:
        return draft
    draft = json.loads(json.dumps(draft))
    for L in draft.get("layers") or []:
        m = L.setdefault("mask", {"faces": [], "triangles": []})
        if L.get("includeOnly"):
            m["faces"] = [f for f in m.get("faces") or () if f not in new_faces]
        else:
            m["faces"] = list(m.get("faces") or ()) + [f for f in new_faces if f not in (m.get("faces") or ())]
    return draft


def _remap_soft(soft, old_T, T, match):
    """Soft paint per corner: a new corner of a matched triangle takes the
    value of the old triangle's corner at its place (exact when the
    tessellation is the same there), else of the nearest one."""
    val = {int(c): float(v) for c, v in zip(soft.get("corners") or (), soft.get("values") or ())}
    if not val:
        return soft
    corners, values = [], []
    for t in np.nonzero(match >= 0)[0].tolist():
        o = int(match[t])
        olds = [val.get(o * 3 + k) for k in range(3)]
        if all(v is None for v in olds):
            continue
        for k in range(3):
            j = int(np.linalg.norm(old_T[o] - T[t, k], axis=1).argmin())
            v = olds[j]
            if v is not None:
                corners.append(t * 3 + k)
                values.append(v)
    return {"corners": corners, "values": values}


def _remap_split(split, old_T, T, match):
    """The Precision brush's rows move to a new triangle with the same three
    corners in the same order; the others are dropped (and counted)."""
    by_old = {}
    for t in np.nonzero(match >= 0)[0].tolist():
        by_old.setdefault(int(match[t]), []).append(t)
    rows, dropped = [], 0
    for r in split:
        o = int(r[0])
        new = [t for t in by_old.get(o, ()) if np.allclose(T[t], old_T[o], atol=1e-6)]
        if new:
            rows.append([new[0], r[1], r[2], r[3]])
        else:
            dropped += 1
    return rows, dropped


# -- the feature ----------------------------------------------------------------------------
def _warn(text):
    try:
        import FreeCAD

        FreeCAD.Console.PrintWarning(f"Texturizer: {text}\n")
    except Exception:  # noqa: BLE001
        pass


def ensure_props(obj):
    _global_source(obj)
    for prop, kind, doc in LINK_PROPS:
        if prop not in obj.PropertiesList:
            obj.addProperty(kind, prop, GROUP, doc)


def _global_source(obj):
    """Source made as a plain App::PropertyLink (50f02ed) becomes a
    PropertyLinkGlobal, keeping its body."""
    if "Source" not in getattr(obj, "PropertiesList", ()):
        return
    try:
        kind = obj.getTypeIdOfProperty("Source")
    except Exception:  # noqa: BLE001
        return
    if kind != "App::PropertyLink":
        return
    body = obj.Source
    obj.removeProperty("Source")
    obj.addProperty(LINK_KIND, "Source", GROUP, LINK_PROPS[0][2])
    obj.Source = body


def is_linked(obj):
    return "Source" in getattr(obj, "PropertiesList", ())


class TexturedFeature:
    """The proxy of a Textured object (Mesh::FeaturePython)."""

    def __init__(self, obj=None):
        if obj is not None:
            obj.Proxy = self

    def execute(self, obj):
        rerun(obj, sync=None)

    def onDocumentRestored(self, obj):  # noqa: N802 (FreeCAD)
        ensure_props(obj)

    def dumps(self):
        return None

    def loads(self, state):
        return None

    __getstate__ = dumps

    def __setstate__(self, state):
        return None


def source_of(obj):
    src = getattr(obj, "Source", None)
    if src is None and getattr(obj, "SourceBody", ""):
        src = live.find(getattr(obj, "Document", None), obj.SourceBody)
    return src


def _set(obj, prop, value):
    if getattr(obj, prop, None) != value:
        setattr(obj, prop, value)


def _probe(obj, what):
    """A linked rebuild's step, with the timing probe on."""
    from . import timing

    timing.report(f"linked rebuild {getattr(obj, 'Label', '')}: {what}", 0.0)


def state(obj):
    return getattr(obj, "TextureState", "") or ""


def rerun(obj, sync=None, force=False):
    """Texture obj's body again when its geometry changed (force: anyway).
    sync None: in the background when a worker is set (Reruns), else here.
    Returns the job's report, or None when nothing ran (up to date) or it
    runs in the background."""
    src = source_of(obj)
    if src is None:
        _set(obj, "TextureState", NO_SOURCE)
        return None
    _set(obj, "SourceBody", src.Name)
    sig = live.signature(src)
    if not force and sig == getattr(obj, "SourceSignature", None) and getattr(obj, "Mesh", None) is not None:
        return None
    job = prepare(obj, src, sig)
    if job is None:   # made from texture_apply's own arguments: Update runs them
        _set(obj, "OutOfDate", True)
        _set(obj, "TextureState", "out of date: Update to texture it again")
        return None
    worker = Reruns.current()
    if sync is False or (sync is None and worker is not None):
        if worker is None:
            worker = Reruns()
        if not force and worker.running_for(obj) == sig:
            _probe(obj, f"skipped: a rebuild for this geometry is already running")
            return None   # a recompute again (another tool's, the GUI's): the run under way is the one
        _set(obj, "TextureState", UPDATING)
        _set(obj, "OutOfDate", True)
        worker.schedule(obj, job)
        return None
    write(obj, job, job.run(lambda: False))
    return job.report


class Job:
    """One rerun: everything read from the document on the GUI thread; run()
    touches numpy and the engine only."""

    def __init__(self, pos, run, mode, draft, report, signature, shape, ft):
        self.pos, self.run_settings, self.mode = pos, run, mode
        self.draft, self.report, self.signature = draft, report, signature
        self.shape, self.ft = shape, ft
        self.new_faces = []

    def run(self, should_abort):
        from . import engine

        return engine.printable(engine.texture(self.pos, self.run_settings, mode=self.mode, should_abort=should_abort))


def prepare(obj, src, sig=None):
    from .draft import Draft
    from .handlers import run_settings

    stored = getattr(obj, "TextureDraft", "") or ""
    if not stored:
        return _prepare_args(obj, src, sig)
    pos, ft = live.mesh_for(src)
    data = json.loads(stored)
    data["part"] = src.Name
    report, new_faces = [], []
    anchored = json.loads(getattr(obj, "MaskAnchors", "") or "null")
    if anchored:
        data, report, new_faces = remap(data, anchored, getattr(src, "Shape", None), pos, ft)
    elif _has_masks(data):
        report = [{"layer": 0, "what": "anchors", "lost": 0, "of": 0,
                   "message": "Masks made before this body changed cannot be followed; re-pick them, then Update."}]
    d = Draft.from_dict(data)
    run = run_settings(Draft.from_dict(without_new(d.to_dict(), new_faces)), ft)
    mode = "bake" if getattr(obj, "Baked", False) else "export"
    job = Job(np.asarray(pos, np.float32), run, mode, d.to_dict(), report, sig or live.signature(src),
              getattr(src, "Shape", None), ft)
    job.new_faces = new_faces
    return job


def _has_masks(data):
    return any((L.get("mask") or {}).get(k) for L in data.get("layers") or () for k in ("faces", "triangles", "soft", "split"))


def _prepare_args(obj, src, sig):
    """An object made from texture_apply's own arguments: they name faces
    (pick_faces queries), so they run as they are, as Update did."""
    return None


def write(obj, job, res):
    """The finished mesh on obj: one write of Mesh, the draft as remapped,
    its anchors on the new geometry, the state; outside any transaction, and
    obj left untouched for the next recompute."""
    from .handlers import _make_mesh

    if res is None:
        return False
    m = _make_mesh(res["positions"])
    if m is not None:
        obj.Mesh = m
    _set(obj, "TextureDraft", json.dumps(job.draft))
    known = [f for f in (job.ft or {}) if f not in job.new_faces]
    _set(obj, "MaskAnchors", json.dumps(anchors(job.draft, job.shape, job.pos, job.ft, known=known)))
    _set(obj, "NewFaces", json.dumps(job.new_faces))
    _set(obj, "SourceSignature", job.signature)
    _set(obj, "OutOfDate", False)
    if job.report:
        _set(obj, "TextureState", REPICK + ": " + " ".join(r["message"] for r in job.report))
        for r in job.report:
            _warn(f"{obj.Label}: {r['message']}")
    else:
        _set(obj, "TextureState", UP_TO_DATE)
    if job.new_faces:
        _warn(f"{obj.Label}: {new_faces_text(job.new_faces)}")
    try:
        obj.purgeTouched()
    except Exception:  # noqa: BLE001
        pass
    return True


class Reruns:
    """The background reruns: run_async(fn) on a worker, post(fn) back on the
    GUI thread (the window's, the preview's own); one generation per
    object, so a newer change makes an older run stale (its engine stops at
    the next should_abort) and only the newest result is written, when no
    transaction is open."""

    _current = None

    def __init__(self, run_async=None, post=None, later=None):
        from .preview import _thread

        self.run_async = run_async or _thread
        self.post = post or (lambda fn: fn())
        self.later = later or (lambda fn: self.post(fn))
        self.gen = {}
        self.running = {}   # key -> the signature being made

    @classmethod
    def current(cls):
        return cls._current

    @classmethod
    def install(cls, run_async, post, later=None):
        cls._current = cls(run_async, post, later)
        return cls._current

    WAIT_TRIES = 8   # x the later() delay (0.25 s with the window): then write inside the open step

    def _key(self, obj):
        return (getattr(getattr(obj, "Document", None), "Name", ""), obj.Name)

    def running_for(self, obj):
        """The signature the run under way for obj is making, or None."""
        return self.running.get(self._key(obj))

    def schedule(self, obj, job):
        key = self._key(obj)
        g = self.gen.get(key, 0) + 1
        self.gen[key] = g
        self.running[key] = job.signature
        _probe(obj, f"queued (run {g})")

        def stale():
            return self.gen.get(key) != g

        def work():
            import time

            t0 = time.perf_counter()
            self.post(lambda: _probe(obj, f"started (run {g})"))
            try:
                res = job.run(stale)
            except Exception as e:  # noqa: BLE001
                res, err = None, e
            else:
                err = None
            ms = (time.perf_counter() - t0) * 1000
            self.post(lambda: self._done(obj, job, res, err, stale, g, ms, 0))

        try:
            self.run_async(work)
        except Exception as e:  # noqa: BLE001 — never leave it "updating…"
            self._fail(obj, key, g, f"could not start: {e}")

    def _fail(self, obj, key, g, why):
        if self.gen.get(key) == g:
            self.running.pop(key, None)
        _set(obj, "TextureState", f"update failed: {why}")
        _warn(f"{getattr(obj, 'Label', '')}: update failed: {why}")
        _probe(obj, f"failed (run {g}): {why}")

    def _done(self, obj, job, res, err, stale, g=0, ms=0.0, tries=0):
        key = self._key(obj)
        try:
            if stale():
                _probe(obj, f"skipped (run {g}): a newer change replaced it")
                return
            if err is not None:
                return self._fail(obj, key, g, f"{type(err).__name__}: {err}")
            if res is None:
                return self._fail(obj, key, g, "the engine stopped before it finished")
            _probe(obj, f"finished (run {g}) {ms:.0f} ms")
            doc = getattr(obj, "Document", None)
            if getattr(doc, "HasPendingTransaction", False) and tries < self.WAIT_TRIES:
                _probe(obj, f"waiting (run {g}): a transaction is open")
                self.later(lambda: self._done(obj, job, res, err, stale, g, ms, tries + 1))   # not inside someone's step
                return
            write(obj, job, res)
            self.running.pop(key, None)
            _probe(obj, f"written (run {g})" + (" inside an open transaction" if tries >= self.WAIT_TRIES else ""))
        except Exception as e:  # noqa: BLE001 — never leave it "updating…", never raise into Qt
            self._fail(obj, key, g, f"{type(e).__name__}: {e}")


def new_faces(obj):
    try:
        return list(json.loads(getattr(obj, "NewFaces", "") or "[]"))
    except ValueError:
        return []


def new_faces_text(faces):
    n = len(faces)
    return (f"{n} new {'face' if n == 1 else 'faces'} since texturing ({', '.join(faces)}) "
            f"{'is' if n == 1 else 'are'} left smooth; Include them to texture {'it' if n == 1 else 'them'}.")


def include_new(obj, sync=None, run=True):
    """Include them: the faces made after setup join the texture (include
    layers list them; exclude layers stop excluding them) and are known
    from now on; then it textures again."""
    faces = new_faces(obj)
    src = source_of(obj)
    if not faces or src is None:
        return []
    data = json.loads(getattr(obj, "TextureDraft", "") or "null")
    if data is not None:
        for L in data.get("layers") or []:
            if L.get("includeOnly"):
                m = L.setdefault("mask", {"faces": [], "triangles": []})
                m["faces"] = list(m.get("faces") or ()) + [f for f in faces if f not in (m.get("faces") or ())]
        _set(obj, "TextureDraft", json.dumps(data))
        pos, ft = live.mesh_for(src)
        _set(obj, "MaskAnchors", json.dumps(anchors(data, getattr(src, "Shape", None), pos, ft)))
    _set(obj, "NewFaces", "[]")
    if run:
        rerun(obj, sync=sync, force=True)
    return faces


# -- making and converting ---------------------------------------------------------------------
def make(doc, name="Textured"):
    """A new linked Textured object."""
    obj = doc.addObject(KIND, name)
    try:
        TexturedFeature(obj)
    except Exception:  # noqa: BLE001 — a stand-in object without a Proxy slot
        pass
    ensure_props(obj)
    return obj


def link(obj, body, draft_dict=None, pos=None, ft=None):
    """Source, SourceBody and the anchors of draft_dict on body as it is."""
    ensure_props(obj)
    obj.Source = body
    _set(obj, "SourceBody", body.Name)
    if draft_dict is not None:
        if pos is None:
            pos, ft = live.mesh_for(body)
        _set(obj, "MaskAnchors", json.dumps(anchors(draft_dict, getattr(body, "Shape", None), pos, ft)))
    _set(obj, "TextureState", UP_TO_DATE)


def convert(doc):
    """Old Textured objects (a Mesh::Feature with a string SourceBody) as
    linked features: the same mesh, properties and label; the old one
    removed. Returns the new objects."""
    from .handlers import PROPS

    out = []
    for old in list(getattr(doc, "Objects", None) or ()):
        props = getattr(old, "PropertiesList", ())
        if "SourceBody" not in props or "Source" in props:
            continue
        body = live.find(doc, old.SourceBody)
        new = make(doc, "Textured")
        try:
            new.Mesh = old.Mesh
        except Exception:  # noqa: BLE001
            pass
        for prop, kind, text in PROPS:
            if prop not in new.PropertiesList:
                new.addProperty(kind, prop, GROUP, text)
            if prop in props:
                setattr(new, prop, getattr(old, prop))
        label = old.Label
        stale = bool(getattr(old, "OutOfDate", False)) or (body is not None and live.signature(body) != getattr(old, "SourceSignature", ""))
        if body is not None:
            data = json.loads(getattr(old, "TextureDraft", "") or "null")
            ensure_props(new)
            new.Source = body
            new.SourceBody = body.Name
            if data is not None and not stale:
                pos, ft = live.mesh_for(body)
                new.MaskAnchors = json.dumps(anchors(data, getattr(body, "Shape", None), pos, ft))
            new.TextureState = UP_TO_DATE if not stale else REPICK + ": made before its body changed; re-pick masks, then Update."
        else:
            new.TextureState = NO_SOURCE
        doc.removeObject(old.Name)
        new.Label = label
        try:
            new.purgeTouched()
        except Exception:  # noqa: BLE001
            pass
        out.append(new)
    return out


# -- the live draft follows its body too ------------------------------------------------------
class DraftFollower:
    """The draft the sidebar shows, kept on its body: a snapshot of the
    part's geometry (tessellation, mapped names) the masks were made on;
    when the part's geometry changes (follow()), its masks move with the
    same anchors as a Textured object's (mapped names, then surface samples;
    brush paint by surface), what can't move is left out and flagged, and
    faces a later CAD step made stay smooth (excluded) and are listed until
    Include Them. Add to Document then never applies old face names to
    renumbered faces. The change is a draft edit: in the CAD step's own
    transaction when one is open (its Undo undoes both)."""

    _current = None

    def __init__(self, draft, doc_fn=None):
        self.draft = draft
        self.doc_fn = doc_fn or (lambda: live._doc() or live.LAST_DOC["doc"])
        self.snap = None
        self.new_faces, self.report = [], []
        self._applying = False
        self._unsub = draft.subscribe(self._on_draft)
        DraftFollower._current = self

    @classmethod
    def current(cls):
        return cls._current

    def detach(self):
        self._unsub()
        if DraftFollower._current is self:
            DraftFollower._current = None

    def _part(self):
        d = self.draft
        return live.find(self.doc_fn(), d.part) if d.part else None

    def _on_draft(self, ev):
        if self._applying:
            return
        if ev.get("kind") in ("part", "load", "reset", "undo", "redo"):
            # An undo or redo restores the part and the draft together: what
            # the masks are made on is the part as it is now.
            if self.snap is None or self.snap["part"] != self.draft.part or ev.get("kind") in ("undo", "redo"):
                if self.new_faces or self.report:
                    self.new_faces, self.report = [], []
                    self._notify()
            self.snapshot()

    def _notify(self):
        fn = getattr(live.GUI, "draft_follow_changed", None)
        if fn is not None:
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass

    def snapshot(self):
        """The part's geometry as it is now: what the masks are made on."""
        o = self._part()
        if o is None:
            self.snap = None
            return
        try:
            pos, ft = live.mesh_for(o)
        except Exception:  # noqa: BLE001 — no faces yet: nothing to follow
            self.snap = None
            return
        sh = getattr(o, "Shape", None)
        rev = getattr(sh, "ElementReverseMap", None) if sh is not None else None
        self.snap = {"part": o.Name, "sig": live.signature(o), "pos": pos, "ft": ft,
                     "shape": types.SimpleNamespace(ElementReverseMap=dict(rev) if isinstance(rev, dict) else {})}

    def follow(self):
        """Move the draft's masks onto the part as it is now, when its
        geometry changed since the snapshot. Returns (report, new faces) or
        None when nothing changed."""
        o = self._part()
        if o is None:
            return None
        if self.snap is None or self.snap["part"] != o.Name:
            self.snapshot()
            return None
        sig = live.signature(o)
        if sig == self.snap["sig"]:
            return None
        d = self.draft
        snap = self.snap
        known = [f for f in snap["ft"] if f not in self.new_faces]
        a = anchors(d.to_dict(), snap["shape"], snap["pos"], snap["ft"], known=known)
        pos, ft = live.mesh_for(o)
        data, report, new = remap(d.to_dict(), a, getattr(o, "Shape", None), pos, ft)
        data = without_new(data, new)
        self.new_faces, self.report = new, report
        self._applying = True
        try:
            d.load(data)
        finally:
            self._applying = False
        self.snapshot()
        for r in report:
            _warn(f"{o.Label} (the sidebar's draft): {r['message']}")
        if new:
            _warn(f"{o.Label} (the sidebar's draft): {new_faces_text(new)}")
        self._notify()
        return report, new

    def include_new(self):
        """Include Them for the draft: the new faces join every layer
        (include layers list them, exclude layers stop excluding them)."""
        faces, self.new_faces = list(self.new_faces), []
        if not faces:
            return []
        data = self.draft.to_dict()
        for L in data.get("layers") or []:
            m = L.setdefault("mask", {"faces": [], "triangles": []})
            if L.get("includeOnly"):
                m["faces"] = list(m.get("faces") or ()) + [f for f in faces if f not in (m.get("faces") or ())]
            else:
                m["faces"] = [f for f in m.get("faces") or () if f not in faces]
        self._applying = True
        try:
            self.draft.load(data)
        finally:
            self._applying = False
        self._notify()
        return faces


def follow_draft(doc=None):
    """The live draft onto its part as it is now (see DraftFollower)."""
    f = DraftFollower.current()
    if f is None:
        return None
    if doc is not None:
        live.LAST_DOC["doc"] = doc
    try:
        return f.follow()
    except Exception as e:  # noqa: BLE001 — a failed follow never stops a tool or a recompute
        _warn(f"following the draft's masks: {e}")
        return None


class Observer:
    """Old documents convert when they open (slotFinishRestoreDocument)."""

    def slotFinishRestoreDocument(self, doc):  # noqa: N802 (FreeCAD)
        try:
            convert(doc)
        except Exception as e:  # noqa: BLE001 — never raise into FreeCAD
            _warn(f"converting old Textured objects: {e}")
