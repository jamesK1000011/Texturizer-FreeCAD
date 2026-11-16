# SPDX-License-Identifier: AGPL-3.0-only
# Port of js/meshRepair.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# After decimation: T-junctions sealed by splitting an open edge at the open
# vertices lying on it (topology only, no geometry added or removed), needles
# dropped, and the defect counts the export reports (open and non-manifold
# edges on the 0.1 µm weld, zero-area slivers).
import numpy as np

from ._js import F32, jround
from .meshindex import first_ids, weld


def _edge_counts(ids):
    t = ids.reshape(-1, 3)
    ok = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
    t = t[ok]
    e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    e.sort(axis=1)
    _, cnt = np.unique(e, axis=0, return_counts=True)
    return cnt, len(ids) // 3


def count_edge_defects(positions, Q=1e4):
    """countEdgeDefects: {open, nonManifold, tris} on the weld grid Q."""
    ids, _, _ = weld(np.asarray(positions, F32).reshape(-1, 3).astype(np.float64), Q)
    cnt, n = _edge_counts(ids)
    return {"open": int((cnt == 1).sum()), "nonManifold": int((cnt > 2).sum()), "tris": n}


def count_area_slivers(positions):
    """countAreaSlivers: triangles with area² < 1e-24."""
    p = np.asarray(positions, F32).reshape(-1, 9).astype(np.float64)
    u = p[:, 3:6] - p[:, 0:3]
    v = p[:, 6:9] - p[:, 0:3]
    a2 = ((u[:, 1] * v[:, 2] - u[:, 2] * v[:, 1]) ** 2 + (u[:, 2] * v[:, 0] - u[:, 0] * v[:, 2]) ** 2
          + (u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]) ** 2)
    return int((a2 < 1e-24).sum())


def _open_edges(F):
    """The open (single-face) edges of faces F (n, 3): (their vertices, each
    once, in order of first appearance as the face-by-face edge walk meets the
    edges; an (n, 3) bool of which face edges are open)."""
    if len(F) == 0:
        return np.zeros(0, np.int64), np.zeros((0, 3), bool)
    E = np.stack([F, np.roll(F, -1, axis=1)], 2).reshape(-1, 2)
    lo, hi = np.minimum(E[:, 0], E[:, 1]), np.maximum(E[:, 0], E[:, 1])
    ids, firsts = first_ids(np.stack([lo, hi], 1))
    num = np.bincount(ids, minlength=len(firsts))
    open_fe = (num[ids] == 1).reshape(-1, 3)
    op = num == 1
    seq = np.stack([lo[firsts][op], hi[firsts][op]], 1).reshape(-1)
    if len(seq) == 0:
        return seq, open_fe
    _, sf = first_ids(seq)
    return seq[sf], open_fe


