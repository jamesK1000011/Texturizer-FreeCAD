# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, which ports the engine of
# BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""Displaced walls that cross, kept apart (not in BumpMesh).

Where the surface is concave (a hole's wall, the inner corner of a pocket,
a hole's facets meeting at a polygon corner) displacement moves points
towards each other; texture heights that differ between neighbours, and a
facet's corner that lags the facets beside it, make triangles cross. On a
40 x 30 x 20 box with a 3.6 mm hole FreeCAD's Mesh evaluation found 21
self-intersecting pairs. This finds the crossing pairs among the triangles
of the concave region and pulls the displacement of their vertices back
(halved a round, gone after the last), the way BumpMesh's fold guard does
at released creases, until none cross. Convex and flat regions, where
displacement cannot cross, are never touched, so BumpMesh's result there is
kept exactly.

    concave(P, F, N)                 vertices whose neighbours rise along their normal
    crossings(T, among=None)         pairs of triangles that cross (sharing no vertex)
    _touching(X, F, tris)            vertices that landed on another (a non-manifold edge)
    near_edges(V, F, tris)           FreeCAD's "points on edges" (MeshEvalPointOnEdge, 0.001 mm)
    untangle(P, D, F, N)             the per-vertex scale that keeps P + D from crossing
"""
import numpy as np

ROUNDS = 6
MARGIN = 0.01   # mm: how near two displaced walls may come (FreeCAD flags 0.001; decimation moves vertices after)


def _edges(F):
    E = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    return np.concatenate([E, E[:, ::-1]])


def concave(V, F, N, rel=1e-3):
    """(nv,) bool: a vertex with a neighbour above its tangent plane (along
    its normal N, by more than rel of the edge): the surface curves towards
    where it is displaced. Flat faces (rounding only) and convex ones are not."""
    E = _edges(F)
    a, b = E[:, 0], E[:, 1]
    e = V[b] - V[a]
    up = (e * N[a]).sum(1) > rel * np.sqrt((e * e).sum(1))
    out = np.zeros(len(V), bool)
    out[a[up]] = True
    return out


def _grow(mask, F, rings=1):
    E = _edges(F)
    for _ in range(rings):
        nxt = mask.copy()
        nxt[E[mask[E[:, 0]], 1]] = True
        mask = nxt
    return mask


def _cells(lo, hi, cell):
    """(cell keys, owner index): each box in every cell of size cell it
    touches; most touch one or two a side (vectorised), bigger ones one by one."""
    a0 = np.floor(lo / cell).astype(np.int64)
    a1 = np.floor(hi / cell).astype(np.int64)
    span = a1 - a0
    keys, owners = [], []
    small = (span <= 1).all(1)
    ii = np.nonzero(small)[0]
    for ox in (0, 1):
        for oy in (0, 1):
            for oz in (0, 1):
                off = np.array([ox, oy, oz])
                ok = (span[ii] >= off).all(1)
                keys.append(a0[ii[ok]] + off)
                owners.append(ii[ok])
    for i in np.nonzero(~small)[0].tolist():
        g = np.stack(np.meshgrid(*[np.arange(a0[i, k], a1[i, k] + 1) for k in range(3)], indexing="ij"), -1).reshape(-1, 3)
        keys.append(g)
        owners.append(np.full(len(g), i))
    return np.concatenate(keys), np.concatenate(owners)


def near_edges(V, F, tris=None, tol=1e-3):
    """FreeCAD's Mesh evaluation "points on edges" (MeshEvalPointOnEdge): a
    vertex that is not a corner of a facet, inside the bounding box of one
    of the facet's edges and within tol (FreeCAD's 0.001) of it. Returns
    (n, 3) rows (vertex, facet, edge k: corners k and k+1). tris: only
    those facets (and the vertices near them)."""
    V = np.asarray(V, np.float64)
    tris = np.arange(len(F)) if tris is None else np.asarray(tris, np.int64)
    if not len(tris):
        return np.zeros((0, 3), np.int64)
    T = V[F[tris]]
    lo, hi = T.min(1) - tol, T.max(1) + tol
    cell = max(2.0 * float(np.median((hi - lo).max(1))), 1e-6)
    tk, to = _cells(lo, hi, cell)
    vk = np.floor(V / cell).astype(np.int64)
    from .meshindex import first_ids

    ids, _f = first_ids(np.concatenate([tk, vk]))
    tid, vid = ids[:len(tk)], ids[len(tk):]
    # Every (facet entry, vertex) sharing a cell, without a loop over cells.
    order = np.argsort(vid, kind="stable")
    vs = vid[order]
    lo_i = np.searchsorted(vs, tid, "left")
    nv = np.searchsorted(vs, tid, "right") - lo_i
    if not nv.sum():
        return np.zeros((0, 3), np.int64)
    rep = np.repeat(np.arange(len(tid)), nv)
    start = np.repeat(np.cumsum(nv) - nv, nv)
    v = order[lo_i[rep] + (np.arange(len(rep)) - start)]
    fl = to[rep]
    # In the facet's box (and tol); a vertex sits in one cell, so no pair twice.
    pv = V[v]
    inb = ((pv >= lo[fl]) & (pv <= hi[fl])).all(1)
    v, f = v[inb], tris[fl[inb]]
    keep = (F[f] != v[:, None]).all(1)
    v, f = v[keep], f[keep]
    out = []
    p = V[v]
    for k in range(3):
        a, b = V[F[f, k]], V[F[f, (k + 1) % 3]]
        inbox = ((p >= np.minimum(a, b)) & (p <= np.maximum(a, b))).all(1)
        ab = b - a
        L2 = (ab * ab).sum(1)
        t = ((p - a) * ab).sum(1) / np.maximum(L2, 1e-300)
        d = np.linalg.norm(a + ab * t[:, None] - p, axis=1)
        hit = inbox & (d <= tol)
        out.append(np.stack([v[hit], f[hit], np.full(int(hit.sum()), k)], 1))
    return np.concatenate(out) if out else np.zeros((0, 3), np.int64)


def _candidate_pairs(T, idx):
    """Triangle pairs among idx whose boxes overlap: a grid of cells about
    twice the usual triangle's size, each triangle in every cell its box
    touches."""
    if len(idx) < 2:
        return np.zeros((0, 2), np.int64)
    S = T[idx]
    lo, hi = S.min(1), S.max(1)
    ext = (hi - lo).max(1)
    cell = max(2.0 * float(np.median(ext)), 1e-6)
    K, O = _cells(lo, hi, cell)
    from .meshindex import first_ids

    cid, _f = first_ids(K)
    order = np.argsort(cid, kind="stable")
    cs, os_ = cid[order], O[order]
    starts = np.nonzero(np.r_[True, cs[1:] != cs[:-1]])[0]
    ends = np.r_[starts[1:], len(cs)]
    out = []
    for a, b in zip(starts.tolist(), ends.tolist()):
        if b - a < 2:
            continue
        m = os_[a:b]
        I, J = np.triu_indices(len(m), 1)
        out.append(np.stack([np.minimum(m[I], m[J]), np.maximum(m[I], m[J])], 1))
    if not out:
        return np.zeros((0, 2), np.int64)
    n = len(idx)
    u = np.unique(np.concatenate(out) @ np.array([n, 1], np.int64))   # one int per pair: a fast unique
    P = np.stack([u // n, u % n], 1)
    ok = (lo[P[:, 0]] <= hi[P[:, 1]]).all(1) & (lo[P[:, 1]] <= hi[P[:, 0]]).all(1)
    P = P[ok]
    return np.stack([idx[P[:, 0]], idx[P[:, 1]]], 1)


@np.errstate(all="ignore")
def _seg_through(T, S):
    """For pairs (k): does an edge of S[k] pass through the inside of T[k]?"""
    a, b, c = T[:, 0], T[:, 1], T[:, 2]
    n = np.cross(b - a, c - a)
    ln2 = (n * n).sum(1)
    hit = np.zeros(len(T), bool)
    for i, j in ((0, 1), (1, 2), (2, 0)):
        p, q = S[:, i], S[:, j]
        dp, dq = ((p - a) * n).sum(1), ((q - a) * n).sum(1)
        cross_ = dp * dq < 0
        t = dp / (dp - dq)
        x = p + (q - p) * t[:, None]
        u = (np.cross(b - a, x - a) * n).sum(1)
        v = (np.cross(c - b, x - b) * n).sum(1)
        w = (np.cross(a - c, x - c) * n).sum(1)
        tol = 1e-9 * ln2
        hit |= cross_ & (u > tol) & (v > tol) & (w > tol) & (ln2 > 1e-24)
    return hit


def crossings(T, F=None, among=None):
    """Pairs [i, j] of triangles of T (n, 3, 3) that cross; triangles that
    share a vertex (F: welded corner ids) are neighbours, not crossings.
    among: only pairs with at least one triangle in it... both in it."""
    T = np.asarray(T, np.float64).reshape(-1, 3, 3)
    if F is None:
        from .meshindex import weld

        F = weld(T.reshape(-1, 3), 1e5)[0].reshape(-1, 3)
    idx = np.arange(len(T)) if among is None else np.asarray(among, np.int64)
    P = _candidate_pairs(T, idx)
    if not len(P):
        return P
    share = (F[P[:, 0]][:, :, None] == F[P[:, 1]][:, None, :]).any((1, 2))
    P = P[~share]
    A, B = T[P[:, 0]], T[P[:, 1]]
    hit = _seg_through(A, B) | _seg_through(B, A)
    return P[hit]


def closest_on_triangles(p, T):
    """The closest point to p on each triangle of T (m, 3, 3) (Ericson's
    regions), and its distance."""
    a, b, c = T[:, 0], T[:, 1], T[:, 2]
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = (ab * ap).sum(1), (ac * ap).sum(1)
    bp = p - b
    d3, d4 = (ab * bp).sum(1), (ac * bp).sum(1)
    cp = p - c
    d5, d6 = (ab * cp).sum(1), (ac * cp).sum(1)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    with np.errstate(all="ignore"):
        den = 1 / (va + vb + vc)
        v, w = vb * den, vc * den
        out = a + ab * v[:, None] + ac * w[:, None]
        # Edges and corners.
        r_ab = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
        t = d1 / (d1 - d3)
        out = np.where(r_ab[:, None], a + ab * t[:, None], out)
        r_ac = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
        t = d2 / (d2 - d6)
        out = np.where(r_ac[:, None], a + ac * t[:, None], out)
        r_bc = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)
        t = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        out = np.where(r_bc[:, None], b + (c - b) * t[:, None], out)
    out = np.where(((d1 <= 0) & (d2 <= 0))[:, None], a, out)
    out = np.where(((d3 >= 0) & (d4 <= d3))[:, None], b, out)
    out = np.where(((d6 >= 0) & (d5 <= d6))[:, None], c, out)
    return out, np.linalg.norm(out - p, axis=1)


def _close_pairs(P, r):
    """Pairs of rows of P (m, 3) nearer than r."""
    try:
        from scipy.spatial import cKDTree

        return np.asarray(sorted(cKDTree(P).query_pairs(r)), np.int64).reshape(-1, 2)
    except ImportError:
        out = []
        step = 2048
        for a in range(0, len(P), step):
            d = np.linalg.norm(P[a:a + step, None, :] - P[None, :, :], axis=2)
            i, j = np.nonzero(d < r)
            i = i + a
            keep = i < j
            out.append(np.stack([i[keep], j[keep]], 1))
        return np.concatenate(out) if out else np.zeros((0, 2), np.int64)


def _touching(X, F, tris, r=None):
    """Vertices of tris nearer than r (MARGIN) to another vertex they share
    no triangle with: two walls meeting (a non-manifold edge once welded, or
    FreeCAD's points on edges once the decimation moves a corner)."""
    r = MARGIN if r is None else r
    ids = np.unique(F[tris].reshape(-1))
    out = np.zeros(len(X), bool)
    pr = _close_pairs(X[ids], r)
    if not len(pr):
        return out
    a, b = ids[pr[:, 0]], ids[pr[:, 1]]
    # Neighbours (a shared triangle) may be close: a short edge, not a touch.
    E = _edges(F[tris])
    near = set(zip(E[:, 0].tolist(), E[:, 1].tolist()))
    keep = np.array([(x, y) not in near for x, y in zip(a.tolist(), b.tolist())], bool)
    out[a[keep]] = True
    out[b[keep]] = True
    return out


def untangle(V, D, F, N, also=None):
    """(nv,) scale per welded vertex for its displacement D, 1 where nothing
    crosses: the triangles of the concave region and of also (the sharp
    creases, whose mitres move vertices apart), two rings round them, that
    cross have their vertices' displacement halved, round by round, and
    taken away after the last."""
    nv = len(V)
    scale = np.ones(nv)
    if not len(F):
        return scale
    region = concave(V, F, N)
    if also is not None:
        region |= also
    region = _grow(region, F, 2)
    tris = np.nonzero(region[F].any(1))[0]
    if not len(tris):
        return scale
    for r in range(ROUNDS + 2):
        X = V + D * scale[:, None]
        P = crossings(X[F], F, among=tris)
        touch = _touching(X, F, tris)
        # Walls nearer than MARGIN: FreeCAD's points on edges (0.001 mm), with
        # room for the decimation that follows to move a vertex a little.
        ne = near_edges(X, F, tris, tol=MARGIN)
        if not len(P) and not touch.any() and not len(ne):
            break
        bad = touch.copy()
        bad[F[P.reshape(-1)].reshape(-1)] = True
        if len(ne):
            bad[ne[:, 0]] = True
            bad[F[ne[:, 1]].reshape(-1)] = True
        # The next rounds look only where it crossed (and round it).
        tris = np.nonzero(_grow(bad, F, 3)[F].any(1))[0]
        if r < ROUNDS:
            scale[bad] *= 0.5
        else:   # still crossing: undisplaced there, and one ring round it
            scale[_grow(bad, F, 1)] = 0.0
    return scale
