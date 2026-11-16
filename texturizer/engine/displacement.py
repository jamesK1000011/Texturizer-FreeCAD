# SPDX-License-Identifier: AGPL-3.0-only
# Port of js/displacement.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# Every welded position is moved once, by one vector, so all copies of it land
# on the same point (watertight): along the area-weighted smooth normal by the
# texture height sampled there, or, on a sharp crease, to where the displaced
# faces meet (the mitre, _crease_moves). Several layers composite per
# position: a layer covers the ones below where its mask lets it through, or
# adds to them (blendAdd). Blend weights come from a Laplacian-smoothed
# normal so seams stay quiet on noisy meshes.
#
# Vectorised per position; the sums run in the JS's corner order (bincount is
# a sequential sum), so the arithmetic is the JS's. Between runs on the same
# mesh (a Rotate drag, a slider) the weld and the blend-normal smoothing are
# kept and checked, not recomputed (_KEEP).
import math

import numpy as np

from ._js import F32
from .mapping import (MODE_CUBIC, compute_uv, cubic_blend_weights, g, is_rotate_around_z, is_stamp_active,
                      rotate_side_frame, scale_mm_to_relative, stamp_frame, stamp_uv)
from .meshindex import weld

CREASE_GROUP_COS = math.cos(20 * math.pi / 180)
SHARP_CREASE_COS = math.cos(50 * math.pi / 180)
MAX_GROUPS = 6
MITER_LIMIT = 2
MITER_RIDGE = 0.01
FOLD_GUARD_ROUNDS = 6
FOLD_GUARD_MIN_COS = 0.05
QUANT = 1e5


def _frac(x):
    """np.fmod(np.fmod(x, 1) + 1, 1), exactly: x - trunc(x) is fmod(x, 1) (an
    exact subtraction; only the sign of a zero differs, and + 1 drops it)."""
    y = x - np.trunc(x) + 1
    return y - np.trunc(y)


def sample_bilinear(R, w, h, u, v):
    """sampleBilinear on the red channel R (h*w float64, already /255), GL-exact wrap."""
    u = _frac(u)
    v = 1 - _frac(v)
    fx = u * w - 0.5
    fy = v * h - 0.5
    x0 = np.floor(fx)
    y0 = np.floor(fy)
    tx = fx - x0
    ty = fy - y0
    x0 = x0.astype(np.int64)
    y0 = y0.astype(np.int64)
    # u in [0, 1) and v in (0, 1]: x0 and y0 are -1 .. size - 1, so the
    # wraps are one step each (the fmods' values, without a division).
    x1 = x0 + 1
    x1[x1 >= w] -= w
    x0[x0 < 0] += w
    y1 = y0 + 1
    y1[y1 >= h] -= h
    y0[y0 < 0] += h
    r0, r1 = y0 * w, y1 * w
    v00 = R.take(r0 + x0)
    v10 = R.take(r0 + x1)
    v01 = R.take(r1 + x0)
    v11 = R.take(r1 + x1)
    return v00 * (1 - tx) * (1 - ty) + v10 * tx * (1 - ty) + v01 * (1 - tx) * ty + v11 * tx * ty


def _red(img):
    return img.data[:, :, 0].astype(np.float64).reshape(-1) / 255


def _projected_grey(R, w, h, pos, nrm, mode, s, bounds):
    grey = np.zeros(len(pos))
    uvw = compute_uv(pos, nrm, mode, s, bounds)
    if len(uvw) > 1:   # one sampling pass for all projections (element-wise: the same values)
        n = len(pos)
        got = sample_bilinear(R, w, h, np.concatenate([np.broadcast_to(u, n) for u, _v, _w in uvw]),
                              np.concatenate([np.broadcast_to(v, n) for _u, v, _w in uvw]))
        for i, (_u, _v, sw) in enumerate(uvw):
            grey = grey + got[i * n:(i + 1) * n] * sw
        return grey
    for su, sv, sw in uvw:
        grey = grey + sample_bilinear(R, w, h, su, sv) * sw
    return grey


def _cubic_uv(ru, rv, rel, s, rot, aU, aV):
    u = (ru * aU) / rel[0] + (s.get("offsetU") or 0)
    v = (rv * aV) / rel[1] + (s.get("offsetV") or 0)
    if rot != 0:
        c, sn = math.cos(rot), math.sin(rot)
        u = u - 0.5
        v = v - 0.5
        a = c * u - sn * v
        b = sn * u + c * v
        u = a + 0.5
        v = b + 0.5
    return u - np.floor(u), v - np.floor(v)


def _cubic_grey(R, w, h, pos, wX, wY, wZ, fx, fy, fz, bounds, md, rel, s, rot, aU, aV):
    """_cubicGrey: the three axis projections blended by (wX, wY, wZ), each
    added only where its weight is positive, in the JS's order (X, Y, Z)."""
    mn = bounds["min"]
    x, y, z = pos[:, 0], pos[:, 1], pos[:, 2]
    sx, sy, sfx, sfy, srot = x - mn[0], y - mn[1], fx, fy, rot
    if is_rotate_around_z(s):
        # Only the side terms read these, and only where their weight is positive.
        sx, sy, sfx, sfy = rotate_side_frame(x, y, fx, fy, bounds, math.cos(rot), math.sin(rot))
        srot = 0.0
    grey = np.zeros(len(pos))
    zz = (z - mn[2]) / md
    raw = sy / md
    raw = np.where(sfx < 0, -raw, raw)
    u, v = _cubic_uv(raw, zz, rel, s, srot, aU, aV)
    grey = np.where(wX > 0, grey + sample_bilinear(R, w, h, u, v) * wX, grey)
    raw = sx / md
    raw = np.where(sfy > 0, -raw, raw)
    u, v = _cubic_uv(raw, zz, rel, s, srot, aU, aV)
    grey = np.where(wY > 0, grey + sample_bilinear(R, w, h, u, v) * wY, grey)
    raw = (x - mn[0]) / md
    raw = np.where(fz < 0, -raw, raw)
    u, v = _cubic_uv(raw, (y - mn[1]) / md, rel, s, rot, aU, aV)
    grey = np.where(wZ > 0, grey + sample_bilinear(R, w, h, u, v) * wZ, grey)
    return grey