def resolve_t_junctions(positions, weld_quant=1e4, on_seg_tol=0.02, max_iters=16):
    """resolveTJunctions: {"position", "normal"} float32, vertices on the weld grid."""
    Q = weld_quant
    on2 = on_seg_tol * on_seg_tol
    p64 = np.asarray(positions, F32).reshape(-1, 3).astype(np.float64)
    vid, _, firsts = weld(p64, Q)
    V = jround(p64[firsts] * Q) / Q
    T = vid.reshape(-1, 3)
    a, b, c = T[:, 0], T[:, 1], T[:, 2]
    keep = (a != b) & (b != c) & (a != c)
    u = V[b] - V[a]
    w = V[c] - V[a]
    cr = np.stack([u[:, 1] * w[:, 2] - u[:, 2] * w[:, 1], u[:, 2] * w[:, 0] - u[:, 0] * w[:, 2],
                   u[:, 0] * w[:, 1] - u[:, 1] * w[:, 0]], 1)
    keep &= (cr * cr).sum(1) >= 1e-18
    faces = [list(f) for f in T[keep].tolist()]
    vx, vy, vz = V[:, 0].tolist(), V[:, 1].tolist(), V[:, 2].tolist()

    for _ in range(max_iters):
        bverts, open_fe = _open_edges(np.array(faces, np.int64).reshape(-1, 3))
        if not len(bverts):
            break
        bv = bverts
        BX, BY, BZ = V[bv, 0], V[bv, 1], V[bv, 2]
        splits = {}
        cand = np.nonzero(open_fe.any(axis=1))[0].tolist()
        open_l = open_fe.tolist()
        for fi in cand:
            f = faces[fi]
            for e in range(3):
                if not open_l[fi][e]:
                    continue
                x, y = f[e], f[(e + 1) % 3]
                ax, ay, az = vx[x], vy[x], vz[x]
                ex, ey, ez = vx[y] - ax, vy[y] - ay, vz[y] - az
                el2 = ex * ex + ey * ey + ez * ez
                if el2 < 1e-20:
                    continue
                cx, cy, cz = BX - ax, BY - ay, BZ - az
                tp = (cx * ex + cy * ey + cz * ez) / el2
                px, py, pz = cx - tp * ex, cy - tp * ey, cz - tp * ez
                hit = (bv != x) & (bv != y) & (tp > 1e-4) & (tp < 1 - 1e-4) & (px * px + py * py + pz * pz < on2)
                if hit.any():
                    idx = np.nonzero(hit)[0]
                    order = np.argsort(tp[idx], kind="stable")
                    splits[fi] = (x, y, bv[idx[order]].tolist())
                    break
        if not splits:
            break
        nxt = []
        for fi, f in enumerate(faces):
            sp = splits.get(fi)
            if sp is None:
                nxt.append(f)
                continue
            x, y, mids = sp
            apex = f[0] if (f[0] != x and f[0] != y) else (f[1] if (f[1] != x and f[1] != y) else f[2])
            dir_ab = any(f[e] == x and f[(e + 1) % 3] == y for e in range(3))
            seq = [x] + mids + [y] if dir_ab else [y] + mids[::-1] + [x]
            for s in range(len(seq) - 1):
                nxt.append([seq[s], seq[s + 1], apex])
        faces = nxt

    F = np.array(faces, np.int64).reshape(-1, 3)
    tri = V[F.reshape(-1)].reshape(-1, 9)
    u = tri[:, 3:6] - tri[:, 0:3]
    w = tri[:, 6:9] - tri[:, 0:3]
    n = np.stack([u[:, 1] * w[:, 2] - u[:, 2] * w[:, 1], u[:, 2] * w[:, 0] - u[:, 0] * w[:, 2],
                  u[:, 0] * w[:, 1] - u[:, 1] * w[:, 0]], 1)
    ln = np.sqrt(n[:, 0] * n[:, 0] + n[:, 1] * n[:, 1] + n[:, 2] * n[:, 2])
    n = n / np.where(ln == 0, 1.0, ln)[:, None]
    return {"position": tri.astype(F32).reshape(-1),
            "normal": np.repeat(n.astype(F32), 3, axis=0).reshape(-1)}


