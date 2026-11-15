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
from .meshindex import first_ids, weld

QUANT_DEFAULT = 1e6
FLIP_DOT = 0.2
FLIP_DOT_SQ = FLIP_DOT * FLIP_DOT
CREASE_COS = 0.5
CREASE_WEIGHT = 1e4
DEFAULT_HARVEST_TOL = 0.005


class _Heap:
    """SoAHeap: entries (cost, v1, v2, ver1, ver2, px, py, pz) in a 1-indexed
    list, sifted exactly as the JS sifts (comparing costs only, so equal costs
    pop in the JS's order). The costs are kept in a parallel list c."""

    __slots__ = ("a", "c")

    def __init__(self):
        self.a = [None]
        self.c = [None]

    def __len__(self):
        return len(self.a) - 1

    def extend(self, entries):
        """push() each entry in turn."""
        a, c = self.a, self.c
        for e in entries:
            cost = e[0]
            a.append(e)
            c.append(cost)
            idx = len(a) - 1
            while idx > 1:
                parent = idx >> 1
                pc = c[parent]
                if pc <= cost:
                    break
                a[idx] = a[parent]
                c[idx] = pc
                idx = parent
            a[idx] = e
            c[idx] = cost

    def push(self, e):
        a, c = self.a, self.c
        cost = e[0]
        a.append(e)
        c.append(cost)
        idx = len(a) - 1
        while idx > 1:
            parent = idx >> 1
            pc = c[parent]
            if pc <= cost:
                break
            a[idx] = a[parent]
            c[idx] = pc
            idx = parent
        a[idx] = e
        c[idx] = cost

    def pop(self):
        a, c = self.a, self.c
        n = len(a) - 1
        if n == 0:
            return None
        top = a[1]
        last = a.pop()
        cost = c.pop()
        n -= 1
        if n > 0:
            idx = 1
            while True:
                l = idx << 1
                if l > n:
                    break
                cl = c[l]
                if l < n:
                    cr = c[l + 1]
                    if cl < cost:
                        child = l + 1 if cr < cl else l
                    elif cr < cost:
                        child = l + 1
                    else:
                        break
                elif cl < cost:
                    child = l
                else:
                    break
                a[idx] = a[child]
                c[idx] = c[child]
                idx = child
            a[idx] = last
            c[idx] = cost
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

    F3 = faces_np.reshape(-1, 3)
    Pn = pos_np.reshape(-1, 3)
    fn = _face_unit_normals(Pn, F3)
    edges = _edges(F3)
    Q = _quadrics(Pn, F3, fn, edges, vert_count).reshape(-1).tolist()

    # Linked vertex-face incidence: slot s = f*3+k, each vertex's list newest
    # slot first (the JS links them in at the head in slot order).
    S = face_count * 3
    vfHead, slotNext, slotPrev = _incidence(faces_np.reshape(-1), vert_count)
    slotVert = faces_np.tolist()
    slotLive = [1] * S

    active = [1] * vert_count
    version = [0] * vert_count
    nbStamp = [0] * vert_count
    epoch = 1
    lkStamp = [0] * vert_count
    lkEpoch = 1
    active_faces = face_count

    heap = _Heap()
    va, vb = edges["a"], edges["b"]
    if locked_vert is not None:
        keep = ~(lv[va] | lv[vb])
        va, vb = va[keep], vb[keep]
    heap.extend(_edge_entries(np.asarray(Q).reshape(-1, 10), Pn, va, vb))

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


def _face_unit_normals(Pn, F3):
    """Each face's unit normal, as _unit_normal works it (a zero length stays
    a zero vector), for all faces at once: (F, 3) doubles."""
    A, B, C = Pn[F3[:, 0]], Pn[F3[:, 1]], Pn[F3[:, 2]]
    ux, uy, uz = B[:, 0] - A[:, 0], B[:, 1] - A[:, 1], B[:, 2] - A[:, 2]
    vx, vy, vz = C[:, 0] - A[:, 0], C[:, 1] - A[:, 1], C[:, 2] - A[:, 2]
    cnx, cny, cnz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    ln = np.sqrt(cnx * cnx + cny * cny + cnz * cnz)
    ln = np.where(ln == 0, 1.0, ln)
    return np.stack([cnx / ln, cny / ln, cnz / ln], 1)


