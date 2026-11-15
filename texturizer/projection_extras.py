# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Auto-fit, Reset, Stamp mode and Position on model are ports of BumpMesh
# (stlTexturizer) by Stefan Hermann (CNC Kitchen), AGPL-3.0,
# github.com/CNCKitchen/stlTexturizer: js/main.js (autoFitCylinderAxis, the
# cylinder Reset button, _setStampMode, _placeStampAt, _moveStampTo,
# _moveNormalStamp, _stampSurfaceUnder, _stampNormalRadius) and js/mapping.js
# (stampSurfaceAt and its helpers).
"""Pure helpers behind the Projection section's buttons.

Each returns a dict of draft updates by BumpMesh's setting names (for the
active layer), or None when there is nothing to do (BumpMesh's button does
nothing then either):

    auto_fit(positions)               Auto-fit: least-squares circle (Kasa) of the
                                      side triangles -> cylinderCenterX/Y, cylinderRadius
    reset()                           Reset: the three back to None (bounds defaults)
    from_face(surface)                From face (added): centre and radius of a
                                      cylindrical face's surface (.Center, .Axis, .Radius)
    stamp_mode(settings, on)          Stamp mode on / off: stampOnce, mappingMode
    stamp_place(point, normal, ...)   Position on model: a picked point and normal
                                      -> stampCenterX/Y/Z, stampSide, stampNormalX/Y/Z
                                      (and mappingMode when the pick chooses the axis)

positions are the part's triangles in the texturing frame: an (N, 3, 3) or
(N, 9) array or a flat list, as BumpMesh's non-indexed position array.
The stamp fields are BumpMesh's own names; apply(draft, updates) sets the
keys the draft has and keeps the others on the layer under "bumpmesh" (the
draft has no stamp fields yet), which project.py saves.

No Qt or FreeCAD import here.
"""
import math

import numpy as np

MODE_PLANAR_XY, MODE_PLANAR_XZ, MODE_PLANAR_YZ, MODE_CYLINDRICAL = 0, 1, 2, 3
MODE_TRIPLANAR, MODE_SURFACE_NORMAL = 5, 7
STAMP_MODES = (MODE_SURFACE_NORMAL, MODE_PLANAR_XY, MODE_PLANAR_XZ, MODE_PLANAR_YZ, MODE_CYLINDRICAL)
STAMP_NORMAL_RADIUS = 0.5   # main.js: the fit spans the stamp's inscribed circle
STAMP_NORMAL_CONE = math.sqrt(0.5)
STAMP_FIT_SAMPLES = 6000
CYLINDER_KEYS = ("cylinderCenterX", "cylinderCenterY", "cylinderRadius")
STAMP_KEYS = ("stampCenterX", "stampCenterY", "stampCenterZ", "stampSide", "stampNormalX", "stampNormalY", "stampNormalZ")


def _tris(positions):
    return np.asarray(positions, dtype=float).reshape(-1, 3, 3)


def _bounds(tris):
    p = tris.reshape(-1, 3)
    lo, hi = p.min(axis=0), p.max(axis=0)
    return {"min": tuple(lo), "max": tuple(hi), "size": tuple(hi - lo), "center": tuple((lo + hi) * 0.5)}


def _face_normals(tris):
    """Unit face normals, (B - A) x (C - A) normalised as three.js does
    (a zero vector stays zero)."""
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    ln = np.linalg.norm(n, axis=1)
    return n / np.where(ln > 0, ln, 1.0)[:, None]


def _xyz(v):
    if hasattr(v, "x"):
        return float(v.x), float(v.y), float(v.z)
    return tuple(float(a) for a in v)