def fix_caps(positions, tol=1e-3, Q=1e5, rounds=10, flat=0.005):
    """FreeCAD's "points on edges" (MeshEvalPointOnEdge: a point within
    0.001 mm of a facet edge, in its box, not one of its corners) taken out.
    Where the point is the apex of a cap across that edge, the cap and the
    facet become the facet split at the point (the same surface, still
    closed). Where splitting can't (a chain of nearly collinear points on a
    flat patch, each split making the next cap), the point is collapsed onto
    the end of that edge it is joined to, when the mesh stays manifold
    (link condition), nothing turns over and the surface moves under flat
    (0.005 mm, the decimation's own flat tolerance). Not in BumpMesh.
    Returns (positions, how many fixed)."""
    from .untangle import closest_on_triangles, near_edges

    p = np.asarray(positions, F32).reshape(-1, 3).astype(np.float64)
    vid, _, firsts = weld(p, Q)
    V = p[firsts]
    F = vid.reshape(-1, 3).copy()
    alive = np.ones(len(F), bool)
    fixed = 0

    def nrm(ts):
        a, b, c = V[F[ts, 0]], V[F[ts, 1]], V[F[ts, 2]]
        return np.cross(b - a, c - a)

    for _ in range(rounds):
        idx = np.nonzero(alive)[0]
        hits = near_edges(V, F[idx], tol=tol)
        if not len(hits):
            break
        hits[:, 1] = idx[hits[:, 1]]
        owner, around = {}, {}
        for i in idx.tolist():
            f = F[i].tolist()
            for j in range(3):
                owner[(f[j], f[(j + 1) % 3])] = i
                around.setdefault(f[j], set()).add(i)
        done, touched = set(), set()
        for v, f, k in hits.tolist():
            if f in done or v in touched:
                continue
            g = F[f].tolist()
            a, b = g[k], g[(k + 1) % 3]
            t = owner.get((b, a))   # the facet across that edge
            if t is not None and t not in done and v in F[t].tolist():
                s_ = g[(k + 2) % 3]
                F[f], F[t] = [a, v, s_], [v, b, s_]   # facet (a, b, s) split at v; the cap (b, a, v) gone
                done.update((f, t))
                touched.update((a, b, v, s_))
                fixed += 1
                continue
            # A collinear chain: v onto the end of the edge it is joined to.
            for u in (a, b):
                both = [x for x in around.get(v, set()) & around.get(u, set()) if x not in done]
                if len(both) != 2 or touched & {u, v}:
                    continue
                ring_v = {y for x in around[v] for y in F[x].tolist()} - {v}
                ring_u = {y for x in around[u] for y in F[x].tolist()} - {u}
                if len(ring_v & ring_u) != 2:
                    continue
                moved = np.array([x for x in around[v] if x not in both and x not in done], np.int64)
                if not len(moved):
                    continue
                before = nrm(moved)
                F[moved] = np.where(F[moved] == v, u, F[moved])
                after = nrm(moved)
                _q, dist = closest_on_triangles(V[v], V[F[moved]])
                if (np.einsum("ij,ij->i", before, after) <= 0).any() or dist.min() > flat:
                    F[moved] = np.where(F[moved] == u, v, F[moved])   # undo
                    continue
                alive[both] = False
                done.update(both)
                done.update(moved.tolist())
                touched.update((u, v))
                fixed += 1
                break
        if not done:
            break
    out = V[F[alive]].astype(F32).reshape(-1)
    return out, fixed


def points_on_edges(positions, tol=1e-3, Q=1e5):
    """How many (point, facet) pairs FreeCAD's MeshEvalPointOnEdge would flag."""
    from .untangle import near_edges

    p = np.asarray(positions, F32).reshape(-1, 3).astype(np.float64)
    vid, _, firsts = weld(p, Q)
    return len(near_edges(p[firsts], vid.reshape(-1, 3), tol=tol))


def collapse_short_edges(positions, tol=1e-3, Q=1e5):
    """Edges shorter than tol (two corners FreeCAD's evaluation sees as a
    point on the other's edges) collapsed onto their first end, where the
    link condition holds (the two ends share exactly the two corners across
    the edge, so the mesh stays manifold and closed) and no triangle round
    it turns over. Not in BumpMesh. Returns (positions, how many)."""
    p = np.asarray(positions, F32).reshape(-1, 3).astype(np.float64)
    vid, _, firsts = weld(p, Q)
    V = p[firsts]
    F = vid.reshape(-1, 3).copy()
    E = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    L = np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1)
    short = E[L < tol]
    if not len(short):
        return np.asarray(positions, F32), 0
    alive = np.ones(len(F), bool)
    around = {}
    for i, f in enumerate(F.tolist()):
        for x in f:
            around.setdefault(x, set()).add(i)

    def ring(x):
        return {y for t in around.get(x, ()) if alive[t] for y in F[t].tolist()} - {x}

    def normal(t):
        a, b, c = V[F[t]]
        return np.cross(b - a, c - a)

    n = 0
    for u, v in short.tolist():
        if u == v or v not in around or u not in around:
            continue
        both = [t for t in around[u] & around[v] if alive[t]]
        if len(both) != 2 or len(ring(u) & ring(v)) != 2:
            continue   # the link condition: else the collapse would pinch the mesh
        moved = [t for t in around[v] if alive[t] and t not in both]
        before = {t: normal(t) for t in moved}
        for t in moved:
            F[t][F[t] == v] = u
        if any(np.dot(before[t], normal(t)) <= 0 for t in moved):
            for t in moved:
                F[t][F[t] == u] = v   # undo: a triangle would turn over
            continue
        for t in both:
            alive[t] = False
        around[u] |= set(moved)
        around[u] -= set(both)
        del around[v]
        n += 1
    if not n:
        return np.asarray(positions, F32), 0
    return V[F[alive]].astype(F32).reshape(-1), n