def _stamp_grey(R, w, h, u, v):
    hu, hv = 0.5 / w, 0.5 / h
    return sample_bilinear(R, w, h, np.minimum(np.maximum(u, hu), 1 - hu), np.minimum(np.maximum(v, hv), 1 - hv))


def _side_normal(s, x, y, z):
    if not is_rotate_around_z(s):
        return x, y, z
    r = g(s, "rotation", 0) * math.pi / 180
    c, sn = math.cos(r), math.sin(r)
    return c * x - sn * y, sn * x + c * y, z


def _curve(t, mode):
    if mode == "scurve":
        return t * t * (3 - 2 * t)
    if mode == "ease":
        return t * t
    return t


def falloff_field(lset, unique, mf_masked, mf_total, pinned, idpos):
    """_buildFalloffField: per position 0 at the boundary .. 1 beyond the falloff, or None."""
    r = g(lset, "boundaryFalloff", 0)
    if not (r > 0):
        return None
    with np.errstate(all="ignore"):
        mf = np.where(mf_total > 0, mf_masked / np.where(mf_total > 0, mf_total, 1), 0.0)
    bnd = pinned | ((mf > 0) & (mf < 1))
    if not bnd.any():
        return None
    B = idpos[bnd]
    out = np.ones(unique)
    q = ~(mf > 0) & ~pinned
    Q = idpos[q]
    best = np.full(len(Q), float(r) * r)
    if len(Q):
        lo = B.min(0) - r - 1e-3
        cell = np.floor((B - lo) / r).astype(np.int64)
        dims = cell.max(0) + 3
        key = (cell[:, 0] * dims[1] + cell[:, 1]) * dims[2] + cell[:, 2]
        order = np.argsort(key, kind="stable")
        ks, Bs = key[order], B[order]
        qc = np.floor((Q - lo) / r).astype(np.int64)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    c = qc + np.array([dx, dy, dz])
                    okc = (c >= 0).all(1) & (c < dims).all(1)
                    k = (c[:, 0] * dims[1] + c[:, 1]) * dims[2] + c[:, 2]
                    st = np.searchsorted(ks, k, "left")
                    en = np.searchsorted(ks, k, "right")
                    cnt = np.where(okc, en - st, 0)
                    m = int(cnt.max()) if len(cnt) else 0
                    for j in range(m):
                        sel = cnt > j
                        idx = st[sel] + j
                        d = Q[sel] - Bs[idx]
                        d2 = d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1] + d[:, 2] * d[:, 2]
                        best[sel] = np.minimum(best[sel], d2)
    t = np.sqrt(best) * (1 / r)
    val = np.where(best < r * r, _curve(t, g(lset, "boundaryFalloffCurve", "linear")), 1.0)
    out[q] = val
    return out


class _Creases:
    pass


def _find_creases(nrm, vid, unique, firsts=None):
    n = len(vid)
    if firsts is None:   # each id's first corner (the weld's own firsts are these)
        firsts = np.full(unique, -1, np.int64)
        rev = np.arange(n)[::-1]
        firsts[vid[rev]] = rev
    f = firsts[vid]
    dot = nrm[:, 0] * nrm[f, 0] + nrm[:, 1] * nrm[f, 1] + nrm[:, 2] * nrm[f, 2]
    cand = np.zeros(unique, bool)
    cand[vid[(np.arange(n) != f) & (dot < CREASE_GROUP_COS)]] = True
    ncand = int(cand.sum())
    if ncand == 0:
        return None
    cr = _Creases()
    cr.slot_of = np.full(unique, -1, np.int64)
    cr.vid_of = np.nonzero(cand)[0]
    cr.slot_of[cr.vid_of] = np.arange(ncand)
    cr.count = ncand
    at = np.nonzero(cr.slot_of[vid] >= 0)[0]
    group_of = np.zeros(n, np.int64)
    groups = [0] * ncand
    rep = [[] for _ in range(ncand)]
    slots = cr.slot_of[vid[at]].tolist()
    nl = nrm[at].tolist()
    for k, i in enumerate(at.tolist()):
        s = slots[k]
        nx, ny, nz = nl[k]
        best, bd = -1, -math.inf
        for gi, (rx, ry, rz) in enumerate(rep[s]):
            d = nx * rx + ny * ry + nz * rz
            if d > bd:
                bd, best = d, gi
        if (best < 0 or bd < CREASE_GROUP_COS) and groups[s] < MAX_GROUPS:
            best = groups[s]
            groups[s] += 1
            rep[s].append((nx, ny, nz))
        group_of[i] = best
    cr.group_of = group_of
    cr.groups = np.array(groups, np.int64)
    cr.start = np.concatenate([[0], np.cumsum(cr.groups)])
    m = int(cr.start[-1])
    cr.group_count = m
    for k in ("nrmX", "nrmY", "nrmZ", "inX", "inY", "inZ", "area", "masked"):
        setattr(cr, k, np.zeros(m))
    cr.excluded = np.zeros(m, np.uint8)
    cr.released = np.zeros(ncand, np.uint8)
    cr.active = None
    cr.extra_start = None
    cr.extra_nodes = 0
    cr.corners = at
    return cr