# -- Cylinder Axis ---------------------------------------------------------------
def auto_fit(positions, bounds=None, face_normals=None):
    """BumpMesh's Auto-fit (autoFitCylinderAxis): a least-squares circle
    (Kasa) through the vertices of the triangles whose normal is roughly
    perpendicular to Z (|n.z| < 0.5), so end caps don't pull the fit. None
    when it fails (fewer than 10 vertices, singular, or a radius beyond 5x the
    part's XY size or under 0.001 mm)."""
    tris = _tris(positions)
    if not len(tris):
        return None
    fn = _face_normals(tris) if face_normals is None else np.asarray(face_normals, dtype=float).reshape(-1, 3)
    side = tris[np.abs(fn[:, 2]) < 0.5].reshape(-1, 3)
    n = len(side)
    if n < 10:
        return None
    x, y = side[:, 0], side[:, 1]
    z = x * x + y * y
    Sx, Sy, Sxx, Syy, Sxy = x.sum(), y.sum(), (x * x).sum(), (y * y).sum(), (x * y).sum()
    Sxz, Syz, Sz = (x * z).sum(), (y * z).sum(), z.sum()
    M = [[Sxx, Sxy, Sx], [Sxy, Syy, Sy], [Sx, Sy, float(n)]]
    b = [Sxz, Syz, Sz]

    def det(m):
        return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))

    D = det(M)
    if abs(D) < 1e-12:
        return None

    def col(c):
        return [[b[i] if j == c else v for j, v in enumerate(row)] for i, row in enumerate(M)]

    A, B, C = det(col(0)) / D, det(col(1)) / D, det(col(2)) / D
    cx, cy = A / 2, B / 2
    r2 = C + cx * cx + cy * cy
    if not math.isfinite(r2) or r2 <= 0:
        return None
    r = math.sqrt(r2)
    size = (bounds or _bounds(tris))["size"]
    if r > max(size[0], size[1]) * 5 or r < 1e-3:
        return None
    return {"cylinderCenterX": float(cx), "cylinderCenterY": float(cy), "cylinderRadius": float(r)}


def reset():
    """BumpMesh's Reset: centre and radius back to null, i.e. the bounds'
    centre and half the larger XY size."""
    return {k: None for k in CYLINDER_KEYS}


def align_rotation(axis):
    """Rotate X / Y (degrees, BumpMesh's Euler XYZ) that turn axis onto +Z,
    for a face whose axis is not along the frame's Z."""
    vx, vy, vz = _xyz(axis)
    ln = math.sqrt(vx * vx + vy * vy + vz * vz)
    if ln <= 0:
        raise ValueError("the axis has no length")
    vx, vy, vz = vx / ln, vy / ln, vz / ln
    ry = math.atan2(-vx, vz)            # Ry first takes the axis into the YZ plane ...
    h = math.hypot(vx, vz)
    rx = math.atan2(vy, h)              # ... then Rx onto +Z (Euler XYZ: v' = Rx Ry v)
    return {"rotateX": math.degrees(rx), "rotateY": math.degrees(ry), "rotateZ": 0.0}


def from_face(surface, to_frame=None, tol=1e-6):
    """From face (added): the cylinder axis read exactly from a picked
    cylindrical B-rep face. surface is duck-typed like Part.Cylinder: .Center
    (a point on the axis), .Axis, .Radius. to_frame(v, is_direction) maps
    document coordinates into the texturing frame (default: unchanged).
    BumpMesh's cylinder axis is Z, so a face whose axis is not along Z (within
    tol) raises ValueError naming the align_rotation() that would fix it.
    Also switches the layer to Cylindrical."""
    c, a, r = _xyz(surface.Center), _xyz(surface.Axis), float(surface.Radius)
    if to_frame is not None:
        c, a = _xyz(to_frame(c, False)), _xyz(to_frame(a, True))
    ln = math.sqrt(sum(t * t for t in a))
    if ln <= 0 or not r > 0:
        raise ValueError("not a cylindrical surface (no axis or radius)")
    if 1.0 - abs(a[2] / ln) > tol:
        rot = align_rotation(a)
        raise ValueError("the face's axis is not along the texturing frame's Z; Rotate X %.4g°, Y %.4g° first"
                         % (rot["rotateX"], rot["rotateY"]))
    return {"mappingMode": MODE_CYLINDRICAL, "cylinderCenterX": c[0], "cylinderCenterY": c[1], "cylinderRadius": r}


# -- Stamp -----------------------------------------------------------------------
def stamp_mode(settings, on, mode_before=None):
    """BumpMesh's Stamp mode button (_setStampMode). On: a projection that
    can't stamp (Triplanar, Cubic, Spherical, Outline wrap) becomes Surface
    normal; the caller keeps settings["mappingMode"] as mode_before. Off: back
    to mode_before (or Triplanar from Surface normal, which exists for stamps
    only). (Entering Surface normal with a stamp already placed also re-fits its
    normal in BumpMesh; stamp_place does that on the next placement.)"""
    mode = settings.get("mappingMode", MODE_TRIPLANAR)
    if on:
        out = {"stampOnce": True}
        if mode not in STAMP_MODES:
            out["mappingMode"] = MODE_SURFACE_NORMAL
        return out
    out = {"stampOnce": False}
    if mode_before is None and mode == MODE_SURFACE_NORMAL:
        mode_before = MODE_TRIPLANAR
    if mode_before is not None:
        out["mappingMode"] = mode_before
    return out


