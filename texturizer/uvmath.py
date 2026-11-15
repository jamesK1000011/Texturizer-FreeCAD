# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# A numpy port, line for line, of computeUV and its helpers in js/mapping.js
# of BumpMesh (stlTexturizer) by Stefan Hermann (CNC Kitchen), AGPL-3.0,
# github.com/CNCKitchen/stlTexturizer.
"""The Quick look's UV maths in numpy, vectorised over points.

compute_uv(pos, nrm, mode, settings, bounds) -> (u, v, w), each (N, 3): up to
three weighted samples per point, in fixed slots per mode:

    planar XY / XZ / YZ   slot 0
    cylindrical           0 = side (the seam's right side inside the seam
                          band), 1 = the seam's left side, 2 = the cap
    spherical             0 = right / single, 1 = left
    triplanar, cubic      0 = XY, 1 = XZ, 2 = YZ
    outline wrap          as cylindrical (the Quick look has no outline tables)

Unused slots have weight 0. Rotation, scale (mm, converted with
scale_mm_to_relative), offset, aspect and tiling (fract) are applied, as in
applyTransform. Used to test the shader's maths (quicklook_glsl) and as the CPU
preview's map stage should the engine's differ. No FreeCAD or Qt import.

Known small differences in BumpMesh's own GLSL (kept as BumpMesh has them,
in quicklook_glsl): triplanar normalises with + 1e-4 (here, as mapping.js,
1e-6), and cubic does not snap weights above 0.999 to one-hot.
"""
import math

import numpy as np

MODE_PLANAR_XY, MODE_PLANAR_XZ, MODE_PLANAR_YZ = 0, 1, 2
MODE_CYLINDRICAL, MODE_SPHERICAL, MODE_TRIPLANAR, MODE_CUBIC = 3, 4, 5, 6
MODE_SURFACE_NORMAL, MODE_OUTLINE_WRAP = 7, 8
TWO_PI = math.pi * 2
CUBIC_AXIS_EPSILON = 1e-4


def _g(s, k, d):
    """settings.k ?? d (None counts as missing, as JS's ??)."""
    v = s.get(k) if s else None
    return d if v is None else v


def bounds_of(points):
    """{min, max, size, center} (3-tuples of float) of an (N, 3) array."""
    p = np.asarray(points, dtype=float).reshape(-1, 3)
    lo, hi = p.min(axis=0), p.max(axis=0)
    return {"min": tuple(lo), "max": tuple(hi), "size": tuple(hi - lo), "center": tuple((lo + hi) * 0.5)}


def _b(bounds):
    mn = np.asarray(bounds["min"], dtype=float)
    size = np.asarray(bounds["size"], dtype=float) if "size" in bounds else np.asarray(bounds["max"], float) - mn
    center = np.asarray(bounds["center"], dtype=float) if "center" in bounds else mn + size * 0.5
    return mn, size, center


def scale_reference_lengths(mode, settings, bounds):
    _, size, _ = _b(bounds)
    md = max(size[0], size[1], size[2], 1e-6)
    if mode in (MODE_OUTLINE_WRAP, MODE_CYLINDRICAL):
        # Outline wrap without its tables maps (and measures) as cylindrical.
        r = max(_g(settings, "cylinderRadius", max(size[0], size[1]) * 0.5), 1e-6)
        c = TWO_PI * r
        return c, c
    if mode == MODE_SPHERICAL:
        r = max(0.5 * md, 1e-6)
        return TWO_PI * r, math.pi * r
    return md, md


def outline_wrap_repeats(scale_u):
    return max(1, int(math.floor(1 / scale_u + 0.5)))  # Math.round


def _num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(v) else v


def scale_mm_to_relative(mode, settings, bounds):
    ref_u, ref_v = scale_reference_lengths(mode, settings, bounds)
    u = max(_num(settings.get("scaleU")) or 1e-6, 1e-6) / ref_u
    v = max(_num(settings.get("scaleV")) or 1e-6, 1e-6) / ref_v
    return u, v


def is_rotate_around_z(settings):
    rot = _g(settings, "rotation", 0)
    return bool(settings.get("rotateAroundZ")) and math.fmod(rot, 360) != 0


def rotate_side_frame(px, py, nx, ny, bounds, cos_r, sin_r):
    _, size, center = _b(bounds)
    rx, ry = px - center[0], py - center[1]
    return (cos_r * rx - sin_r * ry + 0.5 * size[0], sin_r * rx + cos_r * ry + 0.5 * size[1],
            cos_r * nx - sin_r * ny, sin_r * nx + cos_r * ny)


