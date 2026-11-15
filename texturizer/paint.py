# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 CNCKitchen (Stefan Hermann) and contributors; the
# Texturizer for FreeCAD contributors.
# Port of the brush of BumpMesh (stlTexturizer) by Stefan Hermann (CNC
# Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer: js/paintTree.js
# (paintStroke, paintFaces, _facesInDisk, distSqPointToTri), js/softMask.js
# (brushCoverage) and js/main.js (_paintSingleHit, _brushEdgeLimit, paintAt,
# getFrontFaceHit, _viewDirFor).
"""The mask brush (design 4.2, P2 and P3), pure: no FreeCAD, no Coin.

A Surface is the part's source tessellation welded as BumpMesh welds it
(exclusion.build_adjacency). A State is one layer's paint on it: a hard flag
per source triangle and, for the soft brush, a coverage value per welded
vertex (BumpMesh's PaintTree with no face split, so its leaves are the
source triangles).

Brush: Type Single (the triangle under the cursor) or Circle (the source
triangles connected to the hit and reaching into the screen disk of radius
Size / 2, spreading over front-facing neighbours only), Mode Standard, and
Hardness %: 100 paints whole triangles (BumpMesh's Standard brush); under
100 paints BumpMesh's soft coverage per vertex (brushCoverage: full inside
the hard core, a smoothstep fade to the rim). Precision (paintTree's
refinement, P4) is not here.

A stroke is a list of hits {point, normal, triangle[, view]}; None breaks
it (the cursor left the model). A stroke paints, or with erase (Shift)
erases. In the draft a stroke is one undo step (Draft.mask_set), and the
paint is the layer's mask: triangles (hard) plus soft {corners, values}
(BumpMesh's legacy mask.soft form), which project.py writes as paint.json.

PaintTool maps viewer events to strokes: left button paints when it hits
the model, middle and right are never taken (FreeCAD keeps navigating).
"""
import math

import numpy as np

from . import painttree
from .engine import exclusion
from .engine._js import F32
from .engine.softmask import brush_coverage

MIN_SIZE, MAX_SIZE = 0.2, 100.0


# -- the surface ---------------------------------------------------------------------------
class Surface:
    """The source tessellation as BumpMesh's PaintTree sees it."""

    def __init__(self, positions):
        pos = np.asarray(positions, F32).reshape(-1)
        self.positions = pos
        self.n = len(pos) // 9
        adj = exclusion.build_adjacency(pos)
        self.adj = adj
        self.vert_id = np.asarray(adj["vertId"], np.int64)
        self.vert_count = int(adj["vertCount"])
        self.tri = self.vert_id.reshape(-1, 3)
        self.neighbours = [[nb for nb, _a in a] for a in adj["adjacency"]]
        self.face_normals = np.asarray(adj["faceNormals"], F32).astype(np.float64).reshape(-1, 3)
        V = np.zeros((self.vert_count, 3))
        V[self.vert_id] = pos.reshape(-1, 3).astype(np.float64)  # PaintTree: the last corner of a vertex wins
        self.V = V
        self._Vl = V.tolist()
        self._tl = self.tri.tolist()
        self._fn = self.face_normals.tolist()


_CACHE = {}


def _key(positions):
    if isinstance(positions, np.ndarray):
        return ("nd", positions.__array_interface__["data"][0], positions.size, positions.dtype.str)
    return ("id", id(positions))


def surface_for(positions):
    """The Surface of a tessellation, kept while the same array (or a view of
    the same buffer, as the engine's reshapes) is passed (the window's snapshot)."""
    key = _key(positions)
    got = _CACHE.get("s")
    if got is not None and got[0] == key:
        return got[2]
    s = Surface(positions)
    _CACHE["s"] = (key, positions, s)
    return s


