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
    untangle(P, D, F, N)             the per-vertex scale that keeps P + D from crossing
"""
import numpy as np

ROUNDS = 6


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
    a0 = np.floor(lo / cell).astype(np.int64)
    a1 = np.floor(hi / cell).astype(np.int64)
    span = a1 - a0
    # Each triangle into every cell its box touches: most touch one or two a
    # side (the offsets 0 / 1 per axis, vectorised); bigger ones one by one.
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
    K = np.concatenate(keys)
    O = np.concatenate(owners)
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


def _touching(X, F, tris, quant=1e4):
    """Vertices of tris that landed on another vertex (the export's 0.1 µm
    weld would merge them: walls meeting at a concave corner, a
    non-manifold edge)."""
    from .meshindex import weld

    ids = np.unique(F[tris].reshape(-1))
    cell, _n, first = weld(X[ids], quant)
    count = np.bincount(cell)
    return np.isin(np.arange(len(X)), ids[count[cell] > 1])


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
        if not len(P) and not touch.any():
            break
        bad = touch.copy()
        bad[F[P.reshape(-1)].reshape(-1)] = True
        # The next rounds look only where it crossed (and round it).
        tris = np.nonzero(_grow(bad, F, 3)[F].any(1))[0]
        if r < ROUNDS:
            scale[bad] *= 0.5
        else:   # still crossing: undisplaced there, and one ring round it
            scale[_grow(bad, F, 1)] = 0.0
    return scale