def _finish_creases(cr):
    cr.active = np.zeros(cr.count, np.uint8)
    cr.extra_start = np.zeros(cr.count, np.int64)
    extra = active = 0
    NX, NY, NZ = cr.nrmX.tolist(), cr.nrmY.tolist(), cr.nrmZ.tolist()
    IX, IY, IZ = cr.inX.tolist(), cr.inY.tolist(), cr.inZ.tolist()
    AR = cr.area.tolist()
    starts, groups = cr.start.tolist(), cr.groups.tolist()
    for s in range(cr.count):
        base, n = starts[s], groups[s]
        valid = 0
        for gi in range(n):
            k = base + gi
            ln = math.sqrt(NX[k] * NX[k] + NY[k] * NY[k] + NZ[k] * NZ[k])
            if ln > 0 and AR[k] > 0:
                NX[k] /= ln
                NY[k] /= ln
                NZ[k] /= ln
                valid += 1
            else:
                AR[k] = 0.0
                NX[k] = NY[k] = NZ[k] = 0.0
        mind = 1.0
        for a in range(n):
            ka = base + a
            if not (AR[ka] > 0):
                continue
            for b in range(a + 1, n):
                kb = base + b
                if not (AR[kb] > 0):
                    continue
                d = NX[ka] * NX[kb] + NY[ka] * NY[kb] + NZ[ka] * NZ[kb]
                if d < mind:
                    mind = d
        if valid < 2 or mind > SHARP_CREASE_COS:
            cr.slot_of[cr.vid_of[s]] = -1
            continue
        cr.active[s] = 1
        active += 1
        cr.extra_start[s] = extra
        extra += n - 1
        ex = ey = ez = 0.0
        if valid == 2:
            ka = kb = -1
            for gi in range(n):
                if AR[base + gi] > 0:
                    if ka < 0:
                        ka = base + gi
                    else:
                        kb = base + gi
            ex = NY[ka] * NZ[kb] - NZ[ka] * NY[kb]
            ey = NZ[ka] * NX[kb] - NX[ka] * NZ[kb]
            ez = NX[ka] * NY[kb] - NY[ka] * NX[kb]
            el = math.sqrt(ex * ex + ey * ey + ez * ez)
            if el > 1e-9:
                ex, ey, ez = ex / el, ey / el, ez / el
            else:
                ex = ey = ez = 0.0
        for gi in range(n):
            k = base + gi
            tx, ty, tz = IX[k], IY[k], IZ[k]
            tn = tx * NX[k] + ty * NY[k] + tz * NZ[k]
            tx -= tn * NX[k]
            ty -= tn * NY[k]
            tz -= tn * NZ[k]
            te = tx * ex + ty * ey + tz * ez
            tx -= te * ex
            ty -= te * ey
            tz -= te * ez
            tl = math.sqrt(tx * tx + ty * ty + tz * tz)
            if AR[k] > 0 and tl > 1e-12:
                IX[k], IY[k], IZ[k] = tx / tl, ty / tl, tz / tl
            else:
                IX[k] = IY[k] = IZ[k] = 0.0
    cr.nrmX, cr.nrmY, cr.nrmZ = np.array(NX), np.array(NY), np.array(NZ)
    cr.inX, cr.inY, cr.inZ = np.array(IX), np.array(IY), np.array(IZ)
    cr.area = np.array(AR)
    cr.extra_nodes = extra
    return active > 0


def _group_node(cr, unique, s, gi):
    return cr.vid_of[s] if gi == 0 else unique + cr.extra_start[s] + gi - 1


def _crease_moves(cr, gacc, snx, sny, snz):
    out = np.zeros(cr.count * 3)
    NX, NY, NZ = cr.nrmX.tolist(), cr.nrmY.tolist(), cr.nrmZ.tolist()
    IX, IY, IZ = cr.inX.tolist(), cr.inY.tolist(), cr.inZ.tolist()
    AR, GA = cr.area.tolist(), gacc.tolist()
    starts, groups = cr.start.tolist(), cr.groups.tolist()
    for s in np.nonzero(cr.active)[0].tolist():
        base, n = starts[s], groups[s]
        hs = asum = hmax = 0.0
        for gi in range(n):
            k = base + gi
            a = AR[k]
            if not (a > 0):
                continue
            h = GA[k]
            hs += a * h
            asum += a
            if abs(h) > hmax:
                hmax = abs(h)
        if hmax == 0:
            continue
        vid = cr.vid_of[s]
        hm = hs / asum if asum > 0 else 0
        d0x, d0y, d0z = snx[vid] * hm, sny[vid] * hm, snz[vid] * hm
        m00 = m11 = m22 = MITER_RIDGE
        m01 = m02 = m12 = 0.0
        rx, ry, rz = MITER_RIDGE * d0x, MITER_RIDGE * d0y, MITER_RIDGE * d0z
        for gi in range(n):
            k = base + gi
            if not (AR[k] > 0):
                continue
            nx, ny, nz, h = NX[k], NY[k], NZ[k], GA[k]
            m00 += nx * nx
            m01 += nx * ny
            m02 += nx * nz
            m11 += ny * ny
            m12 += ny * nz
            m22 += nz * nz
            rx += h * nx
            ry += h * ny
            rz += h * nz
        c00, c01, c02 = m11 * m22 - m12 * m12, m02 * m12 - m01 * m22, m01 * m12 - m02 * m11
        det = m00 * c00 + m01 * c01 + m02 * c02
        dx, dy, dz = d0x, d0y, d0z
        if abs(det) > 1e-12:
            c11, c12, c22 = m00 * m22 - m02 * m02, m01 * m02 - m00 * m12, m00 * m11 - m01 * m01
            dx = (c00 * rx + c01 * ry + c02 * rz) / det
            dy = (c01 * rx + c11 * ry + c12 * rz) / det
            dz = (c02 * rx + c12 * ry + c22 * rz) / det
        for _r in range(2):
            for gi in range(n):
                k = base + gi
                tx, ty, tz = IX[k], IY[k], IZ[k]
                dt = dx * tx + dy * ty + dz * tz
                if dt > 0:
                    dx -= dt * tx
                    dy -= dt * ty
                    dz -= dt * tz
        ln = math.sqrt(dx * dx + dy * dy + dz * dz)
        lim = MITER_LIMIT * hmax
        if ln > lim:
            f = lim / ln
            dx *= f
            dy *= f
            dz *= f
        out[s * 3], out[s * 3 + 1], out[s * 3 + 2] = dx, dy, dz
    return out


