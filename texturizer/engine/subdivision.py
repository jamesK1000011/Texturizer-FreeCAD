# SPDX-License-Identifier: AGPL-3.0-only
# Port of js/subdivision.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# Edge-based adaptive subdivision until every edge is at most maxEdgeLength:
# edges over it are marked globally (so both sides of an edge agree), and each
# triangle splits into 2, 3 or 4 by how many of its edges are marked. The
# vertices are welded first (accurate mode: split at sharp dihedrals, 30°),
# and a midpoint takes the averaged normal of its two ends.
#
# Vectorised: a pass is array work. The vertex numbering differs from the JS
# (the output is non-indexed, so it never shows); which corners share a
# vertex, every position and every normal are the JS's.
import math

import numpy as np

from ._js import F32
from .meshindex import PointMap, first_ids, quantize

QUANTISE = 1e5
SAFETY_CAP = 16_000_000
SHARP_COS = math.cos(30 * math.pi / 180)
MAX_ITERATIONS = 12


class _Verts:
    """The growable vertex store: pos, nrm (float64), wgt, canon (cell id)."""

    def __init__(self, pos, nrm, wgt, canon):
        self.pos, self.nrm, self.wgt, self.canon = pos, nrm, wgt, canon


def _normalize_rows(v):
    ln = np.sqrt(v[:, 0] * v[:, 0] + v[:, 1] * v[:, 1] + v[:, 2] * v[:, 2])
    ln = np.where(ln == 0, 1.0, ln)
    return v / ln[:, None]


def to_indexed_fast(pos32, nrm32, weights):
    """toIndexedFast: plain position merge (preview path)."""
    p = pos32.reshape(-1, 3).astype(np.float64)
    n = len(p)
    ids, firsts = first_ids(quantize(p, QUANTISE))
    m = len(firsts)
    if nrm32 is not None:
        nr = nrm32.reshape(-1, 3).astype(np.float64)
    else:
        nr = np.zeros((n, 3))
        nr[:, 2] = 1
    acc = np.stack([np.bincount(ids, nr[:, k], m) for k in range(3)], 1)
    wgt = None
    if weights is not None:
        wgt = np.full(m, -np.inf)
        np.maximum.at(wgt, ids, np.asarray(weights, np.float64))
    return _Verts(p[firsts].copy(), _normalize_rows(acc), wgt, None), ids.astype(np.int64), None


def to_indexed(pos32, weights):
    """toIndexed: merge positions, split at sharp dihedral edges (30°) by
    smooth-group clusters whose representative follows the running average."""
    P = pos32.reshape(-1, 3).astype(np.float64)
    n = len(P)
    T = P.reshape(-1, 9)
    e1 = T[:, 3:6] - T[:, 0:3]
    e2 = T[:, 6:9] - T[:, 0:3]
    r = np.empty((len(T), 3))
    r[:, 0] = e1[:, 1] * e2[:, 2] - e1[:, 2] * e2[:, 1]
    r[:, 1] = e1[:, 2] * e2[:, 0] - e1[:, 0] * e2[:, 2]
    r[:, 2] = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
    ln = np.sqrt(r[:, 0] * r[:, 0] + r[:, 1] * r[:, 1] + r[:, 2] * r[:, 2])
    ln = np.where(ln == 0, 1.0, ln)
    fu = np.repeat((r / ln[:, None]).astype(F32), 3, axis=0).astype(np.float64)
    fr = np.repeat(r.astype(F32), 3, axis=0).astype(np.float64)
    cmap = PointMap(QUANTISE)
    cells, _new = cmap.get_or_set(P, np.arange(n))
    cmap.next = n
    gid, gfirst = first_ids(cells[:, None])
    ng = len(gfirst)
    # Fast path: every corner of a cell within 15° of the cell's first corner
    # makes one cluster, whatever the order (the running average stays within
    # 15° of all of them, so each is within 30° of it when it is tested).
    dots = (fu * fu[gfirst][gid]).sum(1)
    loose = np.zeros(ng, bool)
    loose[gid[dots < math.cos(14 * math.pi / 180)]] = True
    vert = np.empty(n, np.int64)
    simple = ~loose[gid]
    # Vertex per simple cell: its cell's index; slow cells get theirs after.
    vert[simple] = gid[simple]
    nv = ng
    extra_pos, extra_canon = [], []
    slow = np.nonzero(~simple)[0]
    if len(slow):
        clusters = {}
        fl = fu[slow].tolist()
        for k, i in enumerate(slow.tolist()):
            gi = int(gid[i])
            ux, uy, uz = fl[k]
            cl = clusters.get(gi)
            if cl is None:
                clusters[gi] = [[gi, ux, uy, uz]]
                vert[i] = gi
                continue
            for c in cl:
                if c[1] * ux + c[2] * uy + c[3] * uz >= SHARP_COS:
                    c[1] += ux
                    c[2] += uy
                    c[3] += uz
                    rl = math.sqrt(c[1] * c[1] + c[2] * c[2] + c[3] * c[3]) or 1
                    c[1] /= rl
                    c[2] /= rl
                    c[3] /= rl
                    vert[i] = c[0]
                    break
            else:
                cl.append([nv, ux, uy, uz])
                vert[i] = nv
                extra_pos.append(i)
                extra_canon.append(gi)
                nv += 1
    first_corner = np.concatenate([gfirst, np.array(extra_pos, np.int64)])
    canon_of = np.concatenate([cells[gfirst], cells[gfirst][np.array(extra_canon, np.int64)]]) if extra_canon else cells[gfirst]
    acc = np.stack([np.bincount(vert, fr[:, k], nv) for k in range(3)], 1)
    wgt = None
    if weights is not None:
        wgt = np.full(nv, -np.inf)
        np.maximum.at(wgt, vert, np.asarray(weights, np.float64))
    verts = _Verts(P[first_corner].copy(), _normalize_rows(acc), wgt, canon_of.astype(np.int64))
    return verts, vert, cmap


