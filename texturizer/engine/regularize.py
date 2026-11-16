# SPDX-License-Identifier: AGPL-3.0-only
# Port of js/regularize.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# Sliver clean-up after subdivision: thin triangles (thinness lmax/hmin above
# aspectThreshold) collapse an edge to its midpoint when the collapse keeps
# every surviving neighbour's edges under maxEdgeLength x slack, its normal
# within the cap of its ORIGINAL normal, its plane within maxPlaneDev, and the
# link condition. Sharp-edge vertices (and with preserveExcluded every vertex
# of an excluded face) are frozen. Rounds until nothing collapses.
#
# The scan and the gates are the JS's; the per-collapse bookkeeping keeps a
# set of incident corners per vertex (the JS's linked lists), which gives the
# same collapses: every gate is all-or-nothing, so order within a list never
# changes an outcome. That is regularize_sequential, kept as the reference;
# regularize_mesh (below) makes the same collapses in batches.
import math

import numpy as np

from ._js import F32, Geometry
from .meshindex import first_ids, quantize, weld

QUANTISE = 1e5


def regularize_sequential(positions, face_parent_id, max_edge, opts=None, exclude_weight=None, should_abort=None):
    """regularizeMesh one collapse at a time, as the JS runs it (the
    reference regularize_mesh is checked against): {"position", "normal",
    "excludeWeight", "faceParentId", "collapseCount", "rejectStats"}; None
    when should_abort() turned true (checked every 1024 candidates)."""
    o = opts or {}
    aspect_th = o.get("aspectThreshold", 5)
    slack = o.get("slack", 3.0)
    agg_slack = o.get("aggressiveSlack", 8.0)
    extreme = o.get("extremeSliverAspect", 8)
    max_ncos = o.get("maxNormalDeltaCos", math.cos(15 * math.pi / 180))
    agg_ncos = o.get("aggressiveNormalDeltaCos", math.cos(25 * math.pi / 180))
    max_plane = o.get("maxPlaneDev", 0.03)
    sharp_cos = o.get("sharpEdgeCos", math.cos(30 * math.pi / 180))
    max_rounds = o.get("maxRounds", 8)
    base_len = (max_edge * slack) * (max_edge * slack)
    agg_len = (max_edge * agg_slack) * (max_edge * agg_slack)
    ext2 = extreme * extreme

    pa = np.asarray(positions, F32).reshape(-1, 3)
    tri_count = len(pa) // 3
    ids, nverts, firsts = weld(pa.astype(np.float64), QUANTISE)
    V = pa[firsts].astype(np.float64)
    X, Y, Z = V[:, 0].tolist(), V[:, 1].tolist(), V[:, 2].tolist()
    corners = ids.tolist()
    C = ids.reshape(-1, 3)

    def face_normals_np(Cx):
        A = V[Cx[:, 0]]
        e1 = V[Cx[:, 1]] - A
        e2 = V[Cx[:, 2]] - A
        nx = e1[:, 1] * e2[:, 2] - e1[:, 2] * e2[:, 1]
        ny = e1[:, 2] * e2[:, 0] - e1[:, 0] * e2[:, 2]
        nz = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
        ln = np.sqrt(nx * nx + ny * ny + nz * nz)
        with np.errstate(all="ignore"):
            out = np.stack([nx / ln, ny / ln, nz / ln], 1)
        out[~(ln > 0)] = 0
        return out.astype(F32)

    tn = face_normals_np(C)
    ON = tn.astype(np.float64)
    ONX, ONY, ONZ = ON[:, 0].tolist(), ON[:, 1].tolist(), ON[:, 2].tolist()
    A0 = V[C[:, 0]]
    OD = (ON[:, 0] * A0[:, 0] + ON[:, 1] * A0[:, 1] + ON[:, 2] * A0[:, 2]).tolist()
    deleted = [False] * tri_count
    parents = np.array(face_parent_id, np.int64).copy()

    def aspect_np(Cx):
        A = V[Cx[:, 0]]
        B = V[Cx[:, 1]]
        Cc = V[Cx[:, 2]]
        ab, ac, bc = B - A, Cc - A, Cc - B
        lab = ab[:, 0] * ab[:, 0] + ab[:, 1] * ab[:, 1] + ab[:, 2] * ab[:, 2]
        lac = ac[:, 0] * ac[:, 0] + ac[:, 1] * ac[:, 1] + ac[:, 2] * ac[:, 2]
        lbc = bc[:, 0] * bc[:, 0] + bc[:, 1] * bc[:, 1] + bc[:, 2] * bc[:, 2]
        lmax = np.maximum(np.maximum(lab, lac), lbc)
        nx = ab[:, 1] * ac[:, 2] - ab[:, 2] * ac[:, 1]
        ny = ab[:, 2] * ac[:, 0] - ab[:, 0] * ac[:, 2]
        nz = ab[:, 0] * ac[:, 1] - ab[:, 1] * ac[:, 0]
        cr = nx * nx + ny * ny + nz * nz
        with np.errstate(all="ignore"):
            return np.where(cr > 0, lmax * lmax / cr, np.inf)

    def aspect(t):
        a, b, c = corners[t * 3], corners[t * 3 + 1], corners[t * 3 + 2]
        ax, ay, az = X[a], Y[a], Z[a]
        abx, aby, abz = X[b] - ax, Y[b] - ay, Z[b] - az
        acx, acy, acz = X[c] - ax, Y[c] - ay, Z[c] - az
        bcx, bcy, bcz = X[c] - X[b], Y[c] - Y[b], Z[c] - Z[b]
        lab = abx * abx + aby * aby + abz * abz
        lac = acx * acx + acy * acy + acz * acz
        lbc = bcx * bcx + bcy * bcy + bcz * bcz
        lmax = max(lab, lac, lbc)
        nx = aby * acz - abz * acy
        ny = abz * acx - abx * acz
        nz = abx * acy - aby * acx
        cr = nx * nx + ny * ny + nz * nz
        return lmax * lmax / cr if cr > 0 else math.inf

    # Incident corner slots per vertex.
    # (Each set filled in increasing slot order, as the one-by-one adds did.)
    slots = np.argsort(ids, kind="stable").tolist()
    ends = np.cumsum(np.bincount(ids, minlength=nverts)).tolist()
    vf = []
    lo = 0
    for hi in ends:
        vf.append(set(slots[lo:hi]))
        lo = hi

    # Sharp-edge freeze: every edge's dihedral against the first triangle on it.
    frozen = np.zeros(nverts, bool)
    thin = aspect_np(C).astype(F32).astype(np.float64)
    E = np.concatenate([C[:, [0, 1]], C[:, [1, 2]], C[:, [2, 0]]])
    et = np.concatenate([np.arange(tri_count)] * 3)
    eo = np.concatenate([np.full(tri_count, k) for k in range(3)])
    order = np.lexsort((eo, et))  # the JS's visiting order: triangle, then edge
    E, et = E[order], et[order]
    lo, hi = np.minimum(E[:, 0], E[:, 1]), np.maximum(E[:, 0], E[:, 1])
    from .meshindex import first_ids

    eid, efirst = first_ids(np.stack([lo, hi], 1))
    other = et[efirst[eid]]
    seen = np.arange(len(E)) != efirst[eid]
    ok = seen & ~((thin[et] > ext2) | (thin[other] > ext2))
    dot = (tn[et].astype(np.float64) * tn[other].astype(np.float64)).sum(1)
    sharp = ok & (dot < sharp_cos)
    frozen[E[sharp, 0]] = True
    frozen[E[sharp, 1]] = True
    if o.get("preserveExcluded") and exclude_weight is not None:
        ew = np.asarray(exclude_weight, F32)
        ex = ew[0::3] > 0.99
        frozen[C[ex].reshape(-1)] = True
    frozen = frozen.tolist()

    stats = {"frozen": 0, "wingCount": 0, "linkCondition": 0, "edgeCap": 0, "normalChange": 0, "planeDev": 0, "degenerate": 0, "foldedApex": 0}

    def sq(u, v):
        dx, dy, dz = X[u] - X[v], Y[u] - Y[v], Z[u] - Z[v]
        return dx * dx + dy * dy + dz * dz

    # vf holds alive corner slots only (a collapse discards its two wings'
    # slots and moves v's to u), so a vertex's triangles need no deleted check.
    def try_collapse(u, v):
        if u == v:
            return False
        if frozen[u] or frozen[v]:
            stats["frozen"] += 1
            return False
        tu = {s // 3 for s in vf[u]}
        tv = {s // 3 for s in vf[v]}
        wings = [t for t in tu if t in tv]
        if len(wings) != 2:
            stats["wingCount"] += 1
            return False
        t0, t1 = wings
        b0, b1 = t0 * 3, t1 * 3
        w1 = corners[b0] if corners[b0] != u and corners[b0] != v else (
            corners[b0 + 1] if corners[b0 + 1] != u and corners[b0 + 1] != v else corners[b0 + 2])
        w2 = corners[b1] if corners[b1] != u and corners[b1] != v else (
            corners[b1 + 1] if corners[b1 + 1] != u and corners[b1 + 1] != v else corners[b1 + 2])
        if w1 == w2:
            stats["foldedApex"] += 1
            return False
        a1, a2 = aspect(t0), aspect(t1)
        either = a1 > ext2 or a2 > ext2
        both = a1 > ext2 and a2 > ext2
        eff_len = agg_len if either else base_len
        eff_cos = agg_ncos if both else max_ncos
        nb_v = set()
        for t in tv:
            nb_v.update(corners[t * 3:t * 3 + 3])
        nb_v.discard(v)
        skip = (u, v, w1, w2)
        for t in tu:
            b3 = t * 3
            for x in corners[b3:b3 + 3]:
                if x in nb_v and x not in skip:
                    stats["linkCondition"] += 1
                    return False
        mx, my, mz = (X[u] + X[v]) / 2, (Y[u] + Y[v]) / 2, (Z[u] + Z[v]) / 2
        aff = (tu | tv) - {t0, t1}
        for t in aff:
            onx, ony, onz = ONX[t], ONY[t], ONZ[t]
            if abs(onx * mx + ony * my + onz * mz - OD[t]) > max_plane:
                stats["planeDev"] += 1
                return False
            b3 = t * 3
            ca, cb, cc = corners[b3:b3 + 3]
            if ca == u or ca == v:
                ax, ay, az = mx, my, mz
            else:
                ax, ay, az = X[ca], Y[ca], Z[ca]
            if cb == u or cb == v:
                bx, by, bz = mx, my, mz
            else:
                bx, by, bz = X[cb], Y[cb], Z[cb]
            if cc == u or cc == v:
                cx, cy, cz = mx, my, mz
            else:
                cx, cy, cz = X[cc], Y[cc], Z[cc]
            e1x, e1y, e1z = bx - ax, by - ay, bz - az
            e2x, e2y, e2z = cx - ax, cy - ay, cz - az
            fx, fy, fz = cx - bx, cy - by, cz - bz
            if (e1x * e1x + e1y * e1y + e1z * e1z > eff_len or fx * fx + fy * fy + fz * fz > eff_len
                    or e2x * e2x + e2y * e2y + e2z * e2z > eff_len):
                stats["edgeCap"] += 1
                return False
            nx = e1y * e2z - e1z * e2y
            ny = e1z * e2x - e1x * e2z
            nz = e1x * e2y - e1y * e2x
            nl = math.sqrt(nx * nx + ny * ny + nz * nz)
            if nl <= 0:
                stats["degenerate"] += 1
                return False
            inv = 1 / nl
            if nx * inv * onx + ny * inv * ony + nz * inv * onz < eff_cos:
                stats["normalChange"] += 1
                return False
        X[u], Y[u], Z[u] = mx, my, mz
        for w in wings:
            deleted[w] = True
            for k in range(3):
                vf[corners[w * 3 + k]].discard(w * 3 + k)
        # (The JS also recomputes the moved faces' normals here; nothing
        # reads them before the output's normals are made afresh.)
        vfu = vf[u]
        for s in vf[v]:
            corners[s] = u
            vfu.add(s)
        vf[v] = set()
        return True

    total = 0
    for _round in range(max_rounds):
        alive = np.nonzero(~np.array(deleted))[0]
        Cx = np.array(corners, np.int64).reshape(-1, 3)[alive]
        Vn = np.stack([np.array(X), np.array(Y), np.array(Z)], 1)
        A, B, Cc = Vn[Cx[:, 0]], Vn[Cx[:, 1]], Vn[Cx[:, 2]]

        def d2(p, q):
            d = p - q
            return d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1] + d[:, 2] * d[:, 2]
        lmin = np.minimum(np.minimum(d2(A, B), d2(B, Cc)), d2(Cc, A))
        V = Vn
        asp = aspect_np(Cx)
        cand = (lmin > 0) & ~(asp < aspect_th * aspect_th)
        ct = alive[cand]
        ca = asp[cand]
        order = np.argsort(-ca, kind="stable")
        n_round = 0
        for k_, t in enumerate(ct[order].tolist()):
            if should_abort is not None and not (k_ & 1023) and should_abort():
                return None
            if deleted[t]:
                continue
            a, b, c = corners[t * 3], corners[t * 3 + 1], corners[t * 3 + 2]
            el = [sq(a, b), sq(b, c), sq(c, a)]
            eu, ev = [a, b, c], [b, c, a]
            o0, o1, o2 = 0, 1, 2
            if el[o1] < el[o0]:
                o0, o1 = o1, o0
            if el[o2] < el[o1]:
                o1, o2 = o2, o1
                if el[o1] < el[o0]:
                    o0, o1 = o1, o0
            if try_collapse(eu[o0], ev[o0]) or try_collapse(eu[o1], ev[o1]) or try_collapse(eu[o2], ev[o2]):
                n_round += 1
        total += n_round
        if n_round == 0:
            break

    keep = np.nonzero(~np.array(deleted))[0]
    Cf = np.array(corners, np.int64).reshape(-1, 3)[keep]
    Vf = np.stack([np.array(X), np.array(Y), np.array(Z)], 1)
    out_pos = Vf[Cf.reshape(-1)].astype(F32).reshape(-1)
    geo = Geometry(out_pos)
    geo.compute_vertex_normals()
    ew_out = None
    if exclude_weight is not None:
        ew = np.asarray(exclude_weight, F32)
        ew_out = np.repeat(ew[0::3][keep], 3).astype(F32)
    return {"position": out_pos, "normal": geo.get("normal"), "excludeWeight": ew_out,
            "faceParentId": parents[keep].astype(np.int32), "collapseCount": total, "rejectStats": stats}


# The same collapses, many at a time. Within a round the JS visits its
# candidates in one order and each collapse changes what the later ones see,
# but only near itself: try_collapse(u, v) reads the triangles around u and v
# (their corners and points) and writes u's point, the corners of v's
# triangles and the triangle lists of u, v and the wings' apexes. So a
# candidate can run as soon as no earlier pending candidate has a vertex
# sharing a triangle with one of its own: nothing earlier can still change
# what it reads, and it changes nothing an earlier one will read. Each batch
# runs every such candidate at once (numpy, the same float64 sums in the same
# order), then the next. The collapses, the output and the reject total are
# the one-at-a-time run's; which per-triangle gate a rejected edge is
# counted under (planeDev, edgeCap, degenerate, normalChange) follows the
# first failing triangle in table order, not a Python set's.
#
# The bookkeeping: rank (a pending candidate's place in the order), m (the
# first pending candidate on each vertex) and mt (the first on a triangle's
# corners) are kept up to date where a batch changed something. Readiness is
# always worked out afresh from them (the smallest mt on the triangles around
# a candidate's corners is its own rank), for the candidates whose earlier
# neighbour just went or that sit beside a change; one looked at late only
# waits a batch longer (it still blocks everything after it), never runs
# early, so the order's outcome is kept.

_BIG = np.iinfo(np.int64).max
_STATS = (None, None, "frozen", "wingCount", "foldedApex", "linkCondition", "planeDev", "edgeCap", "degenerate",
          "normalChange")
_SPLIT = 8   # incidence rows up to this long are read SPLIT wide, the rest full width
_HX, _HZ = np.uint64(0x9E3779B97F4A7C15), np.uint64(0xC2B2AE3D27D4EB4F)


def fast_weld(pa):
    """weld(pa, QUANTISE) for float32 (n, 3) points, the same ids: exact
    copies (a subdivided soup repeats each point about six times) grouped by
    a hash of their bits first, then only the distinct points welded on the
    grid. A hash shared by different points falls back to weld."""
    n = len(pa)
    if n == 0:
        return weld(pa.astype(np.float64), QUANTISE)
    b = np.ascontiguousarray(pa, F32).view(np.uint32)
    xy = (b[:, 0].astype(np.uint64) << np.uint64(32)) | b[:, 1].astype(np.uint64)
    with np.errstate(over="ignore"):
        k = (xy * _HX) ^ (b[:, 2].astype(np.uint64) * _HZ)
    order = np.argsort(k)
    sk = k[order]
    new = np.empty(n, bool)
    new[0] = True
    np.not_equal(sk[1:], sk[:-1], out=new[1:])
    sxy, sz = xy[order], b[order, 2]
    if (~new[1:] & ((sxy[1:] != sxy[:-1]) | (sz[1:] != sz[:-1]))).any():
        return weld(pa.astype(np.float64), QUANTISE)
    grp = np.cumsum(new) - 1
    rep = np.minimum.reduceat(order, np.nonzero(new)[0])     # each copy group's first point
    srt = np.argsort(rep)
    rk = np.empty(len(rep), np.int64)
    rk[srt] = np.arange(len(rep))
    reps = rep[srt]
    cid, cfirst = first_ids(quantize(pa[reps].astype(np.float64), QUANTISE))
    ids = np.empty(n, np.int64)
    ids[order] = cid[rk[grp]]
    return ids, len(cfirst), reps[cfirst]


def _aspect_rows(A, B, Cc):
    """aspect() of triangles (A, B, Cc) row by row, float64 in the same order."""
    ab, ac, bc = B - A, Cc - A, Cc - B
    lab = ab[:, 0] * ab[:, 0] + ab[:, 1] * ab[:, 1] + ab[:, 2] * ab[:, 2]
    lac = ac[:, 0] * ac[:, 0] + ac[:, 1] * ac[:, 1] + ac[:, 2] * ac[:, 2]
    lbc = bc[:, 0] * bc[:, 0] + bc[:, 1] * bc[:, 1] + bc[:, 2] * bc[:, 2]
    lmax = np.maximum(np.maximum(lab, lac), lbc)
    nx = ab[:, 1] * ac[:, 2] - ab[:, 2] * ac[:, 1]
    ny = ab[:, 2] * ac[:, 0] - ab[:, 0] * ac[:, 2]
    nz = ab[:, 0] * ac[:, 1] - ab[:, 1] * ac[:, 0]
    cr = nx * nx + ny * ny + nz * nz
    with np.errstate(all="ignore"):
        return np.where(cr > 0, lmax * lmax / cr, np.inf)


def _d2(p, q):
    d = p - q
    return d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1] + d[:, 2] * d[:, 2]


