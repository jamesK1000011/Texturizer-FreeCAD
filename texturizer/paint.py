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


def surface_for(positions):
    """The Surface of a tessellation, kept while the same array is passed (the window's snapshot)."""
    key = id(positions)
    got = _CACHE.get("s")
    if got is not None and got[0] == key and got[1] is positions:
        return got[2]
    s = Surface(positions)
    _CACHE["s"] = (key, positions, s)
    return s


class State:
    """One layer's paint: hard (uint8 per triangle) and cov (float32 per welded vertex, or None)."""

    def __init__(self, surface, hard=None, cov=None):
        self.s = surface
        self.hard = np.zeros(surface.n, np.uint8) if hard is None else np.asarray(hard, np.uint8).copy()
        self.cov = None if cov is None else np.asarray(cov, F32).copy()

    def copy(self):
        return State(self.s, self.hard, self.cov)

    def _ensure_cov(self):
        if self.cov is None:
            self.cov = np.zeros(self.s.vert_count, F32)
        return self.cov

    def corners(self):
        """Paint per triangle corner (0..1): 1 on a hard triangle, else the vertex coverage."""
        p = np.zeros((self.s.n, 3), F32)
        if self.cov is not None:
            p[:] = self.cov[self.s.tri]
        p[self.hard.astype(bool)] = 1
        return p.reshape(-1)


# -- BumpMesh's geometry -------------------------------------------------------------------
def dist_sq_point_to_tri(px, py, pz, ax, ay, az, bx, by, bz, cx, cy, cz):
    """distSqPointToTri (paintTree.js): the Voronoi-region method."""
    abx, aby, abz = bx - ax, by - ay, bz - az
    acx, acy, acz = cx - ax, cy - ay, cz - az
    apx, apy, apz = px - ax, py - ay, pz - az
    d1 = abx * apx + aby * apy + abz * apz
    d2 = acx * apx + acy * apy + acz * apz
    if d1 <= 0 and d2 <= 0:
        return apx * apx + apy * apy + apz * apz
    bpx, bpy, bpz = px - bx, py - by, pz - bz
    d3 = abx * bpx + aby * bpy + abz * bpz
    d4 = acx * bpx + acy * bpy + acz * bpz
    if d3 >= 0 and d4 <= d3:
        return bpx * bpx + bpy * bpy + bpz * bpz
    cpx, cpy, cpz = px - cx, py - cy, pz - cz
    d5 = abx * cpx + aby * cpy + abz * cpz
    d6 = acx * cpx + acy * cpy + acz * cpz
    if d6 >= 0 and d5 <= d6:
        return cpx * cpx + cpy * cpy + cpz * cpz
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3)
        qx, qy, qz = ax + v * abx - px, ay + v * aby - py, az + v * abz - pz
        return qx * qx + qy * qy + qz * qz
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6)
        qx, qy, qz = ax + w * acx - px, ay + w * acy - py, az + w * acz - pz
        return qx * qx + qy * qy + qz * qz
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        qx, qy, qz = bx + w * (cx - bx) - px, by + w * (cy - by) - py, bz + w * (cz - bz) - pz
        return qx * qx + qy * qy + qz * qz
    den = 1 / (va + vb + vc)
    v, w = vb * den, vc * den
    qx, qy, qz = ax + abx * v + acx * w - px, ay + aby * v + acy * w - py, az + abz * v + acz * w - pz
    return qx * qx + qy * qy + qz * qz


def _tri_dist2_projected(s, f, px, py, pz, vx, vy, vz):
    """_triDist2Projected: point to triangle f, both projected onto the plane ⊥ view."""
    t = []
    for v in s._tl[f]:
        x, y, z = s._Vl[v]
        dx, dy, dz = x - px, y - py, z - pz
        a = dx * vx + dy * vy + dz * vz
        t += (dx - a * vx, dy - a * vy, dz - a * vz)
    return dist_sq_point_to_tri(0.0, 0.0, 0.0, *t)


