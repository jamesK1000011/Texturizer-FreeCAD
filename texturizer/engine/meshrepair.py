# SPDX-License-Identifier: AGPL-3.0-only
# Port of js/meshRepair.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# After decimation: T-junctions sealed by splitting an open edge at the open
# vertices lying on it (topology only, no geometry added or removed), needles
# dropped, and the defect counts the export reports (open and non-manifold
# edges on the 0.1 µm weld, zero-area slivers).
import numpy as np

from ._js import F32, jround
from .meshindex import weld


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
        cnt = {}
        for f in faces:
            for e in range(3):
                x, y = f[e], f[(e + 1) % 3]
                k = (x, y) if x < y else (y, x)
                cnt[k] = cnt.get(k, 0) + 1
        bverts = []
        seen = set()
        for (lo, hi), n in cnt.items():
            if n != 1:
                continue
            for v in (lo, hi):
                if v not in seen:
                    seen.add(v)
                    bverts.append(v)
        if not bverts:
            break
        bv = np.array(bverts, np.int64)
        BX, BY, BZ = V[bv, 0], V[bv, 1], V[bv, 2]
        splits = {}
        for fi, f in enumerate(faces):
            for e in range(3):
                x, y = f[e], f[(e + 1) % 3]
                k = (x, y) if x < y else (y, x)
                if cnt.get(k, 0) != 1:
                    continue
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