def stamp_radius(settings, tex_size=None):
    """_stampNormalRadius: half the stamp's shorter side (mm), through the
    texture's aspect like stampFrame (tex_size = (width, height) px)."""
    tw, th = tex_size or (1, 1)
    tmax = max(tw, th, 1)
    su = max(float(settings.get("scaleU") or 0) or 1e-6, 1e-6) / (tmax / max(tw, 1))
    sv = max(float(settings.get("scaleV") or 0) or 1e-6, 1e-6) / (tmax / max(th, 1))
    return STAMP_NORMAL_RADIUS * min(su, sv)


def _closest_on_tri(t, p):
    """Closest point of triangle t (3x3) to p (Ericson 5.1.5), as mapping.js."""
    a, b, c = t
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = ab @ ap, ac @ ap
    if d1 <= 0 and d2 <= 0:
        return a
    bp = p - b
    d3, d4 = ab @ bp, ac @ bp
    if d3 >= 0 and d4 <= d3:
        return b
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        return a + d1 / (d1 - d3) * ab
    cp = p - c
    d5, d6 = ab @ cp, ac @ cp
    if d6 >= 0 and d5 <= d6:
        return c
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        return a + d2 / (d2 - d6) * ac
    va = d3 * d6 - d5 * d4
    if va <= 0 and d4 - d3 >= 0 and d5 - d6 >= 0:
        return b + (d4 - d3) / ((d4 - d3) + (d5 - d6)) * (c - b)
    s = va + vb + vc
    if not s > 0:
        return a
    return a + ab * (vb / s) + ac * (vc / s)


def _near_box(lo, hi, q, r):
    """mapping.js _triNearBox, over all triangles: bounding boxes within r of q."""
    return np.all((hi >= q - r) & (lo <= q + r), axis=1)


def stamp_surface_at(positions, q, hint, radius, reach=None):
    """mapping.js stampSurfaceAt: the closest surface point to q within
    reach (on faces within ~70° of hint) and the dominant normal of the
    surface within radius of it. (x, y, z, nx, ny, nz) or None."""
    tris = _tris(positions)
    radius = max(radius, 1e-6)
    reach = radius if reach is None else reach
    q = np.asarray(q, dtype=float)
    hint = np.asarray(hint, dtype=float)
    hl = float(np.linalg.norm(hint)) or 1.0
    lo, hi = tris.min(axis=1), tris.max(axis=1)
    cand = np.nonzero(_near_box(lo, hi, q, reach + radius))[0]
    nraw = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    best, best_d2, p = -1, reach * reach, None
    for t in cand:
        if not _near_box(lo[t:t + 1], hi[t:t + 1], q, math.sqrt(best_d2))[0]:
            continue
        n = nraw[t]
        ln = float(np.linalg.norm(n))
        if not ln > 0 or float(n @ hint) < 0.34 * ln * hl:
            continue
        cp = _closest_on_tri(tris[t], q)
        d2 = float(((cp - q) ** 2).sum())
        if (d2 <= best_d2) if best < 0 else (d2 < best_d2):
            best, best_d2, p = t, d2, cp
    if best < 0:
        return None
    fb = nraw[best] / np.linalg.norm(nraw[best])
    nc = len(cand)
    stride = 2 ** max(0, math.floor(math.log2(nc / STAMP_FIT_SAMPLES))) if nc else 1
    share = math.pi * radius * radius / STAMP_FIT_SAMPLES
    F = []
    for t in cand:
        if not _near_box(lo[t:t + 1], hi[t:t + 1], p, radius)[0]:
            continue
        n = nraw[t]
        ln = float(np.linalg.norm(n))
        if not ln > 0:
            continue
        big = 0.5 * ln >= share
        if not big and t % stride != 0:
            continue
        cp = _closest_on_tri(tris[t], p)
        dv = cp - p
        d = float(np.linalg.norm(dv))
        if d > radius:
            continue
        fall = 1 - d / radius
        F.append((n / ln, min(0.5 * ln, radius * radius) * fall * fall * (1 if big else stride), dv))

    def mean(e, cone, plane):
        s = np.zeros(3)
        for nn, w, dv in F:
            if float(nn @ e) < cone:
                continue
            if plane and abs(float(dv @ e)) > 0.5 * radius:
                continue
            s += nn * w
        sl = float(np.linalg.norm(s))
        return s / sl if sl > 0 else None

    e = mean(hint / hl, 0.34, False)
    e = fb if e is None else e
    for _ in range(4):
        m = mean(e, STAMP_NORMAL_CONE, True)
        e = e if m is None else m
    return (float(p[0]), float(p[1]), float(p[2]), float(e[0]), float(e[1]), float(e[2]))


