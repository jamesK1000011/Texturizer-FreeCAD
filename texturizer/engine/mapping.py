# SPDX-License-Identifier: AGPL-3.0-only
# Port of js/mapping.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# UV projection per mapping mode (0-8), vectorised: computeUV answers, for
# every point at once, the weighted samples the JS returns one point at a
# time (a single UV is one sample of weight 1, which sums to the same grey).
# Also the stamp ("place once") frame, coverage, depth map and the faces a
# stamp cannot reach.
import math

import numpy as np

from ._js import F32, fract, hypot, hypot1, jround
from .meshindex import weld

MODE_PLANAR_XY = 0
MODE_PLANAR_XZ = 1
MODE_PLANAR_YZ = 2
MODE_CYLINDRICAL = 3
MODE_SPHERICAL = 4
MODE_TRIPLANAR = 5
MODE_CUBIC = 6
MODE_SURFACE_NORMAL = 7
MODE_OUTLINE_WRAP = 8

TWO_PI = math.pi * 2
CUBIC_AXIS_EPSILON = 1e-4


def g(s, k, d=None):
    """settings[k] ?? d."""
    v = s.get(k)
    return d if v is None else v


def _num_or(v, d):
    """Number(v) || d."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return d
    return d if (f == 0 or math.isnan(f)) else f


def scale_reference_lengths(mode, settings, bounds):
    size = bounds["size"]
    md = max(size[0], size[1], size[2], 1e-6)
    if mode == MODE_OUTLINE_WRAP:
        w = settings.get("outlineWrap")
        if w and w.get("meanLength", 0) > 0:
            L = w["meanLength"]
            return L, L
    if mode in (MODE_OUTLINE_WRAP, MODE_CYLINDRICAL):
        r = max(g(settings, "cylinderRadius", max(size[0], size[1]) * 0.5), 1e-6)
        C = TWO_PI * r
        return C, C
    if mode == MODE_SPHERICAL:
        R = max(0.5 * md, 1e-6)
        return TWO_PI * R, math.pi * R
    return md, md


def outline_wrap_repeats(scale_u):
    return max(1, int(math.floor(1 / scale_u + 0.5)))


def scale_mm_to_relative(mode, settings, bounds):
    ru, rv = scale_reference_lengths(mode, settings, bounds)
    u = max(_num_or(settings.get("scaleU"), 1e-6), 1e-6) / ru
    v = max(_num_or(settings.get("scaleV"), 1e-6), 1e-6) / rv
    return u, v


def is_rotate_around_z(settings):
    rot = g(settings, "rotation", 0)
    return bool(settings.get("rotateAroundZ")) and math.fmod(rot, 360) != 0


def rotate_side_frame(px, py, nx, ny, bounds, cosR, sinR):
    rx = px - bounds["center"][0]
    ry = py - bounds["center"][1]
    return (cosR * rx - sinR * ry + 0.5 * bounds["size"][0],
            sinR * rx + cosR * ry + 0.5 * bounds["size"][1],
            cosR * nx - sinR * ny,
            sinR * nx + cosR * ny)


def dominant_axis(nx, ny, nz):
    """0 (x), 1 (y), 2 (z) per row, near-ties to the first."""
    ax, ay, az = np.abs(nx), np.abs(ny), np.abs(nz)
    e = CUBIC_AXIS_EPSILON
    isx = (ax >= ay - e) & (ax >= az - e)
    isy = ~isx & (ay >= az - e)
    return np.where(isx, 0, np.where(isy, 1, 2))


def cubic_blend_weights(nx, ny, nz, blend, seam_band_width=0.35):
    """getCubicBlendWeights for arrays of normals: (wx, wy, wz)."""
    nx, ny, nz = (np.asarray(a, np.float64) for a in (nx, ny, nz))
    axis = dominant_axis(nx, ny, nz)
    ax, ay, az = np.abs(nx), np.abs(ny), np.abs(nz)
    ohx, ohy, ohz = (axis == 0).astype(float), (axis == 1).astype(float), (axis == 2).astype(float)
    if blend <= 0.001:
        return ohx, ohy, ohz
    primary = np.where(axis == 0, ax, np.where(axis == 1, ay, az))
    secondary = np.where(axis == 0, np.maximum(ay, az), np.where(axis == 1, np.maximum(ax, az), np.maximum(ax, ay)))
    seam_w = max(seam_band_width, CUBIC_AXIS_EPSILON * 2)
    raw = 1 - np.minimum(1, np.maximum(0, (primary - secondary) / seam_w))
    mix = blend * raw * raw * (3 - 2 * raw)
    power = 1 + (1 - mix) * 11
    with np.errstate(all="ignore"):
        sx, sy, sz = np.power(ax, power), np.power(ay, power), np.power(az, power)
        ssum = sx + sy + sz + 1e-6
        mx = ohx * (1 - mix) + (sx / ssum) * mix
        my = ohy * (1 - mix) + (sy / ssum) * mix
        mz = ohz * (1 - mix) + (sz / ssum) * mix
        tot = mx + my + mz
        wx, wy, wz = mx / tot, my / tot, mz / tot
    one = mix <= 0.001
    return np.where(one, ohx, wx), np.where(one, ohy, wy), np.where(one, ohz, wz)


def apply_transform(u, v, su, sv, ou, ov, cosR, sinR):
    uu = u / su + ou
    vv = v / sv + ov
    if cosR != 1 or sinR != 0:
        uu = uu - 0.5
        vv = vv - 0.5
        ru = cosR * uu - sinR * vv
        rv = sinR * uu + cosR * vv
        uu = ru + 0.5
        vv = rv + 0.5
    return fract(uu), fract(vv)


def _smooth(t):
    return t * t * (3 - 2 * t)


def compute_uv(pos, nrm, mode, settings, bounds):
    """computeUV for points (n, 3) and blend normals (n, 3): a list of
    samples (u, v, w), summed in order as grey += sample * w."""
    pos = np.asarray(pos, np.float64).reshape(-1, 3)
    nrm = np.asarray(nrm, np.float64).reshape(-1, 3)
    n = len(pos)
    mn, size, center = bounds["min"], bounds["size"], bounds["center"]
    aU = g(settings, "textureAspectU", 1)
    aV = g(settings, "textureAspectV", 1)
    ru, rv = scale_mm_to_relative(mode, settings, bounds)
    su, sv = ru / aU, rv / aV
    ou, ov = settings.get("offsetU", 0) or 0, settings.get("offsetV", 0) or 0
    rot = g(settings, "rotation", 0) * math.pi / 180
    cosR, sinR = math.cos(rot), math.sin(rot)
    md = max(size[0], size[1], size[2], 1e-6)
    x, y, z = pos[:, 0], pos[:, 1], pos[:, 2]
    T = lambda a, b, c=cosR, s=sinR: apply_transform(a, b, su, sv, ou, ov, c, s)  # noqa: E731
    one = np.ones(n)
    zero = np.zeros(n)

    if mode == MODE_PLANAR_XY:
        return [(*T((x - mn[0]) / md, (y - mn[1]) / md), one)]
    if mode == MODE_PLANAR_XZ:
        return [(*T((x - mn[0]) / md, (z - mn[2]) / md), one)]
    if mode == MODE_PLANAR_YZ:
        return [(*T((y - mn[1]) / md, (z - mn[2]) / md), one)]

    if mode == MODE_CYLINDRICAL:
        cx = g(settings, "cylinderCenterX", center[0])
        cy = g(settings, "cylinderCenterY", center[1])
        r = max(g(settings, "cylinderRadius", max(size[0], size[1]) * 0.5), 1e-6)
        C = TWO_PI * r
        rx, ry = x - cx, y - cy
        blend = g(settings, "mappingBlend", 0.0)
        theta = np.arctan2(ry, rx)
        u_raw = (theta / TWO_PI) + 0.5
        v_side = (z - mn[2]) / C
        band = g(settings, "seamBandWidth", 0.5) * 0.1
        seam = (band > 0.001) & (np.minimum(u_raw, 1.0 - u_raw) < band)
        d = np.where(u_raw < 0.5, u_raw, u_raw - 1.0)
        traw = (d + band) / (2.0 * band) if band > 0 else zero
        t = _smooth(traw)
        lu, lv = T(1.0 + d, v_side)
        rtu, rtv = T(d, v_side)
        tu, tv = T(u_raw, v_side)
        s0u, s0v = np.where(seam, rtu, tu), np.where(seam, rtv, tv)
        w0 = np.where(seam, t, 1.0)
        w1 = np.where(seam, 1 - t, 0.0)
        if blend <= 0.001:
            return [(s0u, s0v, w0), (lu, lv, w1)]
        cap_th = math.cos(g(settings, "capAngle", 20) * math.pi / 180)
        bh = g(settings, "seamBandWidth", 0.5) * 0.5
        capraw = np.maximum(0, np.minimum(1, (np.abs(nrm[:, 2]) - (cap_th - bh)) / (2 * bh + 1e-6)))
        capw = _smooth(capraw)
        cu, cv = T(rx / C + 0.5, ry / C + 0.5)
        full = capw >= 1
        none = capw <= 0
        k = np.where(none, 1.0, 1 - capw)
        return [(s0u, s0v, np.where(full, 0.0, w0 * k)), (lu, lv, np.where(full, 0.0, w1 * k)),
                (cu, cv, np.where(none, 0.0, np.where(full, 1.0, capw)))]

    if mode == MODE_SPHERICAL:
        rx, ry, rz = x - center[0], y - center[1], z - center[2]
        r = np.sqrt(rx * rx + ry * ry + rz * rz)
        phi = np.arccos(np.maximum(-1, np.minimum(1, rz / np.maximum(r, 1e-6))))
        theta = np.arctan2(ry, rx)
        u_raw = (theta / TWO_PI) + 0.5
        v_raw = phi / math.pi
        band = g(settings, "seamBandWidth", 0.5) * 0.1
        seam = (band > 0.001) & (np.minimum(u_raw, 1.0 - u_raw) < band)
        d = np.where(u_raw < 0.5, u_raw, u_raw - 1.0)
        t = _smooth((d + band) / (2.0 * band)) if band > 0 else zero
        lu, lv = T(1.0 + d, v_raw)
        rtu, rtv = T(d, v_raw)
        tu, tv = T(u_raw, v_raw)
        return [(np.where(seam, rtu, tu), np.where(seam, rtv, tv), np.where(seam, t, 1.0)),
                (lu, lv, np.where(seam, 1 - t, 0.0))]

    if mode == MODE_CUBIC:
        around = is_rotate_around_z(settings)
        nx, ny, nz = nrm[:, 0], nrm[:, 1], nrm[:, 2]
        if around:
            sfx, sfy, snx, sny = rotate_side_frame(x, y, nx, ny, bounds, cosR, sinR)
        else:
            sfx, sfy, snx, sny = x - mn[0], y - mn[1], nx, ny
        sc, ss = (1.0, 0.0) if around else (cosR, sinR)
        wx, wy, wz = cubic_blend_weights(snx, sny, nz, g(settings, "mappingBlend", 0.0), g(settings, "seamBandWidth", 0.35))
        yzU = sfy / md
        yzU = np.where(snx < 0, -yzU, yzU)
        xzU = sfx / md
        xzU = np.where(sny > 0, -xzU, xzU)
        xyU = (x - mn[0]) / md
        xyU = np.where(nz < 0, -xyU, xyU)
        a = T(yzU, (z - mn[2]) / md, sc, ss)
        b = T(xzU, (z - mn[2]) / md, sc, ss)
        c = T(xyU, (y - mn[1]) / md)
        ox, oy, oz = wx > 0.999, ~(wx > 0.999) & (wy > 0.999), ~(wx > 0.999) & ~(wy > 0.999) & (wz > 0.999)
        single = ox | oy | oz
        return [(c[0], c[1], np.where(single, np.where(oz, 1.0, 0.0), wz)),
                (b[0], b[1], np.where(single, np.where(oy, 1.0, 0.0), wy)),
                (a[0], a[1], np.where(single, np.where(ox, 1.0, 0.0), wx))]

    if mode == MODE_OUTLINE_WRAP:
        wrap = settings.get("outlineWrap")
        if not (wrap and wrap.get("meanLength", 0) > 0):
            return compute_uv(pos, nrm, MODE_CYLINDRICAL, settings, bounds)
        from .outlinewrap import cap_weight, places

        nrep = outline_wrap_repeats(su)
        L = nrep * su
        share, rise = places(wrap, x, y, z)
        sdu, sdv = T(share * L, rise * L)
        nl = hypot(nrm[:, 0], nrm[:, 1], nrm[:, 2])
        with np.errstate(all="ignore"):
            capw = np.where(nl > 0, cap_weight(nrm[:, 2] / np.where(nl > 0, nl, 1)), 0.0)
        ml = wrap["meanLength"]
        cu, cv = T((x - mn[0]) / ml, (y - mn[1]) / ml)
        none, full = capw <= 0, capw >= 1
        return [(sdu, sdv, np.where(none, 1.0, np.where(full, 0.0, 1 - capw))),
                (cu, cv, np.where(none, 0.0, np.where(full, 1.0, capw)))]

    # Triplanar (and any other code).
    around = is_rotate_around_z(settings)
    nx, ny, nz = nrm[:, 0], nrm[:, 1], nrm[:, 2]
    if around:
        sfx, sfy, snx, sny = rotate_side_frame(x, y, nx, ny, bounds, cosR, sinR)
    else:
        sfx, sfy, snx, sny = x - mn[0], y - mn[1], nx, ny
    sc, ss = (1.0, 0.0) if around else (cosR, sinR)
    ax, ay, az = np.abs(snx), np.abs(sny), np.abs(nz)
    ax2, ay2, az2 = ax * ax, ay * ay, az * az
    bx, by, bz = ax2 * ax2, ay2 * ay2, az2 * az2
    tot = bx + by + bz + 1e-6
    yzU = sfy / md
    yzU = np.where(snx < 0, -yzU, yzU)
    xzU = sfx / md
    xzU = np.where(sny > 0, -xzU, xzU)
    xyU = (x - mn[0]) / md
    xyU = np.where(nz < 0, -xyU, xyU)
    return [(*T(xyU, (y - mn[1]) / md), bz / tot),
            (*T(xzU, (z - mn[2]) / md, sc, ss), by / tot),
            (*T(yzU, (z - mn[2]) / md, sc, ss), bx / tot)]


# ── Stamp ("place once") ─────────────────────────────────────────────────────
STAMP_FACING_LO = 0.15
STAMP_FACING_HI = 0.30
STAMP_FLAT = 0.17
STAMP_DEPTH_RES = 512
STAMP_DEPTH_TOL = 0.05
STAMP_DEPTH_EMPTY = -1e30
HALF_DIAG = math.sqrt(0.5) + 1e-6


def is_stamp_active(s):
    mode = s.get("mappingMode")
    return bool(s.get("stampOnce")) and ((MODE_PLANAR_XY <= mode <= MODE_CYLINDRICAL) or mode == MODE_SURFACE_NORMAL)


def stamp_axes(nx, ny, nz):
    if hypot1(nx, ny) < STAMP_FLAT:
        vx, vy, vz = -ny * nx, 1 - ny * ny, -ny * nz
    else:
        vx, vy, vz = -nz * nx, -nz * ny, 1 - nz * nz
    ln = hypot1(vx, vy, vz)
    vx, vy, vz = vx / ln, vy / ln, vz / ln
    return {"ux": vy * nz - vz * ny, "uy": vz * nx - vx * nz, "uz": vx * ny - vy * nx, "vx": vx, "vy": vy, "vz": vz}


def default_stamp_side(mode):
    return -1 if mode == MODE_PLANAR_XZ else 1


def stamp_frame(s, bounds):
    mode = s.get("mappingMode")
    c, size = bounds["center"], bounds["size"]
    ccx = g(s, "cylinderCenterX", c[0])
    ccy = g(s, "cylinderCenterY", c[1])
    r = max(g(s, "cylinderRadius", max(size[0], size[1]) * 0.5), 1e-6)
    cx, cy, cz = s.get("stampCenterX"), s.get("stampCenterY"), s.get("stampCenterZ")
    if cx is None or cy is None or cz is None:
        cx, cy, cz = c
        if mode == MODE_CYLINDRICAL:
            cx, cy = ccx, ccy - r
    side = s.get("stampSide") if s.get("stampSide") in (1, -1) else default_stamp_side(mode)
    nx, ny, nz = 0.0, -1.0, 0.0
    if mode == MODE_SURFACE_NORMAL:
        side = 1
        a, b, cc = g(s, "stampNormalX", 0), g(s, "stampNormalY", 0), g(s, "stampNormalZ", 0)
        ln = hypot1(a, b, cc)
        if ln > 1e-9:
            nx, ny, nz = a / ln, b / ln, cc / ln
    rot = g(s, "rotation", 0) * math.pi / 180
    f = {"mode": mode, "cx": cx, "cy": cy, "cz": cz, "side": side, "ccx": ccx, "ccy": ccy, "r": r,
         "nx": nx, "ny": ny, "nz": nz, **stamp_axes(nx, ny, nz),
         "sizeU": max(_num_or(s.get("scaleU"), 1e-6), 1e-6) / g(s, "textureAspectU", 1),
         "sizeV": max(_num_or(s.get("scaleV"), 1e-6), 1e-6) / g(s, "textureAspectV", 1),
         "cosR": math.cos(rot), "sinR": math.sin(rot),
         "fade": max(_num_or(s.get("boundaryFalloff"), 0), 0),
         "fadeCurve": g(s, "boundaryFalloffCurve", "linear"), "depth": None}
    return f


def falloff_curve(t, mode):
    if mode == "scurve":
        return t * t * (3 - 2 * t)
    if mode == "ease":
        return t * t
    return t


def _stamp_ab(x, y, z, f):
    mode, side = f["mode"], f["side"]
    dth = None
    if mode == MODE_SURFACE_NORMAL:
        dx, dy, dz = x - f["cx"], y - f["cy"], z - f["cz"]
        a = dx * f["ux"] + dy * f["uy"] + dz * f["uz"]
        b = dx * f["vx"] + dy * f["vy"] + dz * f["vz"]
    elif mode == MODE_PLANAR_XY:
        a, b = x - f["cx"], y - f["cy"]
        if side < 0:
            a = -a
    elif mode == MODE_PLANAR_XZ:
        a, b = x - f["cx"], z - f["cz"]
        if side > 0:
            a = -a
    elif mode == MODE_PLANAR_YZ:
        a, b = y - f["cy"], z - f["cz"]
        if side < 0:
            a = -a
    else:
        rpx, rpy = x - f["ccx"], y - f["ccy"]
        rcx, rcy = f["cx"] - f["ccx"], f["cy"] - f["ccy"]
        dth = np.arctan2(rcx * rpy - rcy * rpx, rcx * rpx + rcy * rpy)
        a, b = dth * f["r"], z - f["cz"]
        if side < 0:
            a = -a
    return a, b, dth


def _stamp_facing(nx, ny, nz, x, y, z, f):
    mode, side = f["mode"], f["side"]
    nl = hypot(nx, ny, nz)
    nl = np.where(nl == 0, 1.0, nl)
    if mode == MODE_SURFACE_NORMAL:
        fa = nx * f["nx"] + ny * f["ny"] + nz * f["nz"]
    elif mode == MODE_PLANAR_XY:
        fa = nz * side
    elif mode == MODE_PLANAR_XZ:
        fa = ny * side
    elif mode == MODE_PLANAR_YZ:
        fa = nx * side
    else:
        rpx, rpy = x - f["ccx"], y - f["ccy"]
        lp = hypot(rpx, rpy)
        with np.errstate(all="ignore"):
            fa = np.where(lp > 1e-6, (nx * rpx + ny * rpy) / np.where(lp > 1e-6, lp, 1) * side, 0.0)
    return fa / nl


def _stamp_height(x, y, z, f):
    mode, side = f["mode"], f["side"]
    if mode == MODE_SURFACE_NORMAL:
        return x * f["nx"] + y * f["ny"] + z * f["nz"]
    if mode == MODE_PLANAR_XY:
        return z * side
    if mode == MODE_PLANAR_XZ:
        return y * side
    if mode == MODE_PLANAR_YZ:
        return x * side
    return hypot(x - f["ccx"], y - f["ccy"]) * side


def stamp_uv(pos, nrm, f):
    """stampUV for points (n, 3): (u, v, cover)."""
    pos = np.asarray(pos, np.float64).reshape(-1, 3)
    nrm = np.asarray(nrm, np.float64).reshape(-1, 3)
    x, y, z = pos[:, 0], pos[:, 1], pos[:, 2]
    a, b, _ = _stamp_ab(x, y, z, f)
    facing = _stamp_facing(nrm[:, 0], nrm[:, 1], nrm[:, 2], x, y, z, f)
    ra = f["cosR"] * a - f["sinR"] * b
    rb = f["sinR"] * a + f["cosR"] * b
    u = ra / f["sizeU"] + 0.5
    v = rb / f["sizeV"] + 0.5
    inside = (u >= 0) & (u <= 1) & (v >= 0) & (v <= 1)
    t = np.maximum(0, np.minimum(1, (facing - STAMP_FACING_LO) / (STAMP_FACING_HI - STAMP_FACING_LO)))
    cover = t * t * (3 - 2 * t)
    if f["fade"] > 0:
        edge = np.minimum(np.minimum(u, 1 - u) * f["sizeU"], np.minimum(v, 1 - v) * f["sizeV"])
        cover = cover * falloff_curve(np.minimum(1, edge / f["fade"]), f["fadeCurve"])
    d = f.get("depth")
    if d is not None:
        occ = _stamp_occluded(u, v, _stamp_height(x, y, z, f), facing, d)
        cover = np.where((cover > 0) & occ, 0.0, cover)
    cover = np.where(inside, cover, 0.0)
    return u, v, cover


def _stamp_depth_tol_max(d):
    fl = STAMP_FACING_LO
    return STAMP_DEPTH_TOL + d["texel"] * math.sqrt(1 - fl * fl) / fl


def _stamp_occluded(u, v, h, facing, d):
    with np.errstate(all="ignore"):
        i = np.minimum(d["nu"] - 1, np.maximum(0, np.floor(u * d["nu"]))).astype(np.int64)
        j = np.minimum(d["nv"] - 1, np.maximum(0, np.floor(v * d["nv"]))).astype(np.int64)
    fa = np.maximum(facing, STAMP_FACING_LO)
    tol = STAMP_DEPTH_TOL + d["texel"] * np.sqrt(np.maximum(0, 1 - fa * fa)) / fa
    return h < d["data"][j * d["nu"] + i].astype(np.float64) - tol


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = np.maximum(0, np.minimum(1, ((px - ax) * dx + (py - ay) * dy) / l2)) if l2 > 0 else 0
    return hypot(px - ax - t * dx, py - ay - t * dy)


def _tri_texels(tx, ty, nu, nv, conservative=False):
    """(i, j) arrays of the depth texels of one triangle (texel-centre coordinates)."""
    pad = HALF_DIAG if conservative else 0
    i0 = max(0, math.ceil(min(tx) - pad))
    i1 = min(nu - 1, math.floor(max(tx) + pad))
    j0 = max(0, math.ceil(min(ty) - pad))
    j1 = min(nv - 1, math.floor(max(ty) + pad))
    if i0 > i1 or j0 > j1:
        return None
    jj, ii = np.mgrid[j0:j1 + 1, i0:i1 + 1]
    ii = ii.reshape(-1).astype(np.float64)
    jj = jj.reshape(-1).astype(np.float64)
    area = (tx[1] - tx[0]) * (ty[2] - ty[0]) - (tx[2] - tx[0]) * (ty[1] - ty[0])
    sgn = 1 if area >= 0 else -1
    eps = -1e-9 * abs(area)
    inside = np.full(len(ii), area != 0)
    for k in range(3):
        k1 = 0 if k == 2 else k + 1
        inside &= ~(sgn * ((tx[k1] - tx[k]) * (jj - ty[k]) - (ty[k1] - ty[k]) * (ii - tx[k])) < eps)
    if conservative:
        near = ((_seg_dist(ii, jj, tx[0], ty[0], tx[1], ty[1]) <= HALF_DIAG)
                | (_seg_dist(ii, jj, tx[1], ty[1], tx[2], ty[2]) <= HALF_DIAG)
                | (_seg_dist(ii, jj, tx[2], ty[2], tx[0], ty[0]) <= HALF_DIAG))
        inside |= near
    return ii[inside].astype(np.int64), jj[inside].astype(np.int64)


def _tri_contains_axis(P, cx, cy):
    """For (t, 9) triangles: the XY projection contains (cx, cy)."""
    def d(x1, y1, x2, y2):
        return (x2 - x1) * (cy - y1) - (y2 - y1) * (cx - x1)
    d1 = d(P[:, 0], P[:, 1], P[:, 3], P[:, 4])
    d2 = d(P[:, 3], P[:, 4], P[:, 6], P[:, 7])
    d3 = d(P[:, 6], P[:, 7], P[:, 0], P[:, 1])
    return ~(((d1 < 0) | (d2 < 0) | (d3 < 0)) & ((d1 > 0) | (d2 > 0) | (d3 > 0)))


def build_stamp_depth(pos, f, res=STAMP_DEPTH_RES):
    """buildStampDepth: {data: float32 (nu*nv), nu, nv, texel}."""
    P = np.asarray(pos, np.float64).reshape(-1, 9)
    big = max(f["sizeU"], f["sizeV"])
    nu = max(16, int(jround(res * f["sizeU"] / big)))
    nv = max(16, int(jround(res * f["sizeV"] / big)))
    data = np.full(nu * nv, STAMP_DEPTH_EMPTY, F32)
    mode, side, cr, sr, sU, sV = f["mode"], f["side"], f["cosR"], f["sinR"], f["sizeU"], f["sizeV"]
    cyl = mode == MODE_CYLINDRICAL
    thC = math.atan2(f["cy"] - f["ccy"], f["cx"] - f["ccx"]) if cyl else 0
    reach = 0.5 * hypot1(sU, sV) + 1e-6
    zlo, zhi = f["cz"] - reach, f["cz"] + reach
    X, Y, Z = P[:, 0::3], P[:, 1::3], P[:, 2::3]
    A, B, DTH = _stamp_ab(X, Y, Z, f)
    TX = ((cr * A - sr * B) / sU + 0.5) * nu - 0.5
    TY = ((sr * A + cr * B) / sV + 0.5) * nv - 0.5
    TH = _stamp_height(X, Y, Z, f)
    keep = np.ones(len(P), bool)
    if cyl:
        keep &= ~(((Z > zhi).all(1)) | ((Z < zlo).all(1)))
        keep &= ~((DTH.max(1) - DTH.min(1) > math.pi) | _tri_contains_axis(P, f["ccx"], f["ccy"]))
    for t in np.nonzero(keep)[0]:
        tx, ty, th = TX[t].tolist(), TY[t].tolist(), TH[t].tolist()
        pa, pb = A[t].tolist(), B[t].tolist()
        i0 = max(0, math.ceil(min(tx)))
        i1 = min(nu - 1, math.floor(max(tx)))
        j0 = max(0, math.ceil(min(ty)))
        j1 = min(nv - 1, math.floor(max(ty)))
        if i0 > i1 or j0 > j1:
            continue
        area_ab = (pa[1] - pa[0]) * (pb[2] - pb[0]) - (pa[2] - pa[0]) * (pb[1] - pb[0])
        span = max(abs(pa[1] - pa[0]), abs(pa[2] - pa[0]), abs(pb[1] - pb[0]), abs(pb[2] - pb[0]))
        if abs(area_ab) <= 1e-9 * span * span:
            continue
        cells = _tri_texels(tx, ty, nu, nv)
        if cells is None or not len(cells[0]):
            continue
        ii, jj = cells
        hmin, hmax = min(th), max(th)
        area = (tx[1] - tx[0]) * (ty[2] - ty[0]) - (tx[2] - tx[0]) * (ty[1] - ty[0])
        if not cyl:
            dhdi = ((th[1] - th[0]) * (ty[2] - ty[0]) - (th[2] - th[0]) * (ty[1] - ty[0])) / area
            dhdj = ((th[2] - th[0]) * (tx[1] - tx[0]) - (th[1] - th[0]) * (tx[2] - tx[0])) / area
            h = th[0] + dhdi * (ii - tx[0]) + dhdj * (jj - ty[0])
            h = np.where(h < hmin, hmin, np.where(h > hmax, hmax, h))
        else:
            o = P[t]
            ax, ay, az = o[0], o[1], o[2]
            e1 = o[3:6] - o[0:3]
            e2 = o[6:9] - o[0:3]
            nx = e1[1] * e2[2] - e1[2] * e2[1]
            ny = e1[2] * e2[0] - e1[0] * e2[2]
            nz = e1[0] * e2[1] - e1[1] * e2[0]
            neps = 1e-9 * (hypot1(nx, ny, nz) or 1)
            dd = nx * ax + ny * ay + nz * az
            ra = ((ii + 0.5) / nu - 0.5) * sU
            rb = ((jj + 0.5) / nv - 0.5) * sV
            a = cr * ra + sr * rb
            b = -sr * ra + cr * rb
            ang = thC + (-a if side < 0 else a) / f["r"]
            den = nx * np.cos(ang) + ny * np.sin(ang)
            with np.errstate(all="ignore"):
                rad = np.where(np.abs(den) < neps, np.nan, (dd - nx * f["ccx"] - ny * f["ccy"] - nz * (f["cz"] + b)) / den)
            h = np.where(rad >= 0, rad * side, np.nan)
            h = np.where(h <= hmax, h, hmax)
        k = jj * nu + ii
        h32 = h.astype(np.float64)
        cur = data[k].astype(np.float64)
        # Several texels never repeat within one triangle, so a plain max per texel holds.
        data[k] = np.where(h32 > cur, h32, cur).astype(F32)
    return {"data": data, "nu": nu, "nv": nv, "texel": max(sU / nu, sV / nv)}


def stamp_untouched_faces(pos, f):
    """stampUntouchedFaces: uint8 per triangle, 1 where the stamp cannot reach."""
    P = np.asarray(pos, np.float64).reshape(-1, 9)
    n = len(P)
    cyl = f["mode"] == MODE_CYLINDRICAL
    cr, sr = f["cosR"], f["sinR"]
    margin = f["fade"] * 1.25
    mu, mv = margin / f["sizeU"], margin / f["sizeV"]
    d = f.get("depth")
    hidden_by = max(_stamp_depth_tol_max(d), margin) if d is not None else 0
    vid, nv_, _ = weld(P.reshape(-1, 3), 1e5)
    e1 = P[:, 3:6] - P[:, 0:3]
    e2 = P[:, 6:9] - P[:, 0:3]
    nx = e1[:, 1] * e2[:, 2] - e1[:, 2] * e2[:, 1]
    ny = e1[:, 2] * e2[:, 0] - e1[:, 0] * e2[:, 2]
    nz = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
    cx = P[:, 0] + (e1[:, 0] + e2[:, 0]) / 3
    cy = P[:, 1] + (e1[:, 1] + e2[:, 1]) / 3
    cz = P[:, 2] + (e1[:, 2] + e2[:, 2]) / 3
    facing = _stamp_facing(nx, ny, nz, cx, cy, cz, f)
    toward = np.zeros(nv_, bool)
    toward[vid.reshape(-1, 3)[facing > 0].reshape(-1)] = True
    out = np.zeros(n, np.uint8)
    reached = toward[vid.reshape(-1, 3)].any(1)
    out[~reached] = 1
    X, Y, Z = P[:, 0::3], P[:, 1::3], P[:, 2::3]
    A, B, DTH = _stamp_ab(X, Y, Z, f)
    amin, amax, bmin, bmax = A.min(1), A.max(1), B.min(1), B.max(1)
    skip = np.zeros(n, bool)
    if cyl:
        skip = (DTH.max(1) - DTH.min(1) > math.pi) | _tri_contains_axis(P, f["ccx"], f["ccy"])
    us, vs = [], []
    for c in range(4):
        a = amax if c & 1 else amin
        b = bmax if c & 2 else bmin
        us.append((cr * a - sr * b) / f["sizeU"] + 0.5)
        vs.append((sr * a + cr * b) / f["sizeV"] + 0.5)
    U, V = np.stack(us, 1), np.stack(vs, 1)
    outside = (U.max(1) < -mu) | (U.min(1) > 1 + mu) | (V.max(1) < -mv) | (V.min(1) > 1 + mv)
    test = reached & ~skip
    out[test & outside] = 1
    if d is not None:
        TX = ((cr * A - sr * B) / f["sizeU"] + 0.5) * d["nu"] - 0.5
        TY = ((sr * A + cr * B) / f["sizeV"] + 0.5) * d["nv"] - 0.5
        HM = _stamp_height(X, Y, Z, f).max(1)
        for t in np.nonzero(test & ~outside)[0]:
            cells = _tri_texels(TX[t].tolist(), TY[t].tolist(), d["nu"], d["nv"], True)
            if cells is None or not len(cells[0]):
                continue
            mind = float(d["data"][cells[1] * d["nu"] + cells[0]].min())
            if HM[t] + hidden_by < mind:
                out[t] = 1
    return out
