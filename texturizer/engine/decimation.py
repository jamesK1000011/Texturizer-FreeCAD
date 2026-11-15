# SPDX-License-Identifier: AGPL-3.0-only
# Port of js/decimation.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# QEM (quadric error metric) edge-collapse decimation, Garland & Heckbert 1997,
# with PrusaSlicer's three guards (boundary edges kept, link condition, normal
# flip under ~78°), crease penalty planes, and flat-face harvesting past the
# target while a collapse costs under harvestTol². Locked faces (preserve
# untextured) and every vertex they touch are left alone.
#
# The collapse loop is sequential by nature, so it is plain Python over lists
# (doubles, as the JS's Float64Arrays): the same heap (1-indexed binary heap,
# the JS's sift rules, so equal costs pop in the same order), the same
# vertex-face linked lists and the same arithmetic, hence the same collapses.
import math

import numpy as np

from ._js import F32
from .meshindex import weld

QUANT_DEFAULT = 1e6
FLIP_DOT = 0.2
FLIP_DOT_SQ = FLIP_DOT * FLIP_DOT
CREASE_COS = 0.5
CREASE_WEIGHT = 1e4
DEFAULT_HARVEST_TOL = 0.005


class _Heap:
    """SoAHeap: entries (cost, v1, v2, ver1, ver2, px, py, pz) in a 1-indexed
    list, sifted exactly as the JS sifts."""

    __slots__ = ("a",)

    def __init__(self):
        self.a = [None]

    def __len__(self):
        return len(self.a) - 1

    def push(self, e):
        a = self.a
        a.append(e)
        idx = len(a) - 1
        cost = e[0]
        while idx > 1:
            parent = idx >> 1
            if a[parent][0] <= cost:
                break
            a[idx] = a[parent]
            idx = parent
        a[idx] = e

    def pop(self):
        a = self.a
        n = len(a) - 1
        if n == 0:
            return None
        top = a[1]
        last = a.pop()
        n -= 1
        if n > 0:
            a[1] = last
            idx = 1
            cost = last[0]
            while True:
                l = idx << 1
                r = l | 1
                child = -1
                if l <= n and a[l][0] < cost:
                    child = l
                if r <= n and a[r][0] < (a[child][0] if child >= 0 else cost):
                    child = r
                if child < 0:
                    break
                a[idx] = a[child]
                idx = child
            a[idx] = last
        return top


def build_indexed(positions):
    """buildIndexed: welded vertices (doubles from the float32 input) and faces."""
    p32 = np.asarray(positions, F32).reshape(-1, 3)
    ids, unique, firsts = weld(p32.astype(np.float64), QUANT_DEFAULT)
    pos = p32[firsts].astype(np.float64)
    return pos, ids.astype(np.int64), unique, len(ids) // 3


def _face_normals32(posArray):
    """Exact per-face normals of float32 positions, as buildOutput writes them."""
    p = posArray.reshape(-1, 9).astype(np.float64)
    u = p[:, 3:6] - p[:, 0:3]
    v = p[:, 6:9] - p[:, 0:3]
    n = np.empty_like(u)
    n[:, 0] = u[:, 1] * v[:, 2] - u[:, 2] * v[:, 1]
    n[:, 1] = u[:, 2] * v[:, 0] - u[:, 0] * v[:, 2]
    n[:, 2] = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    ln = np.sqrt(n[:, 0] * n[:, 0] + n[:, 1] * n[:, 1] + n[:, 2] * n[:, 2])
    ln = np.where(ln == 0, 1.0, ln)
    n = (n / ln[:, None]).astype(F32)
    return np.repeat(n, 3, axis=0).reshape(-1)


def build_output(pos, faces):
    """buildOutput: live faces as float32 triangles with exact face normals."""
    f = np.asarray(faces, np.int64).reshape(-1, 3)
    f = f[f[:, 0] >= 0]
    posArray = np.asarray(pos, np.float64).reshape(-1, 3)[f.reshape(-1)].astype(F32).reshape(-1)
    return {"position": posArray, "normal": _face_normals32(posArray), "userData": {}}


