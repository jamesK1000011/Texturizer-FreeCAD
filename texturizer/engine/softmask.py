# SPDX-License-Identifier: AGPL-3.0-only
# Port of js/softMask.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# Soft (brushed) mask values: brush coverage, per-vertex values to corners
# and back, the soft exclusion a layer reads, and a source mesh's per-corner
# values carried onto the refined mesh through the parent-face map
# (barycentric in the parent, clamped).
import numpy as np

from ._js import F32


def brush_coverage(dist, radius, hardness):
    core = radius * hardness
    if dist <= core:
        return 1.0
    if dist >= radius:
        return 0.0
    t = (dist - core) / (radius - core)
    return 1 - t * t * (3 - 2 * t)


def has_soft_paint(values):
    return values is not None and bool((np.asarray(values) > 0).any())


def values_to_corners(values, vert_id):
    return np.asarray(values, F32)[np.asarray(vert_id)]


def corners_to_values(corners, vert_id, vert_count):
    out = np.zeros(vert_count, F32)
    np.maximum.at(out, np.asarray(vert_id), np.asarray(corners, F32))
    return out


def build_soft_exclusion(vert_id, values, hard_faces, include):
    vert_id = np.asarray(vert_id)
    s = np.asarray(values, F32)[vert_id].astype(np.float64)
    hard = np.zeros(len(vert_id) // 3, bool)
    for t in hard_faces:
        hard[t] = True
    hc = np.repeat(hard, 3)
    out = np.where(hc, 0.0, 1 - s) if include else np.where(hc, 1.0, s)
    return out.astype(F32)


def soft_painted_faces(vert_id, values):
    v = np.asarray(values)[np.asarray(vert_id)].reshape(-1, 3)
    return (v > 0).any(axis=1).astype(np.uint8)


def interpolate_from_parents(child_pos, parent_id, parent_pos, parent_vals):
    """interpolateFromParents: per child corner, the parent face's corner values
    interpolated at the corner (float32)."""
    pid = np.asarray(parent_id, np.int64)
    pv = np.asarray(parent_vals, F32).astype(np.float64).reshape(-1, 3)[pid]
    va, vb, vc = pv[:, 0], pv[:, 1], pv[:, 2]
    same = (va == vb) & (va == vc)
    pp = np.asarray(parent_pos, F32).astype(np.float64).reshape(-1, 9)[pid]
    A = pp[:, 0:3]
    e0 = pp[:, 3:6] - A
    e1 = pp[:, 6:9] - A
    d00 = (e0[:, 0] * e0[:, 0] + e0[:, 1] * e0[:, 1]) + e0[:, 2] * e0[:, 2]
    d01 = (e0[:, 0] * e1[:, 0] + e0[:, 1] * e1[:, 1]) + e0[:, 2] * e1[:, 2]
    d11 = (e1[:, 0] * e1[:, 0] + e1[:, 1] * e1[:, 1]) + e1[:, 2] * e1[:, 2]
    den = d00 * d11 - d01 * d01
    degen = ~(den > 1e-12 * d00 * d11)
    cp = np.asarray(child_pos, F32).astype(np.float64).reshape(-1, 3, 3)
    out = np.empty((len(pid), 3))
    with np.errstate(all="ignore"):
        for k in range(3):
            p = cp[:, k] - A
            d20 = (p[:, 0] * e0[:, 0] + p[:, 1] * e0[:, 1]) + p[:, 2] * e0[:, 2]
            d21 = (p[:, 0] * e1[:, 0] + p[:, 1] * e1[:, 1]) + p[:, 2] * e1[:, 2]
            v = (d11 * d20 - d01 * d21) / den
            w = (d00 * d21 - d01 * d20) / den
            u = 1 - v - w
            neg = (u < 0) | (v < 0) | (w < 0)
            uc, vc_, wc = np.where(u > 0, u, 0.0), np.where(v > 0, v, 0.0), np.where(w > 0, w, 0.0)
            s = uc + vc_ + wc
            ok = s > 0
            sn = np.where(ok, s, 1.0)
            u = np.where(neg, np.where(ok, uc / sn, 1 / 3), u)
            v = np.where(neg, np.where(ok, vc_ / sn, 1 / 3), v)
            w = np.where(neg, np.where(ok, wc / sn, 1 / 3), w)
            u = np.where(degen, 1 / 3, u)
            v = np.where(degen, 1 / 3, v)
            w = np.where(degen, 1 / 3, w)
            val = (u * va + v * vb) + w * vc
            out[:, k] = np.clip(val, 0, 1)
    out[same] = va[same][:, None]
    return out.astype(F32).reshape(-1)