def _guard_released_folds(cr, moves, pos, vid, acc, snx, sny, snz):
    if not cr.released.any():
        return
    NX, NY, NZ, AR = cr.nrmX, cr.nrmY, cr.nrmZ, cr.area
    for s in np.nonzero(cr.released)[0].tolist():
        base = int(cr.start[s])
        dx, dy, dz = moves[s * 3], moves[s * 3 + 1], moves[s * 3 + 2]
        for _r in range(3):
            for gi in range(int(cr.groups[s])):
                k = base + gi
                if cr.excluded[k] != 1 or not (AR[k] > 0):
                    continue
                nx, ny, nz = NX[k], NY[k], NZ[k]
                dn = dx * nx + dy * ny + dz * nz
                dx -= dn * nx
                dy -= dn * ny
                dz -= dn * nz
        moves[s * 3], moves[s * 3 + 1], moves[s * 3 + 2] = dx, dy, dz
    tri = vid.reshape(-1, 3)
    slot = cr.slot_of[tri]
    rel = (slot >= 0) & (cr.released[np.maximum(slot, 0)] == 1)
    cand = np.nonzero(rel.any(1))[0].tolist()
    scale = np.ones(cr.count)
    P = pos.reshape(-1, 9)
    for rnd in range(FOLD_GUARD_ROUNDS + 1):
        bad = 0
        for t in cand:
            p = P[t]
            ax, ay, az = p[3] - p[0], p[4] - p[1], p[5] - p[2]
            bx, by, bz = p[6] - p[0], p[7] - p[1], p[8] - p[2]
            n0x, n0y, n0z = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
            l0 = math.sqrt(n0x * n0x + n0y * n0y + n0z * n0z)
            if not (l0 > 1e-18):
                continue
            Q = []
            for k in range(3):
                v = tri[t, k]
                s = cr.slot_of[v]
                if s >= 0:
                    f = scale[s] if cr.released[s] else 1
                    d = (moves[s * 3] * f, moves[s * 3 + 1] * f, moves[s * 3 + 2] * f)
                else:
                    d = (snx[v] * acc[v], sny[v] * acc[v], snz[v] * acc[v])
                Q.append((p[k * 3] + d[0], p[k * 3 + 1] + d[1], p[k * 3 + 2] + d[2]))
            cx, cy, cz = Q[1][0] - Q[0][0], Q[1][1] - Q[0][1], Q[1][2] - Q[0][2]
            ex, ey, ez = Q[2][0] - Q[0][0], Q[2][1] - Q[0][1], Q[2][2] - Q[0][2]
            n1x, n1y, n1z = cy * ez - cz * ey, cz * ex - cx * ez, cx * ey - cy * ex
            l1 = math.sqrt(n1x * n1x + n1y * n1y + n1z * n1z)
            if n0x * n1x + n0y * n1y + n0z * n1z > FOLD_GUARD_MIN_COS * l0 * l1 and l1 > 1e-18:
                continue
            bad += 1
            for k in range(3):
                s = cr.slot_of[tri[t, k]]
                if s >= 0 and cr.released[s]:
                    scale[s] = scale[s] * 0.5 if rnd < FOLD_GUARD_ROUNDS else 0
        if bad == 0:
            break
    for s in range(cr.count):
        if not cr.released[s] or scale[s] == 1:
            continue
        moves[s * 3:s * 3 + 3] *= scale[s]


# What a displace can keep between calls: a Rotate ring drag reruns it on the
# same subdivided mesh with only the frame moved, and a slider reruns it on
# the same positions. Each is checked against the call's own input before it
# is used: the weld is reused only when it is this input's weld, the smoothed
# blend normals as they were for the same inputs, or turned by the frame
# (_smoothed) for the same mesh in another frame.
_KEEP = {}
FAST = True   # False: the first port's path (the tests' reference)


def _weld(P):
    """weld(P, QUANT), from the last call's grouping when it still holds: every
    copy of a position in its group shares its grid cell, and no two groups
    share one. The weld is that partition, numbered by first occurrence, so the
    same partition is the same weld."""
    if not FAST:
        return weld(P, QUANT)
    hit = _KEEP.get("weld")
    if hit is not None and hit[0] == len(P):
        _n, vid, unique, firsts = hit
        from .meshindex import quantize
        # Copies that are the same point share its cell; else check the cells.
        same = (P == P[firsts][vid]).all()
        rep = quantize(P[firsts], QUANT)
        if same or (quantize(P, QUANT) == rep[vid]).all():
            # Groups in distinct cells: their cells' hashes all differ (a
            # shared hash, even a mere collision, sends it to weld()).
            hk = rep.astype(np.uint64)
            hk = hk[:, 0] * np.uint64(0x9E3779B97F4A7C15) + hk[:, 1] * np.uint64(0xC2B2AE3D27D4EB4F) + hk[:, 2]
            hk.sort()
            if len(hk) < 2 or (hk[1:] != hk[:-1]).all():
                return vid, unique, firsts
    vid, unique, firsts = weld(P, QUANT)
    _KEEP["weld"] = (len(P), vid, unique, firsts)
    return vid, unique, firsts