def _edge_len_sq(pos, a, b):
    d = pos[a] - pos[b]
    return d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1] + d[:, 2] * d[:, 2]


def _key(u, v):
    lo, hi = np.minimum(u, v), np.maximum(u, v)
    return lo * (1 << 31) + hi


def _pass(verts, ind, max_edge, cap, face_excl, cmap, parents):
    """One subdivision pass: (indices, faceExcluded, parents, changed, capped)."""
    max_sq = max_edge * max_edge
    pos, canon = verts.pos, verts.canon
    a, b, c = ind[:, 0], ind[:, 1], ind[:, 2]
    cv = (lambda x: canon[x]) if canon is not None else (lambda x: x)
    active = np.ones(len(ind), bool)
    if face_excl is not None:
        touches = np.zeros(int(max(cv(np.arange(len(pos))).max(), len(pos))) + 1, bool) if len(pos) else np.zeros(1, bool)
        inc = face_excl == 0
        touches[cv(ind[inc].reshape(-1))] = True
        near = touches[cv(a)] | touches[cv(b)] | touches[cv(c)]
        active = ~(face_excl.astype(bool) & ~near)
    lab, lbc, lca = _edge_len_sq(pos, a, b), _edge_len_sq(pos, b, c), _edge_len_sq(pos, c, a)
    marks = []
    for (u, v, ln) in ((a, b, lab), (b, c, lbc), (c, a, lca)):
        sel = active & (ln > max_sq)
        marks.append(_key(cv(u[sel]), cv(v[sel])))
    marked = np.unique(np.concatenate(marks))
    if not len(marked):
        return ind, face_excl, parents, False, False
    sab = np.isin(_key(cv(a), cv(b)), marked)
    sbc = np.isin(_key(cv(b), cv(c)), marked)
    sca = np.isin(_key(cv(c), cv(a)), marked)
    nm = sab.astype(np.int64) + sbc + sca
    per = np.where(nm == 0, 1, nm + 1)
    total = int(per.sum())
    if total > cap:
        return ind, face_excl, parents, False, True
    # Midpoints, one per raw (unordered) vertex pair.
    pairs = np.concatenate([np.stack([a[sab], b[sab]], 1), np.stack([b[sbc], c[sbc]], 1), np.stack([c[sca], a[sca]], 1)])
    lo, hi = np.minimum(pairs[:, 0], pairs[:, 1]), np.maximum(pairs[:, 0], pairs[:, 1])
    pk = lo * (1 << 31) + hi
    uk, ui = np.unique(pk, return_index=True)
    ulo, uhi = lo[ui], hi[ui]
    base = len(pos)
    mp = (pos[ulo] + pos[uhi]) / 2
    nn = verts.nrm[ulo] + verts.nrm[uhi]
    nl = np.sqrt(nn[:, 0] * nn[:, 0] + nn[:, 1] * nn[:, 1] + nn[:, 2] * nn[:, 2])
    nl = np.where(nl == 0, 1.0, nl)
    verts.pos = np.concatenate([pos, mp])
    verts.nrm = np.concatenate([verts.nrm, nn / nl[:, None]])
    if verts.wgt is not None:
        verts.wgt = np.concatenate([verts.wgt, (verts.wgt[ulo] + verts.wgt[uhi]) / 2])
    if canon is not None:
        cells, _ = cmap.get_or_set(mp, np.arange(cmap.next, cmap.next + len(mp)))
        cmap.next += len(mp)
        verts.canon = np.concatenate([canon, cells])

    def mid(u, v):
        k = _key(u, v)
        return base + np.searchsorted(uk, k)

    off = np.concatenate([[0], np.cumsum(per)[:-1]])
    out = np.empty((total, 3), np.int64)
    # n == 0
    s = nm == 0
    out[off[s]] = ind[s]
    # n == 3
    s = nm == 3
    if s.any():
        A, B, Cc = a[s], b[s], c[s]
        mab, mbc, mca = mid(A, B), mid(B, Cc), mid(Cc, A)
        o = off[s]
        out[o] = np.stack([A, mab, mca], 1)
        out[o + 1] = np.stack([mab, B, mbc], 1)
        out[o + 2] = np.stack([mca, mbc, Cc], 1)
        out[o + 3] = np.stack([mab, mbc, mca], 1)
    # n == 1
    for sel, case in ((sab & (nm == 1), 0), (sbc & (nm == 1), 1), (sca & (nm == 1), 2)):
        if not sel.any():
            continue
        A, B, Cc, o = a[sel], b[sel], c[sel], off[sel]
        if case == 0:
            m = mid(A, B)
            out[o] = np.stack([A, m, Cc], 1)
            out[o + 1] = np.stack([m, B, Cc], 1)
        elif case == 1:
            m = mid(B, Cc)
            out[o] = np.stack([A, B, m], 1)
            out[o + 1] = np.stack([A, m, Cc], 1)
        else:
            m = mid(Cc, A)
            out[o] = np.stack([A, B, m], 1)
            out[o + 1] = np.stack([m, B, Cc], 1)
    # n == 2
    two = nm == 2
    for sel, case in ((two & ~sab, 0), (two & sab & ~sbc, 1), (two & sab & sbc, 2)):
        if not sel.any():
            continue
        A, B, Cc, o = a[sel], b[sel], c[sel], off[sel]
        if case == 0:
            mbc, mca = mid(B, Cc), mid(Cc, A)
            out[o] = np.stack([A, B, mbc], 1)
            out[o + 1] = np.stack([A, mbc, mca], 1)
            out[o + 2] = np.stack([Cc, mca, mbc], 1)
        elif case == 1:
            mab, mca = mid(A, B), mid(Cc, A)
            out[o] = np.stack([A, mab, mca], 1)
            out[o + 1] = np.stack([mab, B, Cc], 1)
            out[o + 2] = np.stack([mab, Cc, mca], 1)
        else:
            mab, mbc = mid(A, B), mid(B, Cc)
            out[o] = np.stack([B, mbc, mab], 1)
            out[o + 1] = np.stack([A, mab, mbc], 1)
            out[o + 2] = np.stack([A, mbc, Cc], 1)
    nfe = np.repeat(face_excl, per) if face_excl is not None else None
    npar = np.repeat(parents, per) if parents is not None else None
    return out, nfe, npar, True, False