class State:
    """One layer's paint: a single-layer PaintTree (painttree.py) on the surface.
    hard: the base triangles' own states (uint8), cov: soft coverage per welded
    base vertex (float32) or None; both read the roots, as basePaint does."""

    def __init__(self, surface, hard=None, cov=None, tree=None):
        self.s = surface
        if tree is None:
            tree = painttree.PaintTree(surface)
            tree.add_layer(1)
            L = tree.layers[0]
            if hard is not None:
                L.state[:surface.n] = [int(x) for x in np.asarray(hard, np.uint8).tolist()]
            if cov is not None:
                L.cov = np.asarray(cov, F32).tolist()   # float32 values, as Float32Array holds them
        self.tree = tree

    def copy(self):
        return State(self.s, tree=painttree.single(self.tree, 0))

    @property
    def layer(self):
        return self.tree.layers[0]

    @property
    def hard(self):
        return np.asarray(self.layer.state[:self.s.n], np.uint8)

    @property
    def cov(self):
        c = self.layer.cov
        return None if c is None else np.asarray(c[:self.s.vert_count], F32)

    def _ensure_cov(self):
        self.tree._ensure_cov(self.layer)
        return self.cov

    def corners(self):
        """Paint per base triangle corner (0..1) from the roots: 1 on a hard
        triangle, else the vertex coverage (basePaint)."""
        return self.tree.base_paint(0)[0]

    @property
    def split(self):
        """True when the Precision brush split a triangle."""
        return not self.tree.is_flat


# -- BumpMesh's geometry -------------------------------------------------------------------
dist_sq_point_to_tri = painttree.dist_sq_point_to_tri


edge_limit = painttree.edge_limit


# -- painting ------------------------------------------------------------------------------
def paint_faces(state, faces, erase=False):
    """paintFaces: whole triangles (Single brush, Fill); erasing also wipes soft coverage there."""
    state.tree.paint_faces(0, faces, erase)


def paint_stroke(state, seed, frm, to, radius, view, hardness, erase=False, whole=False, edge=None):
    """paintStroke: one dab, or the capsule swept from frm to to. whole: the
    Standard brush (every touched triangle in full); otherwise the Precision
    brush, which splits the triangles under the rim down to the edge limit
    and, under Hardness 1, paints soft coverage per vertex."""
    state.tree.paint_stroke(0, int(seed), frm, to, radius, view, hardness, erase, edge=edge, whole=whole)


class Brush:
    """The sidebar's Brush rows: circle (Type), size (diameter, mm), hardness
    (%) and mode: BumpMesh's Standard (whole triangles, Hardness ignored) or
    Precision (refines under the stroke, with Hardness)."""

    MODES = ("standard", "precision")

    def __init__(self, circle=True, size=10.0, hardness=50.0, mode="precision"):
        if mode not in self.MODES:
            raise ValueError("Mode: standard or precision.")
        self.circle = bool(circle)
        self.size = max(MIN_SIZE, min(MAX_SIZE, float(size)))
        self.hardness = max(0.0, min(100.0, float(hardness)))
        self.mode = mode

    @classmethod
    def from_draft(cls, d):
        return cls(d.get("brushIsRadius"), d.get("brushSize"), d.get("brushHardness"),
                   "precision" if d.get("brushPrecision") else "standard")

    @property
    def precision(self):
        return self.mode == "precision"

    @property
    def radius(self):
        return self.size / 2

    @property
    def h(self):
        """The hardness a stroke paints with (0..1): 1 for Standard, as BumpMesh."""
        return self.hardness / 100.0 if self.precision else 1.0


def hit_view(hit, s):
    """A hit's view direction: its own, else into the surface (−normal, or −the triangle's normal)."""
    if hit.get("view") is not None:
        v = np.asarray(hit["view"], float)
    else:
        n = hit.get("normal")
        n = np.asarray(n, float) if n is not None else s.face_normals[int(hit["triangle"])]
        v = -n
    ln = np.linalg.norm(v)
    return (v / ln) if ln > 0 else np.array([0.0, 0.0, -1.0])


def nearest_triangle(s, point):
    """The source triangle nearest a point (an agent's hit without a triangle)."""
    p = np.asarray(point, float)
    t = s.positions.astype(np.float64).reshape(-1, 3, 3)
    c = t.mean(axis=1)
    order = np.argsort(((c - p) ** 2).sum(1))[:64]
    d = [dist_sq_point_to_tri(*p, *t[k].reshape(-1)) for k in order.tolist()]
    return int(order[int(np.argmin(d))])