def _edges(F3):
    """Undirected edges in order of first occurrence (face, then edge 0-1,
    1-2, 2-0): {"a", "b"} that occurrence's direction, {"lo", "hi"} the key,
    {"f0", "f1"} the first two faces on it, {"num"} how many faces."""
    nf = len(F3)
    E = np.stack([F3, np.roll(F3, -1, axis=1)], 2).reshape(-1, 2)
    lo, hi = np.minimum(E[:, 0], E[:, 1]), np.maximum(E[:, 0], E[:, 1])
    if nf == 0:
        z = np.zeros(0, np.int64)
        return {"a": z, "b": z, "lo": z, "hi": z, "f0": z, "f1": z, "num": z}
    ids, firsts = first_ids(np.stack([lo, hi], 1))
    num = np.bincount(ids, minlength=len(firsts))
    occ = np.argsort(ids, kind="stable")
    starts = np.concatenate([[0], np.cumsum(num)[:-1]])
    second = occ[np.minimum(starts + 1, len(occ) - 1)]
    f1 = np.where(num >= 2, second // 3, 0)
    return {"a": E[firsts, 0], "b": E[firsts, 1], "lo": lo[firsts], "hi": hi[firsts],
            "f0": firsts // 3, "f1": f1, "num": num}


def _plane_terms(a, b, c, d):
    return np.stack([a * a, a * b, a * c, a * d, b * b, b * c, b * d, c * c, c * d, d * d], 1)


def _quadrics(Pn, F3, fn, edges, vert_count):
    """The per-vertex quadrics (V, 10): every face's plane added to its three
    vertices in face order, then the crease penalty planes in edge order, each
    vertex's sum taken in exactly that order (so the doubles match the JS's
    one-by-one accumulation)."""
    nx, ny, nz = fn[:, 0], fn[:, 1], fn[:, 2]
    A = Pn[F3[:, 0]]
    d = -(nx * A[:, 0] + ny * A[:, 1] + nz * A[:, 2])
    T = _plane_terms(nx, ny, nz, d)
    vids = [F3.reshape(-1)]
    terms = [np.repeat(T, 3, axis=0)]

    ok = edges["num"] == 2
    lo, hi, f0, f1 = edges["lo"][ok], edges["hi"][ok], edges["f0"][ok], edges["f1"][ok]
    n0, n1 = fn[f0], fn[f1]
    dot = n0[:, 0] * n1[:, 0] + n0[:, 1] * n1[:, 1] + n0[:, 2] * n1[:, 2]
    cr = ~(dot >= CREASE_COS)
    lo, hi, n0, n1 = lo[cr], hi[cr], n0[cr], n1[cr]
    if len(lo):
        Pa, Pb = Pn[lo], Pn[hi]
        ex, ey, ez = Pb[:, 0] - Pa[:, 0], Pb[:, 1] - Pa[:, 1], Pb[:, 2] - Pa[:, 2]
        el = np.sqrt(ex * ex + ey * ey + ez * ez)
        el = np.where(el == 0, 1.0, el)
        edx, edy, edz = ex / el, ey / el, ez / el
        sqrtW = math.sqrt(CREASE_WEIGHT)
        m = len(lo)
        cv = np.empty((m, 2, 2), np.int64)  # (edge, plane, lo/hi)
        cv[:, :, 0] = lo[:, None]
        cv[:, :, 1] = hi[:, None]
        ct = np.zeros((m, 2, 2, 10))
        live = np.zeros((m, 2, 2), bool)
        for j, n in enumerate((n0, n1)):
            px = n[:, 1] * edz - n[:, 2] * edy
            py = n[:, 2] * edx - n[:, 0] * edz
            pz = n[:, 0] * edy - n[:, 1] * edx
            pl = np.sqrt(px * px + py * py + pz * pz)
            keep = ~(pl < 1e-10)
            with np.errstate(all="ignore"):
                px, py, pz = px / pl, py / pl, pz / pl
            dd = -(px * Pa[:, 0] + py * Pa[:, 1] + pz * Pa[:, 2])
            t = _plane_terms(px * sqrtW, py * sqrtW, pz * sqrtW, dd * sqrtW)
            ct[:, j, 0] = t
            ct[:, j, 1] = t
            live[:, j, :] = keep[:, None]
        live = live.reshape(-1)
        vids.append(cv.reshape(-1)[live])
        terms.append(ct.reshape(-1, 10)[live])

    vid = np.concatenate(vids)
    term = np.concatenate(terms)
    Q = np.zeros((vert_count, 10))
    if len(vid) == 0:
        return Q
    order = np.argsort(vid, kind="stable")
    sv = vid[order]
    cnt = np.bincount(sv, minlength=vert_count)
    starts = np.concatenate([[0], np.cumsum(cnt)[:-1]])
    for r in range(int(cnt.max())):
        has = np.nonzero(cnt > r)[0]
        Q[has] += term[order[starts[has] + r]]
    return Q


def _incidence(faces_flat, vert_count):
    """vfHead, slotNext, slotPrev as lists: each vertex's slots linked newest
    first, as pushing every face's corners at the head in slot order leaves
    them."""
    S = len(faces_flat)
    head = np.full(vert_count, -1, np.int64)
    nxt = np.full(S, -1, np.int64)
    prv = np.full(S, -1, np.int64)
    if S:
        so = np.argsort(faces_flat, kind="stable")
        sv = faces_flat[so]
        same = sv[1:] == sv[:-1]
        nxt[so[1:]] = np.where(same, so[:-1], -1)
        prv[so[:-1]] = np.where(same, so[1:], -1)
        last = np.append(~same, True)
        head[sv[last]] = so[last]
    return head.tolist(), nxt.tolist(), prv.tolist()


def _pymax(*xs):
    """Python's max over arrays elementwise (a later value wins only when greater)."""
    m = xs[0]
    for x in xs[1:]:
        m = np.where(x > m, x, m)
    return m


def _pymin(*xs):
    m = xs[0]
    for x in xs[1:]:
        m = np.where(x < m, x, m)
    return m


def _eval_qv(Q, x, y, z):
    return (Q[:, 0] * x * x + 2 * Q[:, 1] * x * y + 2 * Q[:, 2] * x * z + 2 * Q[:, 3] * x
            + Q[:, 4] * y * y + 2 * Q[:, 5] * y * z + 2 * Q[:, 6] * y
            + Q[:, 7] * z * z + 2 * Q[:, 8] * z
            + Q[:, 9])


def _edge_entries(Qv, Pn, v1, v2):
    """_push_edge for many edges at once (every version 0): the heap entries,
    in order."""
    if len(v1) == 0:
        return []
    Q1, Q2 = Qv[v1], Qv[v2]
    a00 = Q1[:, 0] + Q2[:, 0]
    a01 = Q1[:, 1] + Q2[:, 1]
    a02 = Q1[:, 2] + Q2[:, 2]
    a11 = Q1[:, 4] + Q2[:, 4]
    a12 = Q1[:, 5] + Q2[:, 5]
    a22 = Q1[:, 7] + Q2[:, 7]
    b0 = -(Q1[:, 3] + Q2[:, 3])
    b1 = -(Q1[:, 6] + Q2[:, 6])
    b2 = -(Q1[:, 8] + Q2[:, 8])
    det = a00 * (a11 * a22 - a12 * a12) - a01 * (a01 * a22 - a12 * a02) + a02 * (a01 * a12 - a11 * a02)
    max_el = _pymax(np.abs(a00), np.abs(a01), np.abs(a02), np.abs(a11), np.abs(a12), np.abs(a22))
    thr = max_el * max_el * max_el * 1e-10
    solve = ~(np.abs(det) < _pymax(thr, np.full_like(thr, 1e-30)))
    with np.errstate(all="ignore"):
        inv = 1 / det
        sx = inv * (b0 * (a11 * a22 - a12 * a12) - a01 * (b1 * a22 - a12 * b2) + a02 * (b1 * a12 - a11 * b2))
        sy = inv * (a00 * (b1 * a22 - a12 * b2) - b0 * (a01 * a22 - a12 * a02) + a02 * (a01 * b2 - b1 * a02))
        sz = inv * (a00 * (a11 * b2 - b1 * a12) - a01 * (a01 * b2 - b1 * a02) + b0 * (a01 * a12 - a11 * a02))
    P1, P2 = Pn[v1], Pn[v2]
    x1, y1, z1 = P1[:, 0], P1[:, 1], P1[:, 2]
    x2, y2, z2 = P2[:, 0], P2[:, 1], P2[:, 2]
    mx, my, mz = (x1 + x2) / 2, (y1 + y2) / 2, (z1 + z2) / 2
    e1 = _eval_qv(Q1, x1, y1, z1) + _eval_qv(Q2, x1, y1, z1)
    e2 = _eval_qv(Q1, x2, y2, z2) + _eval_qv(Q2, x2, y2, z2)
    em = _eval_qv(Q1, mx, my, mz) + _eval_qv(Q2, mx, my, mz)
    e_min = _pymin(e1, e2, em)
    tol = e_min * 1e-2 + 1e-12
    use_m = em <= e_min + tol
    use_1 = ~use_m & (e1 <= e2)
    px = np.where(solve, sx, np.where(use_m, mx, np.where(use_1, x1, x2)))
    py = np.where(solve, sy, np.where(use_m, my, np.where(use_1, y1, y2)))
    pz = np.where(solve, sz, np.where(use_m, mz, np.where(use_1, z1, z2)))
    cost = _eval_qv(Q1, px, py, pz) + _eval_qv(Q2, px, py, pz)
    dx, dy, dz = x2 - x1, y2 - y1, z2 - z1
    cost = cost + (dx * dx + dy * dy + dz * dz) * 1e-8
    zero = [0] * len(v1)
    return list(zip(cost.tolist(), v1.tolist(), v2.tolist(), zero, zero, px.tolist(), py.tolist(), pz.tolist()))


def _push_edge(heap, Q, P, version, v1, v2):
    o1 = v1 * 10
    o2 = v2 * 10
    q0, q1, q2, q3, q4, q5, q6, q7, q8, q9 = Q[o1:o1 + 10]
    r0, r1, r2, r3, r4, r5, r6, r7, r8, r9 = Q[o2:o2 + 10]
    a00 = q0 + r0
    a01 = q1 + r1
    a02 = q2 + r2
    a11 = q4 + r4
    a12 = q5 + r5
    a22 = q7 + r7
    b0 = -(q3 + r3)
    b1 = -(q6 + r6)
    b2 = -(q8 + r8)
    det = a00 * (a11 * a22 - a12 * a12) - a01 * (a01 * a22 - a12 * a02) + a02 * (a01 * a12 - a11 * a02)
    max_el = max(abs(a00), abs(a01), abs(a02), abs(a11), abs(a12), abs(a22))
    thr = max_el * max_el * max_el * 1e-10
    i1 = v1 * 3
    i2 = v2 * 3
    x1, y1, z1 = P[i1], P[i1 + 1], P[i1 + 2]
    x2, y2, z2 = P[i2], P[i2 + 1], P[i2 + 2]
    if not (abs(det) < max(thr, 1e-30)):
        inv = 1 / det
        px = inv * (b0 * (a11 * a22 - a12 * a12) - a01 * (b1 * a22 - a12 * b2) + a02 * (b1 * a12 - a11 * b2))
        py = inv * (a00 * (b1 * a22 - a12 * b2) - b0 * (a01 * a22 - a12 * a02) + a02 * (a01 * b2 - b1 * a02))
        pz = inv * (a00 * (a11 * b2 - b1 * a12) - a01 * (a01 * b2 - b1 * a02) + b0 * (a01 * a12 - a11 * a02))
    else:
        # _eval_q(Q, o1, ..) + _eval_q(Q, o2, ..) at the ends and the midpoint, inlined.
        x, y, z = x1, y1, z1
        e1 = ((q0 * x * x + 2 * q1 * x * y + 2 * q2 * x * z + 2 * q3 * x + q4 * y * y + 2 * q5 * y * z
               + 2 * q6 * y + q7 * z * z + 2 * q8 * z + q9)
              + (r0 * x * x + 2 * r1 * x * y + 2 * r2 * x * z + 2 * r3 * x + r4 * y * y + 2 * r5 * y * z
                 + 2 * r6 * y + r7 * z * z + 2 * r8 * z + r9))
        x, y, z = x2, y2, z2
        e2 = ((q0 * x * x + 2 * q1 * x * y + 2 * q2 * x * z + 2 * q3 * x + q4 * y * y + 2 * q5 * y * z
               + 2 * q6 * y + q7 * z * z + 2 * q8 * z + q9)
              + (r0 * x * x + 2 * r1 * x * y + 2 * r2 * x * z + 2 * r3 * x + r4 * y * y + 2 * r5 * y * z
                 + 2 * r6 * y + r7 * z * z + 2 * r8 * z + r9))
        mx, my, mz = (x1 + x2) / 2, (y1 + y2) / 2, (z1 + z2) / 2
        x, y, z = mx, my, mz
        em = ((q0 * x * x + 2 * q1 * x * y + 2 * q2 * x * z + 2 * q3 * x + q4 * y * y + 2 * q5 * y * z
               + 2 * q6 * y + q7 * z * z + 2 * q8 * z + q9)
              + (r0 * x * x + 2 * r1 * x * y + 2 * r2 * x * z + 2 * r3 * x + r4 * y * y + 2 * r5 * y * z
                 + 2 * r6 * y + r7 * z * z + 2 * r8 * z + r9))
        e_min = min(e1, e2, em)
        tol = e_min * 1e-2 + 1e-12
        if em <= e_min + tol:
            px, py, pz = mx, my, mz
        elif e1 <= e2:
            px, py, pz = x1, y1, z1
        else:
            px, py, pz = x2, y2, z2
    x, y, z = px, py, pz
    cost = ((q0 * x * x + 2 * q1 * x * y + 2 * q2 * x * z + 2 * q3 * x + q4 * y * y + 2 * q5 * y * z
             + 2 * q6 * y + q7 * z * z + 2 * q8 * z + q9)
            + (r0 * x * x + 2 * r1 * x * y + 2 * r2 * x * z + 2 * r3 * x + r4 * y * y + 2 * r5 * y * z
               + 2 * r6 * y + r7 * z * z + 2 * r8 * z + r9))
    dx = x2 - x1
    dy = y2 - y1
    dz = z2 - z1
    heap.push((cost + (dx * dx + dy * dy + dz * dz) * 1e-8, v1, v2, version[v1], version[v2], px, py, pz))