def subdivide(positions, max_edge, on_progress=None, face_weights=None, fast=False, normals=None, should_abort=None):
    """subdivide(geometry, maxEdgeLength, onProgress, faceWeights, {fast}):
    {"position", "normal", "excludeWeight" (or None), "safetyCapHit", "faceParentId"}."""
    pos32 = np.asarray(positions, F32).reshape(-1)
    fw = None if face_weights is None else np.asarray(face_weights, F32)
    init_excl = None
    if fw is not None:
        init_excl = (fw[0::3] > 0.99).astype(np.uint8)
    if fast:
        verts, ids, cmap = to_indexed_fast(pos32, None if normals is None else np.asarray(normals, F32), fw)
    else:
        verts, ids, cmap = to_indexed(pos32, fw)
    ind = ids.reshape(-1, 3)
    excl = init_excl
    parents = np.arange(len(ind), dtype=np.int64)
    cap_hit = False
    for it in range(MAX_ITERATIONS):
        if len(ind) >= SAFETY_CAP:
            cap_hit = True
            break
        ind, nexcl, npar, changed, capped = _pass(verts, ind, max_edge, SAFETY_CAP, excl, cmap, parents)
        if nexcl is not None:
            excl = nexcl
        parents = npar
        if capped or len(ind) >= SAFETY_CAP:
            cap_hit = True
        if on_progress:
            p = verts.pos
            ls = np.concatenate([_edge_len_sq(p, ind[:, 0], ind[:, 1]), _edge_len_sq(p, ind[:, 1], ind[:, 2]), _edge_len_sq(p, ind[:, 2], ind[:, 0])])
            on_progress(min(0.95, (it + 1) / MAX_ITERATIONS), len(ind), math.sqrt(float(ls.max())) if len(ls) else 0.0)
        if should_abort and should_abort():
            return None
        if not changed or cap_hit:
            break
    return _to_non_indexed(verts, ind, excl, cap_hit, parents)


def _to_non_indexed(verts, ind, excl, cap_hit, parents):
    flat = ind.reshape(-1)
    pos = verts.pos[flat].astype(F32).reshape(-1)
    nrm = verts.nrm[flat].astype(F32).reshape(-1)
    w = None
    if excl is not None:
        w = np.repeat(excl.astype(F32), 3)
    elif verts.wgt is not None:
        w = verts.wgt[flat].astype(F32)
    return {"position": pos, "normal": nrm, "excludeWeight": w, "safetyCapHit": cap_hit,
            "faceParentId": parents.astype(np.int32)}