def faces_in_disk(s, seed, cx, cy, cz, r2, vx, vy, vz):
    """_facesInDisk: BFS from the seed over every face reaching into the disk,
    spreading over front-facing neighbours only (the silhouette stops it)."""
    visited = {seed}
    queue = [seed]
    touched = []
    head = 0
    fn = s._fn
    while head < len(queue):
        f = queue[head]
        head += 1
        if _tri_dist2_projected(s, f, cx, cy, cz, vx, vy, vz) > r2:
            continue
        touched.append(f)
        for nb in s.neighbours[f]:
            if nb in visited:
                continue
            visited.add(nb)
            n = fn[nb]
            if n[0] * vx + n[1] * vy + n[2] * vz >= 0:
                continue
            queue.append(nb)
    return touched


def edge_limit(radius, hardness):
    """_brushEdgeLimit (main.js): r/5 for a hard brush, finer across a soft brush's fade band."""
    edge = radius / 5
    if hardness < 1:
        band = (1 - hardness) * radius
        edge = min(edge, max(band / 3, radius / 10))
    return max(0.02, edge)


# -- painting ------------------------------------------------------------------------------
def paint_faces(state, faces, erase=False):
    """paintFaces: whole triangles (Single brush, Fill); erasing also wipes soft coverage there."""
    s = state.s
    for f in faces:
        f = int(f)
        if 0 <= f < s.n:
            state.hard[f] = 0 if erase else 1
            if erase and state.cov is not None:
                state.cov[s.tri[f]] = 0


def paint_stroke(state, seed, frm, to, radius, view, hardness, erase=False, whole=None, edge=None):
    """paintStroke on an unsplit tree: one dab, or the capsule swept from frm to to.
    whole (default: hardness >= 1) paints every touched triangle in full
    (Standard); otherwise soft coverage per vertex."""
    s = state.s
    if seed < 0 or seed >= s.n:
        return
    whole = hardness >= 1 if whole is None else whole
    edge = edge_limit(radius, hardness) if edge is None else edge
    r = float(radius)
    r2 = r * r
    soft = not whole and hardness < 1
    if soft:
        state._ensure_cov()
    vx, vy, vz = (float(c) for c in view)
    tx, ty, tz = (float(c) for c in to)
    frm = to if frm is None else frm
    sx, sy, sz = float(frm[0]) - tx, float(frm[1]) - ty, float(frm[2]) - tz
    a = sx * vx + sy * vy + sz * vz
    sx, sy, sz = sx - a * vx, sy - a * vy, sz - a * vz
    seg2 = sx * sx + sy * sy + sz * sz
    seg = math.sqrt(seg2)

    def dist2(px, py, pz):
        px, py, pz = px - tx, py - ty, pz - tz
        al = px * vx + py * vy + pz * vz
        px, py, pz = px - al * vx, py - al * vy, pz - al * vz
        u = (px * sx + py * sy + pz * sz) / seg2 if seg2 > 0 else 0
        u = 0 if u < 0 else 1 if u > 1 else u
        dx, dy, dz = px - u * sx, py - u * sy, pz - u * sz
        return dx * dx + dy * dy + dz * dz

    n_dabs = max(1, math.ceil(seg / max(r * 0.5, 1e-6)) + 1)
    dabs = []
    for i in range(n_dabs):
        u = 0 if n_dabs == 1 else i / (n_dabs - 1)
        dabs.append((tx + sx * u, ty + sy * u, tz + sz * u))

    def stroke_dist2(f):
        return min(_tri_dist2_projected(s, f, x, y, z, vx, vy, vz) for x, y, z in dabs)

    r_test = r + edge if soft else r
    reach = r_test + seg * 0.5
    touched = faces_in_disk(s, int(seed), tx + sx * 0.5, ty + sy * 0.5, tz + sz * 0.5, reach * reach, vx, vy, vz)
    if whole:
        paint_faces(state, [f for f in touched if stroke_dist2(f) <= r2], erase)
        return
    rt2 = r_test * r_test
    core2 = (r * hardness) ** 2
    V = s._Vl
    for f in touched:
        vs = s._tl[f]
        d = [dist2(*V[v]) for v in vs]
        inside = sum(1 for x in d if x <= rt2)
        if inside == 3 and (not soft or all(x <= core2 for x in d)):
            # _paintSubtree: fully inside (the hard core when soft)
            if soft:
                state.cov[vs] = 0 if erase else 1
                if erase:
                    state.hard[f] = 0
            else:
                state.hard[f] = 0 if erase else 1
                if erase and state.cov is not None:
                    state.cov[vs] = 0
            continue
        if inside == 0 and stroke_dist2(f) > rt2:
            continue
        # _paintLeaf
        cov = state.cov
        if soft:
            if erase and state.hard[f]:
                state.hard[f] = 0
                cov[vs] = 1
            for k in range(3):
                if d[k] > r2:
                    continue
                c = brush_coverage(math.sqrt(d[k]), r, hardness)
                v = vs[k]
                if erase:
                    if 1 - c < cov[v]:
                        cov[v] = 1 - c
                elif c > cov[v]:
                    cov[v] = c
        else:
            p = np.mean([V[v] for v in vs], axis=0)
            if dist2(*p) <= r2:
                state.hard[f] = 0 if erase else 1
                if erase and cov is not None:
                    cov[vs] = 0