def dominant_cubic_axis(nx, ny, nz):
    """0, 1, 2 for x, y, z per point (getDominantCubicAxis)."""
    ax, ay, az = np.abs(nx), np.abs(ny), np.abs(nz)
    e = CUBIC_AXIS_EPSILON
    return np.where((ax >= ay - e) & (ax >= az - e), 0, np.where(ay >= az - e, 1, 2))


def cubic_blend_weights(nx, ny, nz, blend, seam_band_width=0.35):
    """(wx, wy, wz) per point (getCubicBlendWeights)."""
    axis = dominant_cubic_axis(nx, ny, nz)
    ax, ay, az = np.abs(nx), np.abs(ny), np.abs(nz)
    primary = np.choose(axis, [ax, ay, az])
    secondary = np.choose(axis, [np.maximum(ay, az), np.maximum(ax, az), np.maximum(ax, ay)])
    one = [(axis == k).astype(float) for k in range(3)]
    if blend <= 0.001:
        return tuple(one)
    seam_w = max(seam_band_width, CUBIC_AXIS_EPSILON * 2)
    raw = 1 - np.minimum(1, np.maximum(0, (primary - secondary) / seam_w))
    mix = blend * raw * raw * (3 - 2 * raw)
    power = 1 + (1 - mix) * 11
    sx, sy, sz = ax ** power, ay ** power, az ** power
    ssum = sx + sy + sz + 1e-6
    m = [one[0] * (1 - mix) + sx / ssum * mix, one[1] * (1 - mix) + sy / ssum * mix,
         one[2] * (1 - mix) + sz / ssum * mix]
    total = m[0] + m[1] + m[2]
    hot = mix <= 0.001
    return tuple(np.where(hot, one[k], m[k] / total) for k in range(3))


def _fract(x):
    return x - np.floor(x)


def apply_transform(u, v, su, sv, ou, ov, cos_r, sin_r):
    uu = u / su + ou
    vv = v / sv + ov
    if cos_r != 1 or sin_r != 0:
        uu, vv = uu - 0.5, vv - 0.5
        uu, vv = cos_r * uu - sin_r * vv + 0.5, sin_r * uu + cos_r * vv + 0.5
    return _fract(uu), _fract(vv)


def _smooth(t):
    return t * t * (3 - 2 * t)


def _seam(u_raw, v_raw, settings, tr):
    """The atan2-wrap cross-fade: (slot-0 uv, slot-1 uv, w0, w1)."""
    band = _g(settings, "seamBandWidth", 0.5) * 0.1
    dist = np.minimum(u_raw, 1.0 - u_raw)
    zone = (dist < band) if band > 0.001 else np.zeros_like(u_raw, dtype=bool)
    d = np.where(u_raw < 0.5, u_raw, u_raw - 1.0)
    t = _smooth((d + band) / (2.0 * band)) if band > 0 else np.zeros_like(u_raw)
    single = tr(u_raw, v_raw)
    right = tr(d, v_raw)
    left = tr(1.0 + d, v_raw)
    s0 = (np.where(zone, right[0], single[0]), np.where(zone, right[1], single[1]))
    s1 = (np.where(zone, left[0], single[0]), np.where(zone, left[1], single[1]))
    w0 = np.where(zone, t, 1.0)
    w1 = np.where(zone, 1 - t, 0.0)
    return s0, s1, w0, w1


