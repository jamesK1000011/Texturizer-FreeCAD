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
# changes an outcome.
import math

import numpy as np

from ._js import F32, Geometry
from .meshindex import weld

QUANTISE = 1e5


def regularize_mesh(positions, face_parent_id, max_edge, opts=None, exclude_weight=None):
    """regularizeMesh: {"position", "normal", "excludeWeight", "faceParentId", "collapseCount", "rejectStats"}."""
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
    NX, NY, NZ = (tn[:, k].astype(np.float64).tolist() for k in range(3))
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

    def recompute(t):
        a, b, c = corners[t * 3], corners[t * 3 + 1], corners[t * 3 + 2]
        ax, ay, az = X[a], Y[a], Z[a]
        e1x, e1y, e1z = X[b] - ax, Y[b] - ay, Z[b] - az
        e2x, e2y, e2z = X[c] - ax, Y[c] - ay, Z[c] - az
        nx = e1y * e2z - e1z * e2y
        ny = e1z * e2x - e1x * e2z
        nz = e1x * e2y - e1y * e2x
        ln = math.sqrt(nx * nx + ny * ny + nz * nz)
        if ln > 0:
            NX[t], NY[t], NZ[t] = float(F32(nx / ln)), float(F32(ny / ln)), float(F32(nz / ln))
        else:
            NX[t] = NY[t] = NZ[t] = 0.0

    # Incident corner slots per vertex.
    vf = [set() for _ in range(nverts)]
    for s, v in enumerate(corners):
        vf[v].add(s)

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

    def alive_tris(v):
        return {s // 3 for s in vf[v] if not deleted[s // 3]}

    def try_collapse(u, v):
        if u == v:
            return False
        if frozen[u] or frozen[v]:
            stats["frozen"] += 1
            return False
        tu = alive_tris(u)
        tv = alive_tris(v)
        wings = [t for t in tu if v in (corners[t * 3], corners[t * 3 + 1], corners[t * 3 + 2])]
        if len(wings) != 2:
            stats["wingCount"] += 1
            return False

        def third(t):
            a, b, c = corners[t * 3], corners[t * 3 + 1], corners[t * 3 + 2]
            if a != u and a != v:
                return a
            if b != u and b != v:
                return b
            return c
        w1, w2 = third(wings[0]), third(wings[1])
        if w1 == w2:
            stats["foldedApex"] += 1
            return False
        a1, a2 = aspect(wings[0]), aspect(wings[1])
        either = a1 > ext2 or a2 > ext2
        both = a1 > ext2 and a2 > ext2
        eff_len = agg_len if either else base_len
        eff_cos = agg_ncos if both else max_ncos
        nb_v = set()
        for t in tv:
            for k in range(3):
                x = corners[t * 3 + k]
                if x != v:
                    nb_v.add(x)
        for t in tu:
            for k in range(3):
                x = corners[t * 3 + k]
                if x != u and x != v and x != w1 and x != w2 and x in nb_v:
                    stats["linkCondition"] += 1
                    return False
        mx, my, mz = (X[u] + X[v]) / 2, (Y[u] + Y[v]) / 2, (Z[u] + Z[v]) / 2
        aff = (tu | tv) - {wings[0], wings[1]}
        for t in aff:
            if abs(ONX[t] * mx + ONY[t] * my + ONZ[t] * mz - OD[t]) > max_plane:
                stats["planeDev"] += 1
                return False
            pts = []
            for k in range(3):
                x = corners[t * 3 + k]
                pts.append((mx, my, mz) if x == u or x == v else (X[x], Y[x], Z[x]))
            (ax, ay, az), (bx, by, bz), (cx, cy, cz) = pts
            ab2 = (bx - ax) * (bx - ax) + (by - ay) * (by - ay) + (bz - az) * (bz - az)
            bc2 = (cx - bx) * (cx - bx) + (cy - by) * (cy - by) + (cz - bz) * (cz - bz)
            ca2 = (ax - cx) * (ax - cx) + (ay - cy) * (ay - cy) + (az - cz) * (az - cz)
            if ab2 > eff_len or bc2 > eff_len or ca2 > eff_len:
                stats["edgeCap"] += 1
                return False
            e1x, e1y, e1z = bx - ax, by - ay, bz - az
            e2x, e2y, e2z = cx - ax, cy - ay, cz - az
            nx = e1y * e2z - e1z * e2y
            ny = e1z * e2x - e1x * e2z
            nz = e1x * e2y - e1y * e2x
            nl = math.sqrt(nx * nx + ny * ny + nz * nz)
            if nl <= 0:
                stats["degenerate"] += 1
                return False
            inv = 1 / nl
            if nx * inv * ONX[t] + ny * inv * ONY[t] + nz * inv * ONZ[t] < eff_cos:
                stats["normalChange"] += 1
                return False
        X[u], Y[u], Z[u] = mx, my, mz
        for w in wings:
            deleted[w] = True
            for k in range(3):
                vf[corners[w * 3 + k]].discard(w * 3 + k)
        for s in list(vf[v]):
            vf[v].discard(s)
            corners[s] = u
            vf[u].add(s)
            recompute(s // 3)
        for s in vf[u]:
            if not deleted[s // 3]:
                recompute(s // 3)
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
        for t in ct[order].tolist():
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