def _smooth(g, node_count, ix, iy, iz, iters):
    """blendNormalSmoothing's iterations on the graph g = (src, dst, has, invd)."""
    src, dst, has, invd = g
    cx_, cy_, cz_ = ix.copy(), iy.copy(), iz.copy()
    for _ in range(iters):
        sx = np.bincount(src, cx_[dst], node_count) * invd
        sy = np.bincount(src, cy_[dst], node_count) * invd
        sz = np.bincount(src, cz_[dst], node_count) * invd
        l2 = np.sqrt(sx * sx + sy * sy + sz * sz)
        okl = has & (l2 > 1e-12)
        r = np.where(okl, 1 / np.where(okl, l2, 1), 0.0)
        cx_, cy_, cz_ = np.where(okl, sx * r, cx_), np.where(okl, sy * r, cy_), np.where(okl, sz * r, cz_)
    return cx_, cy_, cz_


SMOOTH_FRAME_TOL = 1e-4   # how far (unit normals) a frame's inputs may sit from the kept ones


def _smoothed(node, node_count, ix, iy, iz, iters, graph, R):
    """The smoothed blend normals. The smoothing turns with the frame (it
    averages and normalises), so a run on the same mesh in another frame
    (R, a Rotate drag) takes the kept result turned by R, when its inputs
    turned back match the kept ones to SMOOTH_FRAME_TOL; the same inputs
    give the kept result as it was; else it runs. R False (no frame given:
    the export) takes only the exact match."""
    I = np.stack([ix, iy, iz], 1)
    turn = R is not False
    Rm = np.eye(3) if R is None or R is False else np.asarray(R, np.float64)
    hit = _KEEP.get("smooth")
    if hit is not None and hit["iters"] == iters and hit["n"] == node_count and np.array_equal(hit["node"], node):
        if np.array_equal(hit["I"], I):
            out = hit["out"]
            return out[:, 0], out[:, 1], out[:, 2]
        base_in = I @ Rm   # each row turned back: R^T v
        if not turn or hit["base_in"] is None:
            base_in = None
        if base_in is not None and len(I) and float(np.abs(base_in - hit["base_in"]).max()) \
                <= SMOOTH_FRAME_TOL * max(1.0, float(np.abs(hit["base_in"]).max())):
            out = hit["base_out"] @ Rm.T
            return out[:, 0], out[:, 1], out[:, 2]
    out = np.stack(_smooth(graph(), node_count, ix, iy, iz, iters), 1)   # the graph: only when it runs
    _KEEP["smooth"] = {"iters": iters, "n": node_count, "node": node.copy(), "I": I, "out": out,
                       "base_in": I @ Rm if turn else None, "base_out": out @ Rm}
    return out[:, 0], out[:, 1], out[:, 2]


def apply_displacement(geom, image, settings, bounds, on_progress=None, collect=None, untangled=True):
    """applyDisplacement: one layer read off the geometry's excludeWeight / softExclude."""
    return apply_displacement_layers(geom, [{"image": image, "settings": settings, "legacy": True}], settings, bounds,
                                     on_progress, collect, untangled)