def compute_uv(pos, nrm, mode, settings, bounds):
    """computeUV for N points: (u, v, w), each (N, 3); see the module text."""
    pos = np.asarray(pos, dtype=float).reshape(-1, 3)
    nrm = np.asarray(nrm, dtype=float).reshape(-1, 3)
    n = len(pos)
    if mode == MODE_OUTLINE_WRAP:
        settings = {k: v for k, v in settings.items() if k != "outlineWrap"}
        mode = MODE_CYLINDRICAL
    mn, size, center = _b(bounds)
    a_u = _g(settings, "textureAspectU", 1)
    a_v = _g(settings, "textureAspectV", 1)
    rel_u, rel_v = scale_mm_to_relative(mode, settings, bounds)
    su, sv = rel_u / a_u, rel_v / a_v
    ou, ov = settings.get("offsetU"), settings.get("offsetV")
    ou = 0.0 if ou is None else ou  # JS: undefined offsets would give NaN; the draft always has them
    ov = 0.0 if ov is None else ov
    rot = _g(settings, "rotation", 0) * math.pi / 180
    cos_r, sin_r = math.cos(rot), math.sin(rot)
    md = max(size[0], size[1], size[2], 1e-6)
    px, py, pz = pos[:, 0], pos[:, 1], pos[:, 2]
    nx, ny, nz = nrm[:, 0], nrm[:, 1], nrm[:, 2]
    U, V, W = np.zeros((n, 3)), np.zeros((n, 3)), np.zeros((n, 3))

    def tr(u, v, c=cos_r, s=sin_r):
        return apply_transform(u, v, su, sv, ou, ov, c, s)

    def put(k, uv, w):
        U[:, k], V[:, k], W[:, k] = uv[0], uv[1], w

    if mode in (MODE_PLANAR_XY, MODE_PLANAR_XZ, MODE_PLANAR_YZ):
        a, b = {0: (px - mn[0], py - mn[1]), 1: (px - mn[0], pz - mn[2]), 2: (py - mn[1], pz - mn[2])}[mode]
        put(0, tr(a / md, b / md), 1.0)
    elif mode == MODE_CYLINDRICAL:
        cx = _g(settings, "cylinderCenterX", center[0])
        cy = _g(settings, "cylinderCenterY", center[1])
        r = max(_g(settings, "cylinderRadius", max(size[0], size[1]) * 0.5), 1e-6)
        c = TWO_PI * r
        rx, ry = px - cx, py - cy
        blend = _g(settings, "mappingBlend", 0.0)
        u_raw = np.arctan2(ry, rx) / TWO_PI + 0.5
        v_side = (pz - mn[2]) / c
        s0, s1, w0, w1 = _seam(u_raw, v_side, settings, tr)
        cap_w = np.zeros(n)
        if blend > 0.001:
            thr = math.cos(_g(settings, "capAngle", 20) * math.pi / 180)
            half = _g(settings, "seamBandWidth", 0.5) * 0.5
            raw = np.maximum(0, np.minimum(1, (np.abs(nz) - (thr - half)) / (2 * half + 1e-6)))
            cap_w = _smooth(raw)
        put(0, s0, w0 * (1 - cap_w))
        put(1, s1, w1 * (1 - cap_w))
        put(2, tr(rx / c + 0.5, ry / c + 0.5), cap_w)
    elif mode == MODE_SPHERICAL:
        rx, ry, rz = px - center[0], py - center[1], pz - center[2]
        r = np.sqrt(rx * rx + ry * ry + rz * rz)
        phi = np.arccos(np.maximum(-1, np.minimum(1, rz / np.maximum(r, 1e-6))))
        u_raw = np.arctan2(ry, rx) / TWO_PI + 0.5
        s0, s1, w0, w1 = _seam(u_raw, phi / math.pi, settings, tr)
        put(0, s0, w0)
        put(1, s1, w1)
    else:  # triplanar (and any other mode, as mapping.js's default) or cubic
        around = is_rotate_around_z(settings)
        if around:
            sx, sy, snx, sny = rotate_side_frame(px, py, nx, ny, bounds, cos_r, sin_r)
            side_c, side_s = 1, 0
        else:
            sx, sy, snx, sny = px - mn[0], py - mn[1], nx, ny
            side_c, side_s = cos_r, sin_r
        yz_u = np.where(snx < 0, -(sy / md), sy / md)
        xz_u = np.where(sny > 0, -(sx / md), sx / md)
        xy_u = np.where(nz < 0, -((px - mn[0]) / md), (px - mn[0]) / md)
        t_xy = tr(xy_u, (py - mn[1]) / md)
        t_xz = tr(xz_u, (pz - mn[2]) / md, side_c, side_s)
        t_yz = tr(yz_u, (pz - mn[2]) / md, side_c, side_s)
        if mode == MODE_CUBIC:
            wx, wy, wz = cubic_blend_weights(snx, sny, nz, _g(settings, "mappingBlend", 0.0),
                                             _g(settings, "seamBandWidth", 0.35))
            hx, hy, hz = wx > 0.999, wy > 0.999, wz > 0.999
            hy, hz = hy & ~hx, hz & ~hx & ~hy
            hot = hx | hy | hz
            wx = np.where(hot, hx.astype(float), wx)
            wy = np.where(hot, hy.astype(float), wy)
            wz = np.where(hot, hz.astype(float), wz)
        else:
            ax, ay, az = np.abs(snx), np.abs(sny), np.abs(nz)
            bx, by, bz = (ax * ax) ** 2, (ay * ay) ** 2, (az * az) ** 2
            total = bx + by + bz + 1e-6
            wx, wy, wz = bx / total, by / total, bz / total
        put(0, t_xy, wz)
        put(1, t_xz, wy)
        put(2, t_yz, wx)
    return U, V, W


def blend(u, v, w, fn):
    """sum_k w_k * fn(u_k, v_k) per point: the blended height for a height
    function fn(u, v) -> array (a sampler)."""
    return (w * fn(u, v)).sum(axis=1)