def parse_hit(s, h):
    """An agent's hit: {point, normal?, triangle?, view?} or a bare [x, y, z];
    None (a break) stays None. Raises ValueError on a bad one."""
    if h is None:
        return None
    if isinstance(h, (list, tuple)):
        h = {"point": h}
    if not isinstance(h, dict) or "point" not in h:
        raise ValueError("a hit is {point, normal, triangle} or [x, y, z]")
    pt = [float(c) for c in h["point"]]
    if len(pt) != 3 or not all(math.isfinite(c) for c in pt):
        raise ValueError("a hit's point is [x, y, z]")
    out = {"point": pt, "normal": None, "view": None}
    for k in ("normal", "view"):
        if h.get(k) is not None:
            v = [float(c) for c in h[k]]
            if len(v) != 3 or not any(v):
                raise ValueError(f"a hit's {k} is a direction [x, y, z]")
            out[k] = v
    t = h.get("triangle")
    if t is None:
        got = None
        if out["normal"] is not None:
            n = np.asarray(out["normal"], float)
            n = n / np.linalg.norm(n)
            got = ray_hit(s.positions, np.asarray(pt) + n * 0.01, -n)
        t = got["triangle"] if got else nearest_triangle(s, pt)
    if isinstance(t, bool) or not isinstance(t, (int, np.integer)) or not 0 <= int(t) < s.n:
        raise ValueError(f"a hit's triangle is 0 to {s.n - 1}")
    out["triangle"] = int(t)
    return out


def paint_hit(state, hit, brush, erase, prev=None):
    """_paintSingleHit: Circle sweeps from the previous point of the stroke; Single paints the triangle."""
    s = state.s
    t = int(hit["triangle"])
    if brush.circle:
        paint_stroke(state, t, prev, hit["point"], brush.radius, hit_view(hit, s), brush.hardness / 100.0, erase,
                     whole=not brush.precision, edge=edge_limit(brush.radius, brush.h))
    else:
        paint_faces(state, [t], erase)


def apply_stroke(state, hits, brush, erase=False):
    """One stroke (paintAt over a drag): None breaks the sweep."""
    prev = None
    for h in hits:
        if h is None:
            prev = None
            continue
        paint_hit(state, h, brush, erase, prev)
        prev = h["point"]
    return state


def fill(state, seed, max_angle=20.0, erase=False):
    """BumpMesh's Fill on the triangles: Mask.flood from the seed, painted (or erased) whole."""
    m = exclusion.flood(state.s.positions, int(seed), float(max_angle), adjacency=state.s.adj)
    paint_faces(state, m.triangles.tolist(), erase)
    return state


# -- the draft's mask ----------------------------------------------------------------------
def soft_of(state):
    """The soft paint on the base vertices as {corners, values} (corners with coverage > 0), or None."""
    c = state.cov
    if c is None:
        return None
    c = c[state.s.vert_id]
    idx = np.nonzero(c > 0)[0]
    if not len(idx):
        return None
    return {"corners": idx.tolist(), "values": [float(x) for x in c[idx]]}


def split_of(state):
    """The Precision brush's split base triangles as the draft keeps them:
    [[triangle, codes, states, cov], ...], codes and states the triangle's
    depth-first node stream (paintTree.js serialize's order) and cov
    [position, c0, c1, c2, ...] per leaf with soft coverage (its leafCov)."""
    t = state.tree
    L = t.layers[0]
    out = []
    for f in t.split_roots():
        codes, states, cov = [], [], []
        stack = [f]
        while stack:
            n = stack.pop()
            pos = len(codes)
            codes.append(t.code[n])
            first = t.child[n]
            # A split node's own state is never read (its leaves carry the paint): 0, so equal paint compares equal.
            states.append(L.state[n] if first == painttree.NO_CHILD else 0)
            if first == painttree.NO_CHILD:
                if L.cov is not None:
                    c = [L.cov[t.nv[n * 3 + k]] for k in range(3)]
                    if any(x > 0 for x in c):
                        cov += [pos] + c
                continue
            for k in range(t.child_count(n) - 1, -1, -1):
                stack.append(first + k)
        out.append([f, codes, states, cov])
    return out


def _replay_split(state, rec):
    t = state.tree
    L = t.layers[0]
    f, codes, states, cov = rec
    f = int(f)
    if not 0 <= f < t.base_tri_count or t.child[f] != painttree.NO_CHILD:
        return
    nodes, pos, stack = [], 0, [f]
    while stack and pos < len(codes):
        n = stack.pop()
        nodes.append(n)
        code = int(codes[pos])
        L.state[n] = int(states[pos]) if pos < len(states) else 0
        pos += 1
        if code & 7:
            first = t.split(n, code & 7, (code >> 3) & 1)
            for k in range(t.child_count(n) - 1, -1, -1):
                stack.append(first + k)
    # Every vertex under the triangle takes its recorded value: a leaf not
    # listed has none (a split above may have interpolated some).
    c = t._ensure_cov(L) if cov else L.cov
    if c is None:
        return
    got = {}
    for i in range(0, len(cov) - 3, 4):
        p = int(cov[i])
        if 0 <= p < len(nodes):
            got[nodes[p]] = cov[i + 1:i + 4]
    for n in nodes:
        if t.child[n] != painttree.NO_CHILD:
            continue
        vals = got.get(n, (0.0, 0.0, 0.0))
        for k in range(3):
            c[t.nv[n * 3 + k]] = painttree.f32(float(vals[k]))


