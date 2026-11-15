# SPDX-License-Identifier: AGPL-3.0-only
# Port of js/exportPipeline.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# The heavy pipeline behind Export and Bake, in stages:
#   subdivide → [regularize → re-subdivide] → displace
#   → [decimate] (export) → bottom clamp → smooth bottom → [T-junction repair]
# Each stage is its own function with a result the next takes, so a host can
# keep the subdivided mesh and rerun only displacement when a texture setting
# changes (stage cache). progress(stage, fraction, info) and should_abort()
# are checked between stages and inside the long ones.
import math

import numpy as np

from ._js import F32
from .decimation import decimate
from .displacement import apply_displacement, apply_displacement_layers
from .meshindex import weld
from .meshrepair import count_area_slivers, count_edge_defects, resolve_t_junctions
from .regularize import regularize_mesh
from .softmask import interpolate_from_parents
from .subdivision import subdivide


class Aborted(Exception):
    """should_abort() said stop."""


def _check(should_abort):
    if should_abort and should_abort():
        raise Aborted()


def _face_normal_rows(pa, rows):
    t = pa.reshape(-1, 9)[rows].astype(np.float64)
    u = t[:, 3:6] - t[:, 0:3]
    v = t[:, 6:9] - t[:, 0:3]
    n = np.stack([u[:, 1] * v[:, 2] - u[:, 2] * v[:, 1], u[:, 2] * v[:, 0] - u[:, 0] * v[:, 2],
                  u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]], 1)
    ln = np.sqrt(n[:, 0] * n[:, 0] + n[:, 1] * n[:, 1] + n[:, 2] * n[:, 2])
    ln = np.where(ln == 0, 1.0, ln)
    return np.repeat((n / ln[:, None]).astype(F32)[:, None, :], 3, axis=1).reshape(len(rows), 9)


def clamp_below_bottom(pa, na, bottom_z):
    """clampBelowBottom, in place: nothing below the bottom plane; touched faces re-normalled."""
    z = pa.reshape(-1, 3)[:, 2]
    below = z < bottom_z
    if not below.any():
        return
    z[below] = F32(bottom_z)
    rows = np.nonzero(below.reshape(-1, 3).any(axis=1))[0]
    na.reshape(-1, 9)[rows] = _face_normal_rows(pa, rows)