def decimate(positions, target_triangles, on_progress=None, harvest_flat=True, harvest_tol=DEFAULT_HARVEST_TOL,
             locked_faces=None, should_abort=None):
    """decimate(geometry, targetTriangles, onProgress, harvestFlat, harvestTol, lockedFaces).
    Returns {"position", "normal", "userData"} (userData.lockedOverBudget when it applies)."""
    pos_np, faces_np, vert_count, face_count = build_indexed(positions)
    if face_count <= target_triangles and not harvest_flat:
        return build_output(pos_np, faces_np)

    faces = faces_np.tolist()
    P = pos_np.reshape(-1).tolist()

    locked_vert = None
    locked_face_count = 0
    if locked_faces is not None:
        lf = np.asarray(locked_faces).astype(bool)
        locked_face_count = int(lf.sum())
        lv = np.zeros(vert_count, bool)
        lv[faces_np.reshape(-1, 3)[lf].reshape(-1)] = True
        locked_vert = lv.tolist()
    locked_over = locked_vert is not None and face_count > target_triangles and locked_face_count >= target_triangles
    if locked_over and not harvest_flat:
        if on_progress:
            on_progress(1)
        out = build_output(pos_np, faces_np)
        out["userData"]["lockedOverBudget"] = True
        return out

    Q = [0.0] * (vert_count * 10)
    _init_quadrics(Q, P, faces, face_count)
    _add_crease_quadrics(Q, P, faces, face_count)

    # Linked vertex-face incidence: slot s = f*3+k.
    S = face_count * 3
    vfHead = [-1] * vert_count
    slotVert = [0] * S
    slotNext = [-1] * S
    slotPrev = [-1] * S
    slotLive = [0] * S
    for f in range(face_count):
        if faces[f * 3] < 0:
            continue
        for k in range(3):
            v = faces[f * 3 + k]
            s = f * 3 + k
            slotVert[s] = v
            h = vfHead[v]
            slotNext[s] = h
            slotPrev[s] = -1
            if h >= 0:
                slotPrev[h] = s
            vfHead[v] = s
            slotLive[s] = 1

    active = [1] * vert_count
    version = [0] * vert_count
    nbStamp = [0] * vert_count
    epoch = 1
    lkStamp = [0] * vert_count
    lkEpoch = 1
    active_faces = face_count

    heap = _Heap()
    seen = set()
    for f in range(face_count):
        if faces[f * 3] < 0:
            continue
        for e in range(3):
            va = faces[f * 3 + e]
            vb = faces[f * 3 + (e + 1) % 3]
            if locked_vert is not None and (locked_vert[va] or locked_vert[vb]):
                continue
            key = (va, vb) if va < vb else (vb, va)
            if key in seen:
                continue
            seen.add(key)
            _push_edge(heap, Q, P, version, va, vb)
    seen = None

    init_faces = active_faces
    to_remove = max(1, init_faces - target_triangles)
    last_prog = 0.0
    harvest_ceil = harvest_tol * harvest_tol
    reached = locked_over
    it = 0

    while len(heap) > 0:
        if active_faces <= target_triangles:
            if not harvest_flat:
                break
            reached = True
        e = heap.pop()
        if e is None:
            break
        cost, v1, v2, ver1, ver2, px, py, pz = e
        if reached and cost > harvest_ceil:
            break
        it += 1
        if (it & 16383) == 0:
            if should_abort and should_abort():
                return None
            if on_progress:
                p = min(1.0, (init_faces - active_faces) / to_remove)
                if p - last_prog > 0.005:
                    on_progress(p)
                    last_prog = p
        if not active[v1] or not active[v2]:
            continue
        if version[v1] != ver1 or version[v2] != ver2:
            continue

        # Guard 0+1: shared live faces (0 stale, 1 boundary).
        cnt = 0
        s = vfHead[v1]
        while s >= 0:
            f = s // 3
            b = f * 3
            if faces[b] >= 0 and (faces[b] == v2 or faces[b + 1] == v2 or faces[b + 2] == v2):
                cnt += 1
                if cnt >= 2:
                    break
            s = slotNext[s]
        if cnt < 2:
            continue

        lkEpoch += 2
        if _link_violation(faces, vfHead, slotNext, v1, v2, lkStamp, lkEpoch):
            continue
        if _flipped(P, vfHead, slotNext, faces, v1, v2, px, py, pz):
            continue
        if _flipped(P, vfHead, slotNext, faces, v2, v1, px, py, pz):
            continue

        P[v1 * 3] = px
        P[v1 * 3 + 1] = py
        P[v1 * 3 + 2] = pz
        o1 = v1 * 10
        o2 = v2 * 10
        for i in range(10):
            Q[o1 + i] += Q[o2 + i]
        version[v1] += 1

        s = vfHead[v2]
        while s >= 0:
            f = s // 3
            sn = slotNext[s]
            b = f * 3
            if faces[b] >= 0:
                cv2 = 0 if faces[b] == v2 else (1 if faces[b + 1] == v2 else 2)
                faces[b + cv2] = v1
                fa, fb, fc = faces[b], faces[b + 1], faces[b + 2]
                if fa == fb or fb == fc or fa == fc:
                    for k in range(3):
                        sk = b + k
                        if slotLive[sk]:
                            _unlink(sk, vfHead, slotNext, slotPrev, slotVert)
                            slotLive[sk] = 0
                    faces[b] = faces[b + 1] = faces[b + 2] = -1
                    active_faces -= 1
                else:
                    _unlink(s, vfHead, slotNext, slotPrev, slotVert)
                    h = vfHead[v1]
                    slotNext[s] = h
                    slotPrev[s] = -1
                    if h >= 0:
                        slotPrev[h] = s
                    vfHead[v1] = s
                    slotVert[s] = v1
            s = sn
        active[v2] = 0

        epoch += 1
        sv = vfHead[v1]
        while sv >= 0:
            b = (sv // 3) * 3
            if faces[b] >= 0:
                for k in range(3):
                    nb = faces[b + k]
                    if nb != v1 and nbStamp[nb] != epoch:
                        nbStamp[nb] = epoch
                        if active[nb] and not (locked_vert is not None and locked_vert[nb]):
                            _push_edge(heap, Q, P, version, v1, nb)
            sv = slotNext[sv]

    if on_progress:
        on_progress(1)
    out = build_output(np.array(P, np.float64), np.array(faces, np.int64))
    if locked_over:
        out["userData"]["lockedOverBudget"] = True
    return out


def _unlink(s, vfHead, slotNext, slotPrev, slotVert):
    v = slotVert[s]
    p = slotPrev[s]
    n = slotNext[s]
    if p < 0:
        vfHead[v] = n
    else:
        slotNext[p] = n
    if n >= 0:
        slotPrev[n] = p


def _link_violation(faces, vfHead, slotNext, v1, v2, lk, ep):
    s = vfHead[v1]
    while s >= 0:
        b = (s // 3) * 3
        a = faces[b]
        if a >= 0:
            bb, c = faces[b + 1], faces[b + 2]
            if a != v1:
                lk[a] = ep
            if bb != v1:
                lk[bb] = ep
            if c != v1:
                lk[c] = ep
        s = slotNext[s]
    shared = 0
    s = vfHead[v1]
    while s >= 0:
        b = (s // 3) * 3
        a = faces[b]
        if a >= 0:
            bb, c = faces[b + 1], faces[b + 2]
            if a == v2 or bb == v2 or c == v2:
                shared += 1
                apex = a if (a != v1 and a != v2) else (bb if (bb != v1 and bb != v2) else c)
                lk[apex] = ep + 1
        s = slotNext[s]
    if shared > 2:
        return True
    s = vfHead[v2]
    while s >= 0:
        b = (s // 3) * 3
        a = faces[b]
        if a >= 0:
            bb, c = faces[b + 1], faces[b + 2]
            if a != v2 and a != v1 and lk[a] == ep:
                return True
            if bb != v2 and bb != v1 and lk[bb] == ep:
                return True
            if c != v2 and c != v1 and lk[c] == ep:
                return True
        s = slotNext[s]
    return False


def _flipped(P, vfHead, slotNext, faces, vc, vo, npx, npy, npz):
    s = vfHead[vc]
    while s >= 0:
        b = (s // 3) * 3
        fa = faces[b]
        if fa >= 0:
            fb, fc = faces[b + 1], faces[b + 2]
            if not (fa == vo or fb == vo or fc == vo):
                oax, oay, oaz = P[fa * 3], P[fa * 3 + 1], P[fa * 3 + 2]
                obx, oby, obz = P[fb * 3], P[fb * 3 + 1], P[fb * 3 + 2]
                ocx, ocy, ocz = P[fc * 3], P[fc * 3 + 1], P[fc * 3 + 2]
                oux, ouy, ouz = obx - oax, oby - oay, obz - oaz
                ovx, ovy, ovz = ocx - oax, ocy - oay, ocz - oaz
                onx = ouy * ovz - ouz * ovy
                ony = ouz * ovx - oux * ovz
                onz = oux * ovy - ouy * ovx
                if fa == vc:
                    nax, nay, naz, nbx, nby, nbz, ncx, ncy, ncz = npx, npy, npz, obx, oby, obz, ocx, ocy, ocz
                elif fb == vc:
                    nax, nay, naz, nbx, nby, nbz, ncx, ncy, ncz = oax, oay, oaz, npx, npy, npz, ocx, ocy, ocz
                else:
                    nax, nay, naz, nbx, nby, nbz, ncx, ncy, ncz = oax, oay, oaz, obx, oby, obz, npx, npy, npz
                nux, nuy, nuz = nbx - nax, nby - nay, nbz - naz
                nvx, nvy, nvz = ncx - nax, ncy - nay, ncz - naz
                nnx = nuy * nvz - nuz * nvy
                nny = nuz * nvx - nux * nvz
                nnz = nux * nvy - nuy * nvx
                raw = onx * nnx + ony * nny + onz * nnz
                if raw < 0:
                    return True
                if raw * raw < FLIP_DOT_SQ * (onx * onx + ony * ony + onz * onz) * (nnx * nnx + nny * nny + nnz * nnz):
                    return True
        s = slotNext[s]
    return False


def _add_plane(Q, v, a, b, c, d):
    o = v * 10
    Q[o] += a * a
    Q[o + 1] += a * b
    Q[o + 2] += a * c
    Q[o + 3] += a * d
    Q[o + 4] += b * b
    Q[o + 5] += b * c
    Q[o + 6] += b * d
    Q[o + 7] += c * c
    Q[o + 8] += c * d
    Q[o + 9] += d * d


def _unit_normal(P, a, b, c):
    ux, uy, uz = P[b * 3] - P[a * 3], P[b * 3 + 1] - P[a * 3 + 1], P[b * 3 + 2] - P[a * 3 + 2]
    vx, vy, vz = P[c * 3] - P[a * 3], P[c * 3 + 1] - P[a * 3 + 1], P[c * 3 + 2] - P[a * 3 + 2]
    cnx, cny, cnz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    ln = math.sqrt(cnx * cnx + cny * cny + cnz * cnz) or 1
    return cnx / ln, cny / ln, cnz / ln


def _init_quadrics(Q, P, faces, face_count):
    for f in range(face_count):
        fa = faces[f * 3]
        if fa < 0:
            continue
        fb, fc = faces[f * 3 + 1], faces[f * 3 + 2]
        nx, ny, nz = _unit_normal(P, fa, fb, fc)
        d = -(nx * P[fa * 3] + ny * P[fa * 3 + 1] + nz * P[fa * 3 + 2])
        _add_plane(Q, fa, nx, ny, nz, d)
        _add_plane(Q, fb, nx, ny, nz, d)
        _add_plane(Q, fc, nx, ny, nz, d)


def _add_crease_quadrics(Q, P, faces, face_count):
    edges = {}  # (lo, hi) -> [f0, f1, num], first-occurrence order
    for f in range(face_count):
        if faces[f * 3] < 0:
            continue
        for e in range(3):
            va = faces[f * 3 + e]
            vb = faces[f * 3 + (e + 1) % 3]
            key = (va, vb) if va < vb else (vb, va)
            ent = edges.get(key)
            if ent is None:
                edges[key] = [f, 0, 1]
            elif ent[2] == 1:
                ent[1] = f
                ent[2] = 2
            else:
                ent[2] = 3
    sqrtW = math.sqrt(CREASE_WEIGHT)
    for (va, vb), (f0, f1, num) in edges.items():
        if num != 2:
            continue
        n0x, n0y, n0z = _unit_normal(P, faces[f0 * 3], faces[f0 * 3 + 1], faces[f0 * 3 + 2])
        n1x, n1y, n1z = _unit_normal(P, faces[f1 * 3], faces[f1 * 3 + 1], faces[f1 * 3 + 2])
        if n0x * n1x + n0y * n1y + n0z * n1z >= CREASE_COS:
            continue
        ex = P[vb * 3] - P[va * 3]
        ey = P[vb * 3 + 1] - P[va * 3 + 1]
        ez = P[vb * 3 + 2] - P[va * 3 + 2]
        el = math.sqrt(ex * ex + ey * ey + ez * ez) or 1
        edx, edy, edz = ex / el, ey / el, ez / el
        for nx, ny, nz in ((n0x, n0y, n0z), (n1x, n1y, n1z)):
            px = ny * edz - nz * edy
            py = nz * edx - nx * edz
            pz = nx * edy - ny * edx
            pl = math.sqrt(px * px + py * py + pz * pz)
            if pl < 1e-10:
                continue
            px /= pl
            py /= pl
            pz /= pl
            d = -(px * P[va * 3] + py * P[va * 3 + 1] + pz * P[va * 3 + 2])
            _add_plane(Q, va, px * sqrtW, py * sqrtW, pz * sqrtW, d * sqrtW)
            _add_plane(Q, vb, px * sqrtW, py * sqrtW, pz * sqrtW, d * sqrtW)


def _eval_q(Q, o, x, y, z):
    return (Q[o] * x * x + 2 * Q[o + 1] * x * y + 2 * Q[o + 2] * x * z + 2 * Q[o + 3] * x
            + Q[o + 4] * y * y + 2 * Q[o + 5] * y * z + 2 * Q[o + 6] * y
            + Q[o + 7] * z * z + 2 * Q[o + 8] * z
            + Q[o + 9])


def _push_edge(heap, Q, P, version, v1, v2):
    o1 = v1 * 10
    o2 = v2 * 10
    a00 = Q[o1] + Q[o2]
    a01 = Q[o1 + 1] + Q[o2 + 1]
    a02 = Q[o1 + 2] + Q[o2 + 2]
    a11 = Q[o1 + 4] + Q[o2 + 4]
    a12 = Q[o1 + 5] + Q[o2 + 5]
    a22 = Q[o1 + 7] + Q[o2 + 7]
    b0 = -(Q[o1 + 3] + Q[o2 + 3])
    b1 = -(Q[o1 + 6] + Q[o2 + 6])
    b2 = -(Q[o1 + 8] + Q[o2 + 8])
    det = a00 * (a11 * a22 - a12 * a12) - a01 * (a01 * a22 - a12 * a02) + a02 * (a01 * a12 - a11 * a02)
    max_el = max(abs(a00), abs(a01), abs(a02), abs(a11), abs(a12), abs(a22))
    thr = max_el * max_el * max_el * 1e-10
    if not (abs(det) < max(thr, 1e-30)):
        inv = 1 / det
        px = inv * (b0 * (a11 * a22 - a12 * a12) - a01 * (b1 * a22 - a12 * b2) + a02 * (b1 * a12 - a11 * b2))
        py = inv * (a00 * (b1 * a22 - a12 * b2) - b0 * (a01 * a22 - a12 * a02) + a02 * (a01 * b2 - b1 * a02))
        pz = inv * (a00 * (a11 * b2 - b1 * a12) - a01 * (a01 * b2 - b1 * a02) + b0 * (a01 * a12 - a11 * a02))
    else:
        x1, y1, z1 = P[v1 * 3], P[v1 * 3 + 1], P[v1 * 3 + 2]
        x2, y2, z2 = P[v2 * 3], P[v2 * 3 + 1], P[v2 * 3 + 2]
        mx, my, mz = (x1 + x2) / 2, (y1 + y2) / 2, (z1 + z2) / 2
        e1 = _eval_q(Q, o1, x1, y1, z1) + _eval_q(Q, o2, x1, y1, z1)
        e2 = _eval_q(Q, o1, x2, y2, z2) + _eval_q(Q, o2, x2, y2, z2)
        em = _eval_q(Q, o1, mx, my, mz) + _eval_q(Q, o2, mx, my, mz)
        e_min = min(e1, e2, em)
        tol = e_min * 1e-2 + 1e-12
        if em <= e_min + tol:
            px, py, pz = mx, my, mz
        elif e1 <= e2:
            px, py, pz = x1, y1, z1
        else:
            px, py, pz = x2, y2, z2
    cost = _eval_q(Q, o1, px, py, pz) + _eval_q(Q, o2, px, py, pz)
    dx = P[v2 * 3] - P[v1 * 3]
    dy = P[v2 * 3 + 1] - P[v1 * 3 + 1]
    dz = P[v2 * 3 + 2] - P[v1 * 3 + 2]
    heap.push((cost + (dx * dx + dy * dy + dz * dz) * 1e-8, v1, v2, version[v1], version[v2], px, py, pz))