def state_from_mask(s, mask, face_triangles=None):
    """A State from a draft mask (its B-rep faces count as hard triangles,
    split triangles replay their Precision paint)."""
    # A fresh tree has no splits: the roots' states and the base vertices'
    # coverage are set in one go (what paintFaces and setBaseCoverage leave).
    hard = np.zeros(s.n, np.uint8)
    tris = [int(t) for t in mask.get("triangles") or []]
    for f in mask.get("faces") or []:
        tris.extend(int(t) for t in (face_triangles or {}).get(f, []))
    t = np.asarray(tris, np.int64)
    hard[t[(t >= 0) & (t < s.n)]] = 1
    cov = None
    soft = mask.get("soft")
    if soft and soft.get("corners"):
        cov = np.zeros(s.vert_count, F32)
        c = np.asarray(soft["corners"], np.int64)
        v = np.asarray(soft["values"], F32)
        ok = (c >= 0) & (c < s.n * 3)
        np.maximum.at(cov, s.vert_id[c[ok]], v[ok])
    st = State(s, hard, cov)
    for rec in mask.get("split") or []:
        _replay_split(st, rec)
    return st


def mask_from_state(state, mask, face_triangles=None):
    """The draft mask for a State: a B-rep face stays a face while all its
    triangles are painted whole; one the brush cut into becomes its
    triangles; split triangles go in split."""
    split = split_of(state)
    cut = {r[0] for r in split}
    hard = set(np.nonzero(state.hard)[0].tolist()) - cut
    faces = []
    for f in mask.get("faces") or []:
        ts = [int(t) for t in (face_triangles or {}).get(f, [])]
        if ts and all(t in hard for t in ts):
            faces.append(f)
            hard.difference_update(ts)
    out = {"faces": faces, "triangles": sorted(hard)}
    soft = soft_of(state)
    if soft:
        out["soft"] = soft
    if split:
        out["split"] = split
    return out


def overlay(state):
    """The painted triangles to draw over the body: (corner positions (k*3, 3),
    paint per corner (k*3,)), the flattened tree's leaves where the Precision
    brush split, the source triangles elsewhere."""
    if state.split:
        flat = state.tree.flatten()
        p = state.tree.flat_paint(0, flat)[0].reshape(-1, 3)
        P = flat["positions"].reshape(-1, 3, 3)
    else:
        p = state.corners().reshape(-1, 3)
        P = state.s.positions.reshape(-1, 3, 3)
    on = np.nonzero((p > 0).any(axis=1))[0]
    return P[on].reshape(-1, 3).astype(float), p[on].reshape(-1).astype(float)


# -- the engine's view of split masks ------------------------------------------------------
def has_split(specs):
    return any((L.get("mask") or {}).get("split") for L in specs if L.get("visible") is not False)


def _mode_of(L):
    m = L.get("mask") or {}
    return m.get("mode") or ("include" if (L.get("includeOnly") or m.get("includeOnly")) else "exclude")


def flatten_masks(positions, specs):
    """BumpMesh's export input when the Precision brush split triangles
    (main.js _pipelineInputs): the layers' trees joined (painttree.combine),
    flattened, and each layer's mask as per-corner paint on that mesh.
    (positions, specs, faceParentId to the source triangles), or None when
    nothing is split."""
    if not has_split(specs):
        return None
    srf = surface_for(positions)
    trees = [state_from_mask(srf, L.get("mask") or {}).tree for L in specs]
    t = painttree.combine(srf, trees)
    flat = t.flatten()
    out = []
    for i, L in enumerate(specs):
        paint, _hard = t.flat_paint(i, flat)
        out.append(dict(L, mask={"mode": _mode_of(L), "paint": paint}))
    return flat["positions"], out, np.asarray(flat["faceParentId"], np.int64)


def split_marker(mask, n_tri):
    """Per-corner paint for a layer's mask at the source-triangle level with
    its split triangles neither hard-painted nor untouched (0.5), so neither
    mode hard-masks them; sample_split fills them in on the refined mesh."""
    p = np.zeros((n_tri, 3), F32)
    for r in mask.get("split") or []:
        f = int(r[0])
        if 0 <= f < n_tri:
            p[f] = 0.5
    return p