def snap_bottom_to_flat(pa, na, bottom_z, tol=0.1):
    """snapBottomToFlat, in place: welded vertices within tol of the bottom
    snapped onto it where no incident face would fold (> 75°) or collapse."""
    P = pa.reshape(-1, 3)
    vc = len(P)
    vid, n_unique, firsts = weld(P.astype(np.float64), 1e6)
    bz = float(bottom_z)
    fz = P[firsts, 2].astype(np.float64)
    cand = np.nonzero((fz != bz) & (np.abs(fz - bz) <= tol))[0]
    if not len(cand):
        return 0
    order = np.argsort(vid, kind="stable")
    starts = np.searchsorted(vid[order], np.arange(n_unique + 1))
    fold_cos = math.cos(75 * math.pi / 180)
    fc2 = fold_cos * fold_cos
    Z = P[:, 2]
    X = P[:, 0].astype(np.float64)
    Y = P[:, 1].astype(np.float64)
    bz32 = F32(bz)
    dirty = set()
    vidl = vid
    for i in cand.tolist():
        inc = order[starts[i]:starts[i + 1]].tolist()
        ok = True
        for c in inc:
            t = c // 3
            b = t * 3
            zs = [bz if vidl[b + v] == i else float(Z[b + v]) for v in range(3)]
            ax, ay, az = X[b], Y[b], float(Z[b])
            oux, ouy, ouz = X[b + 1] - ax, Y[b + 1] - ay, float(Z[b + 1]) - az
            ovx, ovy, ovz = X[b + 2] - ax, Y[b + 2] - ay, float(Z[b + 2]) - az
            onx, ony, onz = ouy * ovz - ouz * ovy, ouz * ovx - oux * ovz, oux * ovy - ouy * ovx
            nuz, nvz = zs[1] - zs[0], zs[2] - zs[0]
            nnx, nny, nnz = ouy * nvz - nuz * ovy, nuz * ovx - oux * nvz, oux * ovy - ouy * ovx
            o2 = onx * onx + ony * ony + onz * onz
            n2 = nnx * nnx + nny * nny + nnz * nnz
            if n2 < 1e-20:
                ok = False
                break
            if o2 < 1e-20:
                continue
            dot = onx * nnx + ony * nny + onz * nnz
            if dot < 0 or dot * dot < fc2 * o2 * n2:
                ok = False
                break
        if not ok:
            continue
        for c in inc:
            Z[c] = bz32
            dirty.add(c // 3)
    if dirty:
        rows = np.array(sorted(dirty), np.int64)
        na.reshape(-1, 9)[rows] = _face_normal_rows(pa, rows)
    return len(dirty)


def _bounds(b):
    return {k: tuple(float(x) for x in b[k]) for k in ("min", "max", "size", "center")}


def stage_subdivide(inp, progress=None, should_abort=None):
    """Subdivision (+ regularize and re-subdivide): {"geom", "faceParentId", "safetyCapHit"}.
    Reusable until the mesh, resolution or the face weights change."""
    s = inp["settings"]
    prog = progress or (lambda *a: None)
    prog("subdivide1", 0)
    _check(should_abort)
    sub = subdivide(inp["positions"], s["refineLength"],
                    lambda p, n, l: prog("subdivide1", p, {"triCount": n, "longestEdge": l}),
                    inp.get("faceWeights"), should_abort=should_abort)
    if sub is None:
        raise Aborted()
    parents = sub["faceParentId"]
    if s.get("regularizeEnabled"):
        prog("regularize", 0)
        reg = regularize_mesh(sub["position"], parents, s["refineLength"], inp.get("regularizeOpts") or {},
                              sub.get("excludeWeight"))
        _check(should_abort)
        resub = subdivide(reg["position"], s["refineLength"] * s["regularizeSecondPassMul"],
                          lambda p, n, l: prog("subdivide2", p, {"triCount": n, "longestEdge": l}),
                          reg.get("excludeWeight"), fast=False, should_abort=should_abort)
        if resub is None:
            raise Aborted()
        parents = np.asarray(reg["faceParentId"])[resub["faceParentId"]].astype(np.int32)
        sub = resub
    _check(should_abort)
    geom = {"position": sub["position"], "normal": sub["normal"], "excludeWeight": sub.get("excludeWeight")}
    return {"geom": geom, "faceParentId": parents, "safetyCapHit": bool(sub["safetyCapHit"])}


def stage_displace(inp, sub, progress=None, should_abort=None):
    """Displacement of a subdivided mesh: {"position", "normal", "lockedFaces"}."""
    s = inp["settings"]
    prog = progress or (lambda *a: None)
    geom = dict(sub["geom"])
    parents = sub["faceParentId"]
    n = len(geom["position"]) // 9
    prog("displace", 0, {"triCount": n})
    bounds = _bounds(inp["bounds"])
    layers = inp.get("layers") or None
    if inp.get("softExclude") is not None:
        geom["softExclude"] = interpolate_from_parents(geom["position"], parents, inp["positions"], inp["softExclude"])
    if layers:
        refined = []
        for L in layers:
            hf = None
            if L.get("hardFaces") is not None:
                hf = np.asarray(L["hardFaces"], np.uint8)[parents]
            ex = None
            if L.get("exclude") is not None:
                ex = interpolate_from_parents(geom["position"], parents, inp["positions"], L["exclude"])
            refined.append({"image": L["image"], "settings": L["settings"], "blendAdd": bool(L.get("blendAdd")),
                            "hardFaces": hf, "stampDepth": L.get("stampDepth"), "exclude": ex})
        out = apply_displacement_layers(geom, refined, s, bounds, lambda p: prog("displace", p, {"triCount": n}))
    else:
        out = apply_displacement(geom, inp["image"], s, bounds, lambda p: prog("displace", p, {"triCount": n}))
    _check(should_abort)
    locked = None
    if s.get("preserveUntextured") and geom.get("excludeWeight") is not None:
        locked = (np.asarray(geom["excludeWeight"], F32)[0::3] > 0.99).astype(np.uint8)
    return {"position": np.asarray(out["position"], F32).copy(), "normal": np.asarray(out["normal"], F32).copy(),
            "lockedFaces": locked}


def stage_finish(inp, disp, mode="export", progress=None, should_abort=None):
    """Decimate, clamp, snap, repair: the pipeline's result dict."""
    s = inp["settings"]
    prog = progress or (lambda *a: None)
    bounds = inp["bounds"]
    pa, na = disp["position"], disp["normal"]
    n = len(pa) // 9
    needs = n > s["maxTriangles"]
    run_dec = mode == "export" and (needs or bool(s.get("harvestFlatFaces")))
    locked_over = False
    if run_dec:
        prog("decimate", 0, {"from": n, "needsDecimation": needs})
        dec = decimate(pa, s["maxTriangles"], lambda p: prog("decimate", p, {"from": n, "needsDecimation": needs}),
                       bool(s.get("harvestFlatFaces")), s.get("harvestTol", 0.005), disp.get("lockedFaces"),
                       should_abort=should_abort)
        if dec is None:
            raise Aborted()
        locked_over = bool(dec["userData"].get("lockedOverBudget"))
        pa, na = dec["position"], dec["normal"]
        _check(should_abort)
    else:
        pa, na = pa.copy(), na.copy()
    bottom = float(bounds["min"][2])
    if (s.get("bottomAngleLimit") or 0) > 0:
        clamp_below_bottom(pa, na, bottom)
    if s.get("smoothBottom") and (s.get("bottomAngleLimit") or 0) > 0:
        snap_bottom_to_flat(pa, na, bottom, 0.1)
    repair = None
    if run_dec:
        prog("repair", 0)
        before = count_area_slivers(pa)
        rep = resolve_t_junctions(pa)
        pa, na = rep["position"], rep["normal"]
        after = count_edge_defects(pa)
        repair = {"beforeSlivers": before, "open": after["open"], "nonManifold": after["nonManifold"],
                  "slivers": count_area_slivers(pa), "tris": after["tris"]}
        _check(should_abort)
    return {"positions": pa, "normals": na, "lockedOverBudget": locked_over, "runDecimation": run_dec,
            "needsDecimation": needs, "repairStats": repair}


def run_export_pipeline(inp, progress=None, should_abort=None, mode="export"):
    """runExportPipeline: None when aborted."""
    try:
        sub = stage_subdivide(inp, progress, should_abort)
        disp = stage_displace(inp, sub, progress, should_abort)
        out = stage_finish(inp, disp, mode, progress, should_abort)
    except Aborted:
        return None
    out["safetyCapHit"] = sub["safetyCapHit"]
    out["faceParentId"] = sub["faceParentId"] if mode == "bake" else None
    return out