def apply_displacement_layers(geom, layers, settings, bounds, on_progress=None, collect=None, untangled=True):
    """applyDisplacementLayers. geom: {"position", "normal", "excludeWeight", "softExclude"}
    (float32 arrays); layers: [{"image", "settings", "exclude", "hardFaces", "blendAdd",
    "stampDepth", "legacy"}]. Returns {"position", "normal"} (float32). With collect (a
    dict), also each layer's weight per welded position (mask x falloff x angle
    mask x stamp coverage) in collect["weights"] and the weld in collect["vid"]."""
    pos32 = np.asarray(geom["position"], F32).reshape(-1, 3)
    P = pos32.astype(np.float64)
    count = len(P)
    nrm32 = geom.get("normal")
    N = np.asarray(nrm32, F32).reshape(-1, 3).astype(np.float64) if nrm32 is not None else None
    vid, unique, firsts = _weld(P)
    idpos = P[firsts]
    creases = _find_creases(N, vid, unique, firsts) if N is not None else None

    T = P.reshape(-1, 9)
    A, B, Cc = T[:, 0:3], T[:, 3:6], T[:, 6:9]
    e1, e2 = B - A, Cc - A
    fx = e1[:, 1] * e2[:, 2] - e1[:, 2] * e2[:, 1]
    fy = e1[:, 2] * e2[:, 0] - e1[:, 0] * e2[:, 2]
    fz = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
    area = np.sqrt(fx * fx + fy * fy + fz * fz)
    with np.errstate(all="ignore"):
        nzn = np.where(area > 1e-12, fz / np.where(area > 1e-12, area, 1), 0.0)
    ang = np.arccos(np.abs(nzn)) * (180 / math.pi)
    bal, tal = settings.get("bottomAngleLimit") or 0, settings.get("topAngleLimit") or 0
    amask = np.where(nzn < 0, (bal > 0) & (ang <= bal), (tal > 0) & (ang <= tal))
    ew = geom.get("excludeWeight")
    ew = None if ew is None else np.asarray(ew, F32).astype(np.float64)
    uexcl = ((ew[0::3] + ew[1::3] + ew[2::3]) / 3 > 0.99) if ew is not None else np.zeros(len(T), bool)
    ca = np.repeat(area, 3)
    cmask = np.repeat(amask, 3)
    cexcl = np.repeat(uexcl, 3)
    smx = np.bincount(vid, N[:, 0] * ca, unique)
    smy = np.bincount(vid, N[:, 1] * ca, unique)
    smz = np.bincount(vid, N[:, 2] * ca, unique)
    mf_masked = np.bincount(vid, np.where(cmask, ca, 0.0), unique)
    mf_total = np.bincount(vid, ca, unique)
    excluded_pos = None
    if ew is not None:
        excluded_pos = np.zeros(unique, bool)
        excluded_pos[vid[cexcl]] = True
    se_attr = geom.get("softExclude")
    for L in layers:
        if L.get("legacy"):
            L["_se"] = None if se_attr is None else np.asarray(se_attr, F32).astype(np.float64)
            L["_hardPos"] = None
        else:
            L["_se"] = None if L.get("exclude") is None else np.asarray(L["exclude"], F32).astype(np.float64)
            hf = L.get("hardFaces")
            L["_hardPos"] = None
            if hf is not None:
                hp = np.zeros(unique, bool)
                hp[vid[np.repeat(np.asarray(hf).astype(bool), 3)]] = True
                L["_hardPos"] = hp
        L["_softMax"] = None
        if L["_se"] is not None:
            sm = np.zeros(unique)
            np.maximum.at(sm, vid, L["_se"].astype(F32).astype(np.float64))
            L["_softMax"] = sm.astype(F32).astype(np.float64)
        if L["settings"].get("mappingMode") == MODE_CUBIC:
            ok = area > 1e-12
            ux, uy, uz = (np.where(ok, c / np.where(ok, area, 1), 0.0) for c in (fx, fy, fz))
            sx, sy, sz = _side_normal(L["settings"], ux, uy, uz)
            wx, wy, wz = cubic_blend_weights(sx, sy, sz, g(L["settings"], "mappingBlend", 0), g(L["settings"], "seamBandWidth", 0.35))
            czx, czy, czz = (np.where(ok, w * area, 0.0) for w in (wx, wy, wz))
            use = (czx > 1e-12) | (czy > 1e-12) | (czz > 1e-12)
            cu = np.repeat(use, 3)
            L["_zone"] = [np.bincount(vid, np.where(cu, np.repeat(c, 3), 0.0), unique) for c in (czx, czy, czz)]

    if creases is not None:
        at = creases.corners
        k = creases.start[creases.slot_of[vid[at]]] + creases.group_of[at]
        m = creases.group_count
        cas = ca[at]
        creases.nrmX = np.bincount(k, N[at, 0] * cas, m)
        creases.nrmY = np.bincount(k, N[at, 1] * cas, m)
        creases.nrmZ = np.bincount(k, N[at, 2] * cas, m)
        cen = (A + B + Cc) / 3
        cc = np.repeat(cen, 3, axis=0)[at]
        creases.inX = np.bincount(k, cc[:, 0] - P[at, 0], m)
        creases.inY = np.bincount(k, cc[:, 1] - P[at, 1], m)
        creases.inZ = np.bincount(k, cc[:, 2] - P[at, 2], m)
        creases.area = np.bincount(k, cas, m)
        creases.masked = np.bincount(k, np.where(cmask[at], cas, 0.0), m)
        creases.excluded[k[cexcl[at]]] = 1
        for L in layers:
            L["_hardGroup"] = None
            if L["_hardPos"] is not None:
                hg = np.zeros(m, np.uint8)
                hf = np.repeat(np.asarray(L["hardFaces"]).astype(bool), 3)[at]
                hg[k[hf]] = 1
                L["_hardGroup"] = hg
        if not _finish_creases(creases):
            creases = None
    if creases is None:
        for L in layers:
            L["_hardGroup"] = None

    ln = np.sqrt(smx * smx + smy * smy + smz * smz)
    with np.errstate(all="ignore"):
        rel = np.where((ln > 0) & (mf_total > 0), ln / np.where(mf_total > 0, mf_total, 1), 0.0)
    inv = np.where(ln > 0, 1 / np.where(ln > 0, ln, 1), 1.0)
    smx, smy, smz = smx * inv, smy * inv, smz * inv

    iters = max(0, int(math.floor(settings.get("blendNormalSmoothing") or 0)))
    if creases is not None:
        node_count = unique + creases.extra_nodes
        node = vid.copy()
        at = creases.corners
        sl = creases.slot_of[vid[at]]
        gi = creases.group_of[at]
        act = sl >= 0
        nd = np.where(gi == 0, vid[at], unique + creases.extra_start[np.maximum(sl, 0)] + gi - 1)
        node[at[act]] = nd[act]
        ix, iy, iz = (np.concatenate([a, np.zeros(creases.extra_nodes)]) for a in (smx, smy, smz))
        for s in np.nonzero(creases.active)[0].tolist():
            for gg in range(int(creases.groups[s])):
                nn = _group_node(creases, unique, s, gg)
                kk = creases.start[s] + gg
                ix[nn], iy[nn], iz[nn] = creases.nrmX[kk], creases.nrmY[kk], creases.nrmZ[kk]
    else:
        node_count = unique
        node = vid
        ix, iy, iz = smx, smy, smz
    bx, by, bz = ix, iy, iz
    if iters > 0:
        def graph():
            tn = node.reshape(-1, 3)
            a, b, c = tn[:, 0], tn[:, 1], tn[:, 2]
            # Per triangle, in the JS's order: a-b, b-c, c-a, each both ways.
            pr = []
            for (p, q) in ((a, b), (b, c), (c, a)):
                ok = p != q
                pr.append((np.where(ok, p, -1), np.where(ok, q, -1)))
            S = np.stack([pr[0][0], pr[0][1], pr[1][0], pr[1][1], pr[2][0], pr[2][1]], 1).reshape(-1)
            D = np.stack([pr[0][1], pr[0][0], pr[1][1], pr[1][0], pr[2][1], pr[2][0]], 1).reshape(-1)
            keep = S >= 0
            src, dst = S[keep], D[keep]
            deg = np.bincount(src, minlength=node_count).astype(np.float64)
            has = deg > 0
            invd = np.where(has, 1 / np.where(has, deg, 1), 0.0)
            return src, dst, has, invd

        if not FAST:   # the reference: smoothed every run
            cx_, cy_, cz_ = _smooth(graph(), node_count, ix, iy, iz, iters)
        else:
            cx_, cy_, cz_ = _smoothed(node, node_count, ix, iy, iz, iters, graph,
                                      geom["frame"] if "frame" in geom else False)
        bx, by, bz = cx_, cy_, cz_

    acc = np.zeros(unique)
    gacc = np.zeros(creases.group_count) if creases is not None else None
    fpos = P[firsts]
    md = max(bounds["size"][0], bounds["size"][1], bounds["size"][2], 1e-6)
    crease_slot = creases.slot_of if creases is not None else np.full(unique, -1)
    plain = crease_slot < 0
    if creases is not None:
        gk, gvid, gnode = [], [], []
        for s in np.nonzero(creases.active)[0].tolist():
            for gg in range(int(creases.groups[s])):
                gk.append(int(creases.start[s]) + gg)
                gvid.append(int(creases.vid_of[s]))
                gnode.append(int(_group_node(creases, unique, s, gg)))
        gk, gvid, gnode = np.array(gk, np.int64), np.array(gvid, np.int64), np.array(gnode, np.int64)

    for li, L in enumerate(layers):
        ls = L["settings"]
        img = L["image"]
        w, h = img.width, img.height
        R = _red(img)
        tmax = max(w, h, 1)
        aU, aV = tmax / max(w, 1), tmax / max(h, 1)
        swa = {**ls, "textureAspectU": aU, "textureAspectV": aV}
        hard = L["_hardPos"]
        pinned = np.zeros(unique, bool)
        if excluded_pos is not None:
            pinned |= excluded_pos
        if hard is not None:
            pinned |= hard
        fo = falloff_field(ls, unique, mf_masked, mf_total, pinned, idpos)
        val = np.zeros(unique)
        cov = None
        gval = np.zeros(creases.group_count) if creases is not None else None
        gcov = None
        stamp = stamp_frame(swa, bounds) if is_stamp_active(ls) else None
        if stamp is not None and L.get("stampDepth") is not None:
            stamp["depth"] = L["stampDepth"]
        rel_s = scale_mm_to_relative(6, ls, bounds)
        rot = g(ls, "rotation", 0) * math.pi / 180
        cb, cbw = g(ls, "mappingBlend", 0), g(ls, "seamBandWidth", 0.35)
        mode = ls.get("mappingMode")
        if stamp is not None:
            cov = np.zeros(unique)
            gcov = np.zeros(creases.group_count) if creases is not None else None
        # Crease groups.
        if creases is not None and len(gk):
            pp = fpos[gvid]
            if stamp is not None:
                u, v, cv = stamp_uv(pp, np.stack([ix[gnode], iy[gnode], iz[gnode]], 1), stamp)
                gval[gk] = _stamp_grey(R, w, h, u, v)
                gcov[gk] = cv
            elif mode == MODE_CUBIC:
                sx, sy, sz = _side_normal(ls, bx[gnode], by[gnode], bz[gnode])
                wx, wy, wz = cubic_blend_weights(sx, sy, sz, cb, cbw)
                gval[gk] = _cubic_grey(R, w, h, pp, wx, wy, wz, creases.nrmX[gk], creases.nrmY[gk], creases.nrmZ[gk], bounds, md, rel_s, ls, rot, aU, aV)
            else:
                gval[gk] = _projected_grey(R, w, h, pp, np.stack([bx[gnode], by[gnode], bz[gnode]], 1), mode, swa, bounds)
        ids = np.nonzero(plain)[0]
        pp = fpos[ids]
        if stamp is not None:
            u, v, cv = stamp_uv(pp, np.stack([smx[ids], smy[ids], smz[ids]], 1), stamp)
            val[ids] = _stamp_grey(R, w, h, u, v)
            cov[ids] = cv
        else:
            rest = np.ones(len(ids), bool)
            if mode == MODE_CUBIC:
                zx, zy, zz = L["_zone"]
                good = rel[ids] > 0.5
                sx, sy, sz = _side_normal(ls, bx[ids], by[ids], bz[ids])
                wx, wy, wz = cubic_blend_weights(sx, sy, sz, cb, cbw)
                tot = zx[ids] + zy[ids] + zz[ids]
                with np.errstate(all="ignore"):
                    fx_, fy_, fz_ = (np.where(tot > 0, z_[ids] / np.where(tot > 0, tot, 1), 0.0) for z_ in (zx, zy, zz))
                wx, wy, wz = np.where(good, wx, fx_), np.where(good, wy, fy_), np.where(good, wz, fz_)
                some = wx + wy + wz > 0
                if some.any():
                    sel = ids[some]
                    val[sel] = _cubic_grey(R, w, h, fpos[sel], wx[some], wy[some], wz[some], smx[sel], smy[sel], smz[sel], bounds, md, rel_s, ls, rot, aU, aV)
                rest = ~some
            if rest.any():
                sel = ids[rest]
                val[sel] = _projected_grey(R, w, h, fpos[sel], np.stack([bx[sel], by[sel], bz[sel]], 1), mode, swa, bounds)
        softmax = L["_softMax"]
        sym = bool(ls.get("symmetricDisplacement"))
        amp = ls.get("amplitude")
        add = bool(L.get("blendAdd"))
        with np.errstate(all="ignore"):
            mfr = np.where(mf_total > 0, mf_masked / np.where(mf_total > 0, mf_total, 1), 0.0)
        cg = val - 0.5 if sym else val
        ff = fo if fo is not None else 1.0
        disp = np.where(pinned, 0.0, ff * (1 - mfr) * cg * amp)
        if softmax is not None:
            disp = disp * (1 - softmax)
        if cov is not None:
            disp = disp * cov
        if li == 0:
            acc = disp
        elif add:
            acc = acc + disp
        else:
            cover = np.where(pinned, 0.0, (1 - softmax) if softmax is not None else 1.0)
            if cov is not None:
                cover = cover * cov
            acc = acc * (1 - cover) + disp
        if collect is not None:
            wl = np.where(pinned, 0.0, ff * (1 - mfr)) * np.ones(unique)
            if softmax is not None:
                wl = wl * (1 - softmax)
            if cov is not None:
                wl = wl * cov
            collect.setdefault("weights", []).append(wl)
            collect["vid"] = vid
        if creases is not None and len(gk):
            hg = L["_hardGroup"]
            per_group = not (g(ls, "boundaryFalloff", 0) > 0)
            pp_ = pinned[gvid]
            grp_pin = pp_ & ((not per_group) | (creases.excluded[gk] == 1) | ((hg[gk] == 1) if hg is not None else False))
            rel_slot = creases.slot_of[gvid]
            creases.released[rel_slot[pp_ & ~grp_pin]] = 1
            garea = creases.area[gk]
            with np.errstate(all="ignore"):
                gmf = np.where(garea > 0, creases.masked[gk] / np.where(garea > 0, garea, 1), 0.0)
            gcg = gval[gk] - 0.5 if sym else gval[gk]
            gff = fo[gvid] if fo is not None else 1.0
            gd = np.where(grp_pin, 0.0, gff * (1 - gmf) * gcg * amp)
            if softmax is not None:
                gd = gd * (1 - softmax[gvid])
            if gcov is not None:
                gd = gd * gcov[gk]
            if li == 0:
                gacc[gk] = gd
            elif add:
                gacc[gk] = gacc[gk] + gd
            else:
                gc = np.where(grp_pin, 0.0, (1 - softmax[gvid]) if softmax is not None else 1.0)
                if gcov is not None:
                    gc = gc * gcov[gk]
                gacc[gk] = gacc[gk] * (1 - gc) + gd
        if on_progress:
            on_progress(0.5 * (li + 1) / len(layers))

    moves = None
    if creases is not None:
        moves = _crease_moves(creases, gacc, smx, smy, smz)
        _guard_released_folds(creases, moves, P.reshape(-1), vid, acc, smx, smy, smz)
    slot = crease_slot[vid]
    d = acc[vid]
    dxyz = np.stack([smx[vid] * d, smy[vid] * d, smz[vid] * d], 1)
    if moves is not None:
        cs = slot >= 0
        dxyz[cs] = moves.reshape(-1, 3)[slot[cs]]
    new = P + dxyz
    nz = new[:, 2]
    mfr_c = np.where(mf_total[vid] > 0, mf_masked[vid] / np.where(mf_total[vid] > 0, mf_total[vid], 1), 0.0)
    oz = P[:, 2]
    part = mfr_c > 0
    if bal > 0:
        nz = np.where(part & (nz < oz), oz, nz)
    if tal > 0:
        nz = np.where(part & (nz > oz), oz, nz)
    if settings.get("noDownwardZ"):
        nz = np.where(nz < oz, oz, nz)
        nz = np.where(oz <= bounds["min"][2] + 1e-5, oz, nz)
    new[:, 2] = nz
    # Concave regions (a hole, a pocket's inner corner): walls that the
    # displacement makes cross are pulled back (untangle.py; not in BumpMesh).
    from .untangle import untangle

    Vw = P[firsts]
    k = np.ones(len(Vw)) if not untangled else untangle(Vw, new[firsts] - Vw, vid.reshape(-1, 3), np.stack([smx, smy, smz], 1), also=crease_slot >= 0)
    if (k < 1).any():
        new = P + (new - P) * k[vid][:, None]
        if collect is not None:
            collect["untangled"] = int((k < 1).sum())
    new32 = new.astype(F32)
    Q = new32.astype(np.float64).reshape(-1, 9)
    ea, eb = Q[:, 3:6] - Q[:, 0:3], Q[:, 6:9] - Q[:, 0:3]
    nx = ea[:, 1] * eb[:, 2] - ea[:, 2] * eb[:, 1]
    ny = ea[:, 2] * eb[:, 0] - ea[:, 0] * eb[:, 2]
    nz = ea[:, 0] * eb[:, 1] - ea[:, 1] * eb[:, 0]
    ln = np.sqrt(nx * nx + ny * ny + nz * nz)
    ln = np.where(ln == 0, 1.0, ln)
    s = 1 / ln
    fn = np.stack([nx * s, ny * s, nz * s], 1).astype(F32)
    if on_progress:
        on_progress(1.0)
    return {"position": new32.reshape(-1), "normal": np.repeat(fn, 3, axis=0).reshape(-1)}