def sample_split(positions, mask, include, child_pos, parents):
    """BumpMesh's _sampledPaint for one layer on a refinement of the source
    mesh (the 3D preview): for the refined triangles whose source triangle
    the Precision brush split, (rows, exclude per corner, hard flag) by the
    layer's tree sampled at each corner; None when nothing is split."""
    split = {int(r[0]) for r in mask.get("split") or []}
    if not split:
        return None
    parents = np.asarray(parents, np.int64)
    rows = np.nonzero(np.isin(parents, np.fromiter(split, np.int64)))[0]
    if not len(rows):
        return None
    st = state_from_mask(surface_for(positions), mask)
    t = st.tree
    cp = np.asarray(child_pos, F32).astype(np.float64).reshape(-1, 3, 3)[rows].tolist()
    pr = parents[rows].tolist()
    paint = np.zeros((len(rows), 3))
    for i, (f, tri) in enumerate(zip(pr, cp)):
        for k in range(3):
            paint[i, k] = t.sample_paint(0, f, *tri[k])
    hard = (paint >= 1).all(axis=1)
    if include:
        cover = paint
        hm = ~hard & (paint == 0).all(axis=1)
    else:
        cover = 1 - paint
        hm = hard
    return rows, (1 - cover).astype(F32), hm.astype(np.uint8)


def commit(d, s, layer, fn, face_triangles=None, coalesce=None, base=None):
    """Run fn(state) on the layer's paint and write it back as one undo step.
    base: the State to start from (a stroke in progress keeps its own)."""
    m = d.mask(layer)
    st = base if base is not None else state_from_mask(s, m, face_triangles)
    fn(st)
    new = mask_from_state(st, m, face_triangles)
    d.mask_set(new, layer=layer, coalesce=coalesce)
    return st


# -- the viewer ------------------------------------------------------------------------------
def view_dir(ortho, cam_pos, cam_dir, point):
    """_viewDirFor: an orthographic camera looks along its axis, a perspective one from its position."""
    if ortho or cam_pos is None:
        v = np.asarray(cam_dir, float)
    else:
        v = np.asarray(point, float) - np.asarray(cam_pos, float)
    ln = np.linalg.norm(v)
    return (v / ln).tolist() if ln > 0 else [0.0, 0.0, -1.0]


def pick_hit(face_index, point, normal, face_parent=None, n_source=None):
    """A Coin pick (SoFaceDetail face index on the drawn mesh) as a stroke hit on a source triangle."""
    fi = int(face_index)
    if face_parent is not None:
        if fi < 0 or fi >= len(face_parent):
            return None
        fi = int(face_parent[fi])
    if fi < 0 or (n_source is not None and fi >= n_source):
        return None
    return {"point": [float(c) for c in point], "normal": None if normal is None else [float(c) for c in normal],
            "triangle": fi}


def ray_hit(positions, origin, direction):
    """getFrontFaceHit on the source tessellation (Möller–Trumbore): the nearest
    hit on a triangle facing the ray, as a hit; None on a miss."""
    p = np.asarray(positions, F32).astype(np.float64).reshape(-1, 3, 3)
    o = np.asarray(origin, float)
    dv = np.asarray(direction, float)
    dv = dv / (np.linalg.norm(dv) or 1)
    a, e1, e2 = p[:, 0], p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
    pv = np.cross(dv, e2)
    det = (e1 * pv).sum(1)
    with np.errstate(all="ignore"):
        inv = 1 / det
        tv = o - a
        u = (tv * pv).sum(1) * inv
        qv = np.cross(tv, e1)
        v = (qv * dv).sum(1) * inv
        t = (e2 * qv).sum(1) * inv
        n = np.cross(e1, e2)
        ok = (np.abs(det) > 1e-12) & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0) & ((n @ dv) < 0)
    if not ok.any():
        return None
    k = int(np.nonzero(ok)[0][np.argmin(t[ok])])
    nn = n[k] / (np.linalg.norm(n[k]) or 1)
    return {"point": (o + t[k] * dv).tolist(), "normal": nn.tolist(), "triangle": k}


LEFT, MIDDLE, RIGHT = 1, 2, 3