class Brush:
    """The sidebar's Brush rows: circle (Type), size (diameter, mm), hardness (%)."""

    def __init__(self, circle=True, size=10.0, hardness=50.0, mode="standard"):
        if mode != "standard":
            raise ValueError("Mode: only Standard for now (Precision comes later).")
        self.circle = bool(circle)
        self.size = max(MIN_SIZE, min(MAX_SIZE, float(size)))
        self.hardness = max(0.0, min(100.0, float(hardness)))
        self.mode = mode

    @classmethod
    def from_draft(cls, d):
        return cls(d.get("brushIsRadius"), d.get("brushSize"), d.get("brushHardness"))

    @property
    def radius(self):
        return self.size / 2

    @property
    def h(self):
        return self.hardness / 100.0


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
        paint_stroke(state, t, prev, hit["point"], brush.radius, hit_view(hit, s), brush.h, erase)
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
    """The soft paint as {corners, values} (corners with coverage > 0), or None."""
    if state.cov is None:
        return None
    c = state.cov[state.s.vert_id]
    idx = np.nonzero(c > 0)[0]
    if not len(idx):
        return None
    return {"corners": idx.tolist(), "values": [float(x) for x in c[idx]]}


def state_from_mask(s, mask, face_triangles=None):
    """A State from a draft mask (its B-rep faces count as hard triangles)."""
    st = State(s)
    tris = [int(t) for t in mask.get("triangles") or []]
    for f in mask.get("faces") or []:
        tris.extend(int(t) for t in (face_triangles or {}).get(f, []))
    paint_faces(st, [t for t in tris if 0 <= t < s.n])
    soft = mask.get("soft")
    if soft and soft.get("corners"):
        cov = st._ensure_cov()
        c = np.asarray(soft["corners"], np.int64)
        v = np.asarray(soft["values"], F32)
        ok = (c >= 0) & (c < s.n * 3)
        np.maximum.at(cov, s.vert_id[c[ok]], v[ok])
    return st


def mask_from_state(state, mask, face_triangles=None):
    """The draft mask for a State: a B-rep face stays a face while all its
    triangles are painted; one the brush cut into becomes its triangles."""
    hard = set(np.nonzero(state.hard)[0].tolist())
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
    return out


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
    circle. Each method returns True when it took the event (so FreeCAD does
    not navigate)."""

    def __init__(self, draft, surface, pick, on_cursor=None):
        self.d = draft
        self.surface = surface
        self.pick = pick
        self.on_cursor = on_cursor or (lambda *a: None)
        self.stroke = None   # {"state", "prev", "erase", "tag", "layer", "ft"}
        self._n = 0

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
        if tool != "brush":
            return False
        if self.stroke is None:
            hit = self.pick(x, y) if self.surface() else None
            self.on_cursor(hit, Brush.from_draft(self.d), bool(shift))
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