def regularize_mesh(positions, face_parent_id, max_edge, opts=None, exclude_weight=None, should_abort=None):
    """regularizeMesh: {"position", "normal", "excludeWeight", "faceParentId",
    "collapseCount", "rejectStats"}, or None when should_abort() turned true.
    Batched (see above) from BATCHED_FROM triangles; below that the batches'
    fixed cost outweighs them and the one-at-a-time run is quicker. Either
    gives the same output, bit for bit."""
    if len(positions) // 9 < BATCHED_FROM:
        return regularize_sequential(positions, face_parent_id, max_edge, opts, exclude_weight, should_abort)
    return regularize_batched(positions, face_parent_id, max_edge, opts, exclude_weight, should_abort)


BATCHED_FROM = 20_000


def regularize_batched(positions, face_parent_id, max_edge, opts=None, exclude_weight=None, should_abort=None):
    """regularize_sequential's output, bit for bit, in batches (see above);
    None when should_abort() turned true (checked every batch)."""
    o = opts or {}
    aspect_th = o.get("aspectThreshold", 5)
    slack = o.get("slack", 3.0)
    agg_slack = o.get("aggressiveSlack", 8.0)
    extreme = o.get("extremeSliverAspect", 8)
    max_ncos = o.get("maxNormalDeltaCos", math.cos(15 * math.pi / 180))
    agg_ncos = o.get("aggressiveNormalDeltaCos", math.cos(25 * math.pi / 180))
    max_plane = o.get("maxPlaneDev", 0.03)
    sharp_cos = o.get("sharpEdgeCos", math.cos(30 * math.pi / 180))
    max_rounds = o.get("maxRounds", 8)
    base_len = (max_edge * slack) * (max_edge * slack)
    agg_len = (max_edge * agg_slack) * (max_edge * agg_slack)
    ext2 = extreme * extreme

    pa = np.asarray(positions, F32).reshape(-1, 3)
    nt = len(pa) // 3
    pa = pa[:nt * 3]
    ids, nv, firsts = fast_weld(pa)
    # A sentinel vertex (nv) and triangle (nt): a -1 in the incidence table
    # reads the sentinel triangle, whose corners are the sentinel vertex.
    P = np.zeros((nv + 1, 3))
    P[:nv] = pa[firsts]
    C = ids.reshape(-1, 3)
    Cn = np.full((nt + 1, 3), nv, np.int64)
    Cn[:nt] = C

    A0, B0, C0 = P[C[:, 0]], P[C[:, 1]], P[C[:, 2]]
    e1 = B0 - A0
    e2 = C0 - A0
    nx = e1[:, 1] * e2[:, 2] - e1[:, 2] * e2[:, 1]
    ny = e1[:, 2] * e2[:, 0] - e1[:, 0] * e2[:, 2]
    nz = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
    ln = np.sqrt(nx * nx + ny * ny + nz * nz)
    with np.errstate(all="ignore"):
        tn = np.stack([nx / ln, ny / ln, nz / ln], 1)
    tn[~(ln > 0)] = 0
    tn = tn.astype(F32)
    ON = np.zeros((nt + 1, 3))
    ON[:nt] = tn
    OD = np.zeros(nt + 1)
    OD[:nt] = ON[:nt, 0] * A0[:, 0] + ON[:nt, 1] * A0[:, 1] + ON[:nt, 2] * A0[:, 2]
    deleted = np.zeros(nt + 1, bool)
    deleted[nt] = True
    asp = np.full(nt + 1, np.inf)
    asp[:nt] = _aspect_rows(A0, B0, C0)
    lmin = np.zeros(nt + 1)
    lmin[:nt] = np.minimum(np.minimum(_d2(A0, B0), _d2(B0, C0)), _d2(C0, A0))
    dirty = np.zeros(nt + 1, bool)

    # Vertex -> incident triangles: padded rows, live entries first.
    VL = np.bincount(ids, minlength=nv + 1).astype(np.int64)
    K = max(int(VL.max()), 1)
    VT = np.full((nv + 1, K), -1, np.int64)
    srt = np.argsort(ids, kind="stable")
    sv = ids[srt]
    start = np.cumsum(VL) - VL
    VT[sv, np.arange(len(sv)) - start[sv]] = srt // 3

    # Sharp-edge freeze: every edge's dihedral against the first triangle on
    # it (triangle order, then edge order).
    frozen = np.zeros(nv + 1, bool)
    if nt:
        thin = asp[:nt].astype(F32).astype(np.float64)
        E = C[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
        key = np.minimum(E[:, 0], E[:, 1]) * (nv + 1) + np.maximum(E[:, 0], E[:, 1])
        eo = np.argsort(key)
        sk = key[eo]
        newg = np.empty(len(sk), bool)
        newg[0] = True
        np.not_equal(sk[1:], sk[:-1], out=newg[1:])
        gs = np.nonzero(newg)[0]
        firstrow = np.empty(len(sk), np.int64)
        firstrow[eo] = np.minimum.reduceat(eo, gs)[np.cumsum(newg) - 1]
        rows = np.nonzero(np.arange(len(E)) != firstrow)[0]
        et, other = rows // 3, firstrow[rows] // 3
        ok = ~((thin[et] > ext2) | (thin[other] > ext2))
        dot = (tn[et].astype(np.float64) * tn[other].astype(np.float64)).sum(1)
        sharp = rows[ok & (dot < sharp_cos)]
        frozen[E[sharp, 0]] = True
        frozen[E[sharp, 1]] = True
    if o.get("preserveExcluded") and exclude_weight is not None:
        ew = np.asarray(exclude_weight, F32)
        frozen[C[ew[0::3] > 0.99].reshape(-1)] = True

    stat_counts = np.zeros(len(_STATS), np.int64)
    scratch_v = np.zeros(nv + 1, np.int64)
    scratch_t = np.zeros(nt + 1, np.int64)

    def uniq(a, scratch):
        """a's distinct values (any order), without a sort."""
        i = np.arange(len(a))
        scratch[a] = i
        return a[scratch[a] == i]

    def split(X):
        """X's positions with short rows, and with long ones (and their width)."""
        long_ = VL[X] > _SPLIT
        if not long_.any():
            return [(slice(None), min(_SPLIT, K))]
        li = np.nonzero(long_)[0]
        return [(np.nonzero(~long_)[0], _SPLIT), (li, int(VL[X[li]].max()))]

    def row_min(vals, X):
        """min of vals over the triangles of each vertex in X (vals[-1]: none)."""
        out = np.empty(len(X), vals.dtype)
        for sel, w in split(X):
            out[sel] = vals[VT[X[sel], :w].T].min(0)
        return out

    def row_tris(X):
        """The triangles of the vertices in X, flattened (with repeats)."""
        parts = []
        for sel, w in split(X):
            r = VT[X[sel], :w].reshape(-1)
            parts.append(r[r >= 0])
        return parts[0] if len(parts) == 1 else np.concatenate(parts)

    def gate_part(U, Vv, ku, kv):
        n = len(U)
        code = np.zeros(n, np.int64)
        code[U == Vv] = 1
        code[(code == 0) & (frozen[U] | frozen[Vv])] = 2
        tu = VT[U, :ku]
        tv = VT[Vv, :kv]
        Cu = Cn[tu]                     # (n, ku, 3); the sentinel where tu is -1
        Cv = Cn[tv]
        Vc_, Uc_ = Vv[:, None], U[:, None]
        wing_u = (Cu[:, :, 0] == Vc_) | (Cu[:, :, 1] == Vc_) | (Cu[:, :, 2] == Vc_)
        wing_v = (Cv[:, :, 0] == Uc_) | (Cv[:, :, 1] == Uc_) | (Cv[:, :, 2] == Uc_)
        code[(code == 0) & (wing_u.sum(1) != 2)] = 3
        r = np.arange(n)
        c0 = np.argmax(wing_u, 1)
        w2m = wing_u.copy()
        w2m[r, c0] = False
        t0, t1 = tu[r, c0], tu[r, np.argmax(w2m, 1)]

        def third(t):
            a, b, c = Cn[t, 0], Cn[t, 1], Cn[t, 2]
            return np.where((a != U) & (a != Vv), a, np.where((b != U) & (b != Vv), b, c))
        w1, w2 = third(t0), third(t1)
        code[(code == 0) & (w1 == w2)] = 4
        Q0, Q1 = Cn[t0], Cn[t1]
        a1 = _aspect_rows(P[Q0[:, 0]], P[Q0[:, 1]], P[Q0[:, 2]])
        a2 = _aspect_rows(P[Q1[:, 0]], P[Q1[:, 1]], P[Q1[:, 2]])
        eff_len = np.where((a1 > ext2) | (a2 > ext2), agg_len, base_len)
        eff_cos = np.where((a1 > ext2) & (a2 > ext2), agg_ncos, max_ncos)
        # Link condition: a vertex of u's triangles other than u, v, w1, w2
        # that is also a vertex of v's triangles other than v. (Each
        # triangle's two corners besides its centre.)
        c_0, c_1, c_2 = Cu[:, :, 0], Cu[:, :, 1], Cu[:, :, 2]
        Uc = U[:, None]
        xs = np.concatenate([np.where(c_0 == Uc, c_1, c_0), np.where(c_2 == Uc, c_1, c_2)], 1)
        xs = np.where((xs == Vv[:, None]) | (xs == w1[:, None]) | (xs == w2[:, None]) | (xs == nv), -1, xs)
        c_0, c_1, c_2 = Cv[:, :, 0], Cv[:, :, 1], Cv[:, :, 2]
        Vc = Vv[:, None]
        ys = np.concatenate([np.where(c_0 == Vc, c_1, c_0), np.where(c_2 == Vc, c_1, c_2)], 1)
        ys = np.where(ys == nv, -2, ys)
        link = (xs[:, :, None] == ys[:, None, :]).reshape(n, -1).any(1)
        code[(code == 0) & link] = 5
        mx = (P[U, 0] + P[Vv, 0]) / 2
        my = (P[U, 1] + P[Vv, 1]) / 2
        mz = (P[U, 2] + P[Vv, 2]) / 2
        aff = np.concatenate([np.where((tu >= 0) & ~wing_u, tu, -1), np.where((tv >= 0) & ~wing_v, tv, -1)], 1)
        ai, aj = np.nonzero((aff >= 0) & (code == 0)[:, None])
        if len(ai):
            T = aff[ai, aj]
            Mx, My, Mz = mx[ai], my[ai], mz[ai]
            onx, ony, onz = ON[T, 0], ON[T, 1], ON[T, 2]
            pdev = np.abs(onx * Mx + ony * My + onz * Mz - OD[T]) > max_plane
            Ui, Vi = U[ai], Vv[ai]
            CC = Cn[T]
            pts = []
            for k in range(3):
                ck = CC[:, k]
                mv = (ck == Ui) | (ck == Vi)
                Pk = P[ck]
                pts.append((np.where(mv, Mx, Pk[:, 0]), np.where(mv, My, Pk[:, 1]), np.where(mv, Mz, Pk[:, 2])))
            (ax, ay, az), (bx, by, bz), (cx, cy, cz) = pts
            e1x, e1y, e1z = bx - ax, by - ay, bz - az
            e2x, e2y, e2z = cx - ax, cy - ay, cz - az
            fx, fy, fz = cx - bx, cy - by, cz - bz
            el = eff_len[ai]
            ecap = ((e1x * e1x + e1y * e1y + e1z * e1z > el) | (fx * fx + fy * fy + fz * fz > el)
                    | (e2x * e2x + e2y * e2y + e2z * e2z > el))
            nx = e1y * e2z - e1z * e2y
            ny = e1z * e2x - e1x * e2z
            nz = e1x * e2y - e1y * e2x
            nl = np.sqrt(nx * nx + ny * ny + nz * nz)
            with np.errstate(all="ignore"):
                inv = 1 / nl
                ncos = nx * inv * onx + ny * inv * ony + nz * inv * onz
            tcode = np.where(pdev, 6, np.where(ecap, 7, np.where(nl <= 0, 8, np.where(ncos < eff_cos[ai], 9, 0))))
            fi = np.nonzero(tcode)[0]
            if len(fi):
                fa, first = np.unique(ai[fi], return_index=True)
                code[fa] = tcode[fi[first]]
        return code, t0, t1, w1, w2, np.stack([mx, my, mz], 1)

    def gate(U, Vv):
        """try_collapse(U[i], Vv[i]) for edges far apart, all at once: the
        _STATS index of the first gate each fails (0: it collapses), the
        wings, their apexes and the midpoints."""
        n = len(U)
        out = [np.zeros(n, np.int64) for _ in range(5)] + [np.zeros((n, 3))]
        lu, lv = VL[U], VL[Vv]
        long_ = (lu > _SPLIT) | (lv > _SPLIT)
        if n < 256 or not long_.any():
            parts = [(slice(None), max(int(lu.max()), 1), max(int(lv.max()), 1))] if n else []
        else:
            li = np.nonzero(long_)[0]
            parts = [(np.nonzero(~long_)[0], _SPLIT, _SPLIT),
                     (li, max(int(lu[li].max()), 1), max(int(lv[li].max()), 1))]
        for sel, ku, kv in parts:
            for dst, src in zip(out, gate_part(U[sel], Vv[sel], ku, kv)):
                dst[sel] = src
        return out

    def apply(U, Vv, t0, t1, w1, w2, M):
        nonlocal VT, K
        n = len(U)
        ku = max(int(VL[U].max()), 1)
        kv = max(int(VL[Vv].max()), 1)
        tu = VT[U, :ku]
        tv = VT[Vv, :kv]
        tum = (tu >= 0) & (tu != t0[:, None]) & (tu != t1[:, None])
        tvm = (tv >= 0) & (tv != t0[:, None]) & (tv != t1[:, None])
        P[U] = M
        deleted[t0] = True
        deleted[t1] = True
        rr, cc = np.nonzero(tvm)
        T = tv[rr, cc]
        CT = Cn[T]
        Cn[T] = np.where(CT == Vv[rr][:, None], U[rr][:, None], CT)
        for w, t in ((w1, t0), (w2, t1)):
            ri, ci = np.nonzero(VT[w] == t[:, None])
            VT[w[ri], ci] = -1
        nu = np.concatenate([np.where(tum, tu, -1), np.where(tvm, tv, -1)], 1)
        nu = -np.sort(-nu, 1)
        lens = (nu >= 0).sum(1)
        need = int(lens.max()) if n else 0
        if need > K:
            grow = need + 4
            VT = np.concatenate([VT, np.full((len(VT), grow - K), -1, np.int64)], 1)
            K = grow
        w_ = min(nu.shape[1], K)
        VT[U] = -1
        VT[U, :w_] = nu[:, :w_]
        VL[U] = lens
        VT[Vv] = -1
        VL[Vv] = 0
        dirty[nu[nu >= 0]] = True
        ws = np.unique(np.concatenate([w1, w2]))
        rows = -np.sort(-VT[ws], 1)
        VT[ws] = rows
        VL[ws] = (rows >= 0).sum(1)

    def edge_order(TE):
        """The three edges of each triangle in the order the JS tries them."""
        a, b, c = Cn[TE, 0], Cn[TE, 1], Cn[TE, 2]
        el = np.stack([_d2(P[a], P[b]), _d2(P[b], P[c]), _d2(P[c], P[a])], 1)
        n = len(TE)
        r = np.arange(n)
        o0 = np.zeros(n, np.int64)
        o1 = np.ones(n, np.int64)
        o2 = np.full(n, 2, np.int64)
        s = el[r, o1] < el[r, o0]
        o0, o1 = np.where(s, o1, o0), np.where(s, o0, o1)
        s = el[r, o2] < el[r, o1]
        o1, o2 = np.where(s, o2, o1), np.where(s, o1, o2)
        s = s & (el[r, o1] < el[r, o0])
        o0, o1 = np.where(s, o1, o0), np.where(s, o0, o1)
        eu = np.stack([a, b, c], 1)
        ev = np.stack([b, c, a], 1)
        return [(eu[r, oo], ev[r, oo]) for oo in (o0, o1, o2)]

    rank = np.full(nt + 1, _BIG, np.int64)
    m = np.full(nv + 1, _BIG, np.int64)
    mt = np.full(nt + 1, _BIG, np.int64)
    blk = np.zeros(nt + 1, np.int64)
    mr = np.zeros(nv + 1, np.int64)      # scratch: the first pending candidate around a vertex

    def update(D):
        """m and mt again around the vertices D, where something changed; the
        pending candidates on the triangles around D."""
        m[D] = row_min(rank, D)
        Ts = uniq(row_tris(D), scratch_t)
        CT = Cn[Ts]
        mt[Ts] = np.minimum(np.minimum(m[CT[:, 0]], m[CT[:, 1]]), m[CT[:, 2]])
        return Ts[rank[Ts] != _BIG]

    def first_near(S):
        """The rank of the first pending candidate near each of S."""
        X = Cn[S]
        Xu = uniq(X.reshape(-1), scratch_v)
        mr[Xu] = row_min(mt, Xu)
        return np.minimum(np.minimum(mr[X[:, 0]], mr[X[:, 1]]), mr[X[:, 2]])

    total = 0
    for _round in range(max_rounds):
        dt = np.nonzero(dirty)[0]
        if len(dt):
            dirty[dt] = False
            Cx = Cn[dt]
            A, B, Cc = P[Cx[:, 0]], P[Cx[:, 1]], P[Cx[:, 2]]
            lmin[dt] = np.minimum(np.minimum(_d2(A, B), _d2(B, Cc)), _d2(Cc, A))
            asp[dt] = _aspect_rows(A, B, Cc)
        cand = np.nonzero(~deleted & (lmin > 0) & ~(asp < aspect_th * aspect_th))[0]
        if not len(cand):
            break
        order = cand[np.argsort(-asp[cand], kind="stable")]
        rank[:] = _BIG
        m[:] = _BIG
        mt[:] = _BIG
        rank[order] = np.arange(len(order))
        resolved = np.zeros(len(order) + 1, bool)
        update(uniq(Cn[order].reshape(-1), scratch_v))
        pend = order
        check = order
        n_round = 0
        while len(pend):
            if should_abort is not None and should_abort():
                return None
            # Ready: no earlier pending candidate near. The others note the
            # one in their way and are looked at again once it is gone (or
            # something changes beside them).
            fn = first_near(check)
            rk = rank[check]
            TE = check[fn == rk]
            blk[check] = fn
            if not len(TE):     # cannot happen (the first pending one is always ready); were it to, look at all
                fn = first_near(pend)
                blk[pend] = fn
                TE = pend[fn == rank[pend]]
                if not len(TE):
                    raise RuntimeError("regularize: no candidate ready")
            # Each ready triangle's shortest edge first; where that is
            # refused, its second and third (both read before either is
            # applied: a refusal changes nothing).
            corners = Cn[TE].reshape(-1)
            (U, Vv), (u1, v1), (u2, v2) = edge_order(TE)
            code, t0, t1, w1, w2, M = gate(U, Vv)
            stat_counts += np.bincount(code, minlength=len(_STATS))
            f = np.nonzero(code)[0]
            if len(f):
                nf = len(f)
                c2, *r2 = gate(np.concatenate([u1[f], u2[f]]), np.concatenate([v1[f], v2[f]]))
                stat_counts += np.bincount(c2[:nf], minlength=len(_STATS))
                third_ = c2[:nf] != 0
                stat_counts += np.bincount(c2[nf:][third_], minlength=len(_STATS))
                pick = np.where(third_, np.arange(nf) + nf, np.arange(nf))
                U, Vv = U.copy(), Vv.copy()
                U[f] = np.concatenate([u1[f], u2[f]])[pick]
                Vv[f] = np.concatenate([v1[f], v2[f]])[pick]
                code[f] = c2[pick]
                for a, b in zip((t0, t1, w1, w2, M), r2):
                    a[f] = b[pick]
            ok = code == 0
            t0, t1, w1, w2 = t0[ok], t1[ok], w1[ok], w2[ok]
            if len(t0):
                apply(U[ok], Vv[ok], t0, t1, w1, w2, M[ok])
                n_round += len(t0)
            touched = [corners, w1, w2]
            gone = [TE, t0, t1]
            gone = np.concatenate(gone)
            gone = uniq(gone[rank[gone] != _BIG], scratch_t)
            resolved[rank[gone]] = True
            rank[gone] = _BIG
            near = update(uniq(np.concatenate(touched), scratch_v))
            pend = pend[rank[pend] != _BIG]
            waiting = pend[resolved[blk[pend]]]
            resolved[:] = False
            check = uniq(np.concatenate([waiting, near]), scratch_t)
        total += n_round
        if n_round == 0:
            break

    stats = {name: int(stat_counts[k]) for k, name in enumerate(_STATS) if name}
    keep = np.nonzero(~deleted[:nt])[0]
    out_pos = P[Cn[keep].reshape(-1)].astype(F32).reshape(-1)
    geo = Geometry(out_pos)
    geo.compute_vertex_normals()
    ew_out = None
    if exclude_weight is not None:
        ew = np.asarray(exclude_weight, F32)
        ew_out = np.repeat(ew[0::3][keep], 3).astype(F32)
    parents = np.asarray(face_parent_id, np.int64)
    return {"position": out_pos, "normal": geo.get("normal"), "excludeWeight": ew_out,
            "faceParentId": parents[keep].astype(np.int32), "collapseCount": total, "rejectStats": stats}