def stamp_place(point, normal, settings, bounds=None, positions=None, pick_axis=True, tex_size=None):
    """Position on model (_placeStampAt with a click, no drag): centre the
    stamp on the picked point. With pick_axis the picked face also chooses the
    projection: for Planar modes the axis its normal is nearest (switching
    between XY / XZ / YZ) and the side it faces; for Cylindrical, outside or
    inside (a cap keeps the side). Surface normal snaps to the surface and fits
    its normal over the stamp's inscribed circle when positions are given
    (else the picked normal, as BumpMesh's fallback). bounds (uvmath style) is
    needed for Cylindrical with no centre set; positions give them otherwise."""
    p = _xyz(point)
    n = np.asarray(_xyz(normal), dtype=float)
    ln = float(np.linalg.norm(n))
    n = n / ln if ln > 0 else n
    mode = settings.get("mappingMode")
    if mode == MODE_SURFACE_NORMAL:
        s = None
        if positions is not None:
            R = stamp_radius(settings, tex_size)
            s = stamp_surface_at(positions, p, n, R, 0.1 * R + 0.5)
        if s is None:
            s = (p[0], p[1], p[2], float(n[0]), float(n[1]), float(n[2]))
        return dict(zip(("stampCenterX", "stampCenterY", "stampCenterZ", "stampNormalX", "stampNormalY", "stampNormalZ"), s))
    out = {}
    if pick_axis:
        if mode == MODE_CYLINDRICAL:
            cx, cy = settings.get("cylinderCenterX"), settings.get("cylinderCenterY")
            if cx is None or cy is None:
                if bounds is None:
                    bounds = _bounds(_tris(positions)) if positions is not None else {"center": (0.0, 0.0, 0.0)}
                cx = bounds["center"][0] if cx is None else cx
                cy = bounds["center"][1] if cy is None else cy
            rx, ry = p[0] - cx, p[1] - cy
            L = math.hypot(rx, ry)
            radial = (n[0] * rx + n[1] * ry) / L if L > 1e-6 else 0.0
            if abs(radial) > 0.2:
                out["stampSide"] = 1 if radial > 0 else -1
        else:
            ax, ay, az = abs(n[0]), abs(n[1]), abs(n[2])
            m = MODE_PLANAR_XY if (az >= ax and az >= ay) else (MODE_PLANAR_XZ if ay >= ax else MODE_PLANAR_YZ)
            along = n[2] if m == MODE_PLANAR_XY else n[1] if m == MODE_PLANAR_XZ else n[0]
            out["stampSide"] = 1 if along >= 0 else -1
            if m != mode:
                out["mappingMode"] = m
    out.update({"stampCenterX": p[0], "stampCenterY": p[1], "stampCenterZ": p[2]})
    return out


# -- applying ------------------------------------------------------------------
def apply(draft, updates, layer=None):
    """Put updates on a draft layer: keys the draft knows through
    draft.update (one undo step), BumpMesh keys it lacks (the stamp fields)
    on the layer's "bumpmesh" dict."""
    from . import controls

    i = draft.active if layer is None else layer
    known = {k: v for k, v in updates.items() if k in controls.BY_KEY}
    extra = {k: v for k, v in updates.items() if k not in controls.BY_KEY}
    if known:
        draft.update(known, layer=i)
    if extra:
        draft.layers[i].setdefault("bumpmesh", {}).update(extra)
    return known, extra