class PaintTool:
    """Viewer events to strokes on the draft's active layer.

    surface() -> (Surface, face_triangles) or None; pick(x, y) -> hit (with
    its view) or None. on_cursor(hit or None, brush, erase) draws the brush
    circle; on_hover(source triangles or None, erase) BumpMesh's hover
    preview of what a click would mark (Standard and Single brush, Fill; the
    Precision brush shows its footprint as the ring alone). Each method
    returns True when it took the event (so FreeCAD does not navigate)."""

    def __init__(self, draft, surface, pick, on_cursor=None, on_hover=None):
        self.d = draft
        self.surface = surface
        self.pick = pick
        self.on_cursor = on_cursor or (lambda *a: None)
        self.on_hover = on_hover or (lambda *a: None)
        self.stroke = None   # {"state", "prev", "erase", "tag", "layer", "ft"}
        self._n = 0
        self._hover_key = None

    def _hover(self, faces, erase):
        key = None if faces is None else (bool(erase), tuple(faces))
        if key != self._hover_key:
            self._hover_key = key
            self.on_hover(None if faces is None else list(faces), bool(erase))

    def hover_faces(self, hit, s, erase=False):
        """The source triangles a click at hit would mark (updateBrushHover,
        updateBucketHover), or None."""
        tool = self.tool()
        if hit is None or tool not in ("brush", "bucket"):
            return None
        t = int(hit["triangle"])
        if tool == "bucket":   # unchanged seed: no new flood (BumpMesh skips the BFS too)
            key = (id(s), t, float(self.d.get("bucketThreshold")))
            if getattr(self, "_fill", (None,))[0] != key:
                m = exclusion.flood(s.positions, t, key[2], adjacency=s.adj)
                self._fill = (key, sorted(m.triangles.tolist()))
            return self._fill[1]
        b = Brush.from_draft(self.d)
        if not b.circle:
            return [t]
        if b.precision:
            return None
        tree = painttree.PaintTree(s) if getattr(self, "_hover_tree", (None,))[0] is not s else self._hover_tree[1]
        self._hover_tree = (s, tree)
        return sorted(tree.faces_under_brush(t, hit["point"], b.radius, hit_view(hit, s)))

    def tool(self):
        try:
            return self.d.get("exclusionTool")
        except KeyError:
            return None

    def button(self, button, down, x, y, shift=False):
        tool = self.tool()
        if button != LEFT or tool not in ("brush", "bucket"):
            return False
        if not down:
            if self.stroke is None:
                return False
            self.stroke = None
            self.d.end_coalesce()
            return True
        got = self.surface()
        hit = self.pick(x, y) if got else None
        if hit is None:
            return False   # off the model: the drag navigates
        s, ft = got
        self._hover(None, shift)
        layer = self.d.active
        if tool == "bucket":
            ang = self.d.get("bucketThreshold")
            commit(self.d, s, layer, lambda st: fill(st, hit["triangle"], ang, shift), ft)
            return True
        self._n += 1
        brush = Brush.from_draft(self.d)
        tag = ("paint", self._n)
        st = state_from_mask(s, self.d.mask(layer), ft)
        self.stroke = {"state": st, "prev": None, "erase": bool(shift), "tag": tag, "layer": layer, "ft": ft,
                       "brush": brush, "s": s}
        self._dab(hit)
        return True

    def _dab(self, hit):
        k = self.stroke
        commit(self.d, k["s"], k["layer"], lambda st: paint_hit(st, hit, k["brush"], k["erase"], k["prev"]),
               k["ft"], coalesce=k["tag"], base=k["state"])
        k["prev"] = hit["point"]
        self.on_cursor(hit, k["brush"], k["erase"])

    def move(self, x, y, shift=False):
        tool = self.tool()
        if tool not in ("brush", "bucket"):
            return False
        if self.stroke is None:
            got = self.surface()
            hit = self.pick(x, y) if got else None
            if tool == "brush":
                self.on_cursor(hit, Brush.from_draft(self.d), bool(shift))
            self._hover(self.hover_faces(hit, got[0], shift) if got else None, shift)
            return False
        if tool != "brush":
            return False
        hit = self.pick(x, y)
        if hit is None:
            self.stroke["prev"] = None   # off the model: break the sweep
            self.on_cursor(None, self.stroke["brush"], self.stroke["erase"])
            return True
        self._dab(hit)
        return True

    def cancel(self):
        if self.stroke is not None:
            self.stroke = None
            self.d.end_coalesce()
        self.on_cursor(None, None, False)
        self._hover(None, False)
