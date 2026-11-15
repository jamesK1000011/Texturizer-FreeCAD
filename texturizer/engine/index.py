# SPDX-License-Identifier: AGPL-3.0-only
# Port of engine/index.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# BumpMesh's texturing engine as one call: a triangle mesh, up to 4 texture
# layers with their masks, the export settings in; the printable mesh out. The
# same pipeline BumpMesh's Export button runs, fed as main.js feeds it: the
# model centred on its bounding box while it works (where a tile starts
# depends on it) and put back afterwards; one plain layer goes the app's
# single-texture path, several go the layered one.
import math

import numpy as np

from ._js import F32
from .exclusion import build_face_weights
from .exportpipeline import Aborted, stage_displace, stage_finish, stage_subdivide
from .mapping import build_stamp_depth, is_stamp_active, stamp_frame, stamp_untouched_faces
from .outlinewrap import OutlineError, build_outline_wrap

ENGINE_VERSION = "1.0.0"

LAYER_KEYS = [
    "mappingMode", "scaleU", "scaleV", "lockScale",
    "offsetU", "offsetV", "rotation", "rotateAroundZ",
    "amplitude", "textureHeight", "invertDisplacement", "invertTexture", "textureSmoothing",
    "symmetricDisplacement", "mappingBlend", "seamBandWidth", "capAngle",
    "boundaryFalloff", "boundaryFalloffCurve",
    "snapSeamlessWrap", "cylinderCenterX", "cylinderCenterY", "cylinderRadius",
    "stampOnce", "stampCenterX", "stampCenterY", "stampCenterZ", "stampSide",
    "stampNormalX", "stampNormalY", "stampNormalZ",
]

DEFAULTS = {
    "mappingMode": 5, "scaleU": 25, "scaleV": 25, "amplitude": 0.5, "textureHeight": 0.5,
    "invertDisplacement": False, "offsetU": 0, "offsetV": 0, "rotation": 0, "rotateAroundZ": False,
    "refineLength": 1.0, "maxTriangles": 750_000, "lockScale": True,
    "bottomAngleLimit": 5, "topAngleLimit": 0, "mappingBlend": 1, "seamBandWidth": 0.5,
    "textureSmoothing": 0, "invertTexture": False, "blendNormalSmoothing": 32, "capAngle": 20,
    "boundaryFalloff": 0, "boundaryFalloffCurve": "ease", "symmetricDisplacement": False,
    "noDownwardZ": False, "smoothBottom": True, "harvestFlatFaces": True, "harvestTol": 0.005,
    "preserveUntextured": True, "snapSeamlessWrap": True,
    "cylinderCenterX": None, "cylinderCenterY": None, "cylinderRadius": None,
    "stampOnce": False, "stampCenterX": None, "stampCenterY": None, "stampCenterZ": None, "stampSide": None,
    "stampNormalX": None, "stampNormalY": None, "stampNormalZ": None,
    "regularizeEnabled": True, "regularizeAspectThreshold": 5, "regularizeSlack": 3.0,
    "regularizeAggressiveSlack": 8.0, "regularizeExtremeAspect": 8, "regularizeNormalDeg": 15,
    "regularizeAggressiveNormalDeg": 25, "regularizeSecondPassMul": 1.1,
}

GLOBAL_KEYS = [k for k in DEFAULTS if k not in LAYER_KEYS]

MAPPING_MODES = {
    "planarXY": 0, "planarXZ": 1, "planarYZ": 2, "cylindrical": 3, "spherical": 4,
    "triplanar": 5, "cubic": 6, "surfaceNormal": 7, "outlineWrap": 8,
}
MODE_OUTLINE_WRAP = 8
MAX_LAYERS = 4

STAGE_SHARE = {"subdivide1": (0, 0.3), "regularize": (0.3, 0.35), "subdivide2": (0.35, 0.45),
               "displace": (0.45, 0.65), "decimate": (0.65, 0.92), "repair": (0.92, 1)}


def _pick(d, keys):
    return {k: d[k] for k in keys if k in d and d[k] is not None} if d else {}


def _pick_all(d, keys):
    return {k: d[k] for k in keys if k in d} if d else {}


_INT_KEYS = {"mappingMode", "stampSide", "maxTriangles"}


def as_numbers(s):
    """Settings with JS numbers: ints (but codes and counts) as floats, so no
    numpy array takes an integer dtype from a setting."""
    return {k: (float(v) if isinstance(v, int) and not isinstance(v, bool) and k not in _INT_KEYS else
                (int(v) if k in _INT_KEYS and isinstance(v, float) and v.is_integer() else v))
            for k, v in (s or {}).items()}


def layer_settings(s=None):
    """layerSettings: a layer's settings over the defaults; amplitude signed
    from textureHeight and invertDisplacement; scaleV follows scaleU when locked."""
    s = as_numbers(s)
    out = {**as_numbers(_pick_all(DEFAULTS, LAYER_KEYS)), **_pick(s, LAYER_KEYS)}
    if s.get("textureHeight") is not None or s.get("invertDisplacement") is not None or s.get("amplitude") is None:
        out["amplitude"] = (-1 if out["invertDisplacement"] else 1) * abs(out["textureHeight"])
    if out["lockScale"] and s.get("scaleV") is None and s.get("scaleU") is not None:
        out["scaleV"] = out["scaleU"]
    return out


def bounds_of(positions):
    p = np.asarray(positions, F32).reshape(-1, 3)
    mn = p.min(axis=0).astype(np.float64)
    mx = p.max(axis=0).astype(np.float64)
    return {"min": tuple(mn.tolist()), "max": tuple(mx.tolist()), "size": tuple((mx - mn).tolist()),
            "center": tuple(((mn + mx) * 0.5).tolist())}


def _cover_of(mask, tri_count):
    include = bool(mask) and mask.get("mode") == "include"
    paint = None if not mask else mask.get("paint")
    if paint is None:
        if include:
            return np.zeros(tri_count * 3, F32), np.ones(tri_count, np.uint8)
        return None, None
    paint = np.asarray(paint, F32).reshape(-1)
    if len(paint) != tri_count * 3:
        raise ValueError(f"A mask's paint has {len(paint)} values; the mesh has {tri_count * 3} corners.")
    t = paint.reshape(-1, 3)
    hard = (t >= 1).all(axis=1)
    if include:
        cover = paint.copy()
        hm = (~hard & (t == 0).all(axis=1)).astype(np.uint8)
    else:
        cover = (1 - paint.astype(np.float64)).astype(F32)
        hm = hard.astype(np.uint8)
    return cover, hm


def _angle_masked(positions, g):
    p = np.asarray(positions, F32).reshape(-1, 9).astype(np.float64)
    u = p[:, 3:6] - p[:, 0:3]
    v = p[:, 6:9] - p[:, 0:3]
    nx = u[:, 1] * v[:, 2] - u[:, 2] * v[:, 1]
    ny = u[:, 2] * v[:, 0] - u[:, 0] * v[:, 2]
    nz = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    area = np.sqrt(nx * nx + ny * ny + nz * nz)
    with np.errstate(all="ignore"):
        nzn = np.where(area > 1e-12, nz / np.where(area > 1e-12, area, 1), 0.0)
    ang = np.arccos(np.abs(nzn)) * (180 / math.pi)
    bal, tal = g["bottomAngleLimit"], g["topAngleLimit"]
    return np.where(nzn < 0, (bal > 0) & (ang <= bal), (tal > 0) & (ang <= tal))


def _combined_face_weights(positions, excluded, g):
    n = len(positions) // 9
    w = build_face_weights(n, excluded, False, None)
    if not (g["bottomAngleLimit"] > 0 or g["topAngleLimit"] > 0):
        return w
    m = _angle_masked(positions, g) & ~(w[0::3] > 0.99)
    w.reshape(-1, 3)[m] = 1
    return w


def _combined_invertible(positions, faces, include, s):
    if not include:
        return _combined_face_weights(positions, faces, s)
    n = len(positions) // 9
    w = build_face_weights(n, faces, True, None)
    if not (s["bottomAngleLimit"] > 0 or s["topAngleLimit"] > 0):
        return w
    masked = _combined_face_weights(positions, set(), s)
    w[masked > 0.99] = 1
    return w


def _is_whole_faces(paint):
    t = np.asarray(paint).reshape(-1, 3)
    on = t >= 1
    if not ((on[:, 0] == on[:, 1]) & (on[:, 1] == on[:, 2])).all():
        return False
    return not (~on[:, 0] & (t > 0).any(axis=1)).any()


def regularize_opts(g):
    return {
        "aspectThreshold": g["regularizeAspectThreshold"], "slack": g["regularizeSlack"],
        "aggressiveSlack": g["regularizeAggressiveSlack"], "extremeSliverAspect": g["regularizeExtremeAspect"],
        "maxNormalDeltaCos": math.cos(g["regularizeNormalDeg"] * math.pi / 180),
        "aggressiveNormalDeltaCos": math.cos(g["regularizeAggressiveNormalDeg"] * math.pi / 180),
        "preserveExcluded": g["preserveUntextured"],
    }


def _to_working(s, o):
    if o is None:
        return s
    out = dict(s)
    for k, i in (("stampCenterX", 0), ("stampCenterY", 1), ("stampCenterZ", 2), ("cylinderCenterX", 0), ("cylinderCenterY", 1)):
        if out.get(k) is not None:
            out[k] = out[k] - o[i]
    return out


def _shift(p, d):
    q = np.asarray(p, F32).reshape(-1, 3).astype(np.float64)
    return (q + np.asarray(d, np.float64)).astype(F32).reshape(-1)


def prepare_input(positions, layers, settings=None, centered=True):
    """Everything up to the pipeline: the centred mesh, the layers as the
    pipeline takes them, the face weights, and which path (single / layered)."""
    positions = np.asarray(positions, F32).reshape(-1)
    if len(positions) % 9:
        raise ValueError("positions must be whole triangles (9 floats each).")
    settings = settings or {}
    origin = bounds_of(positions)["center"] if centered else None
    pos = _shift(positions, [-c for c in origin]) if origin else positions
    tri_count = len(pos) // 9
    g = as_numbers({**DEFAULTS, **_pick_all(settings, GLOBAL_KEYS)})
    bounds = bounds_of(pos)
    layers = [L for L in (layers or []) if L and L.get("visible", True) is not False and L.get("image") is not None]
    if not layers:
        raise ValueError("No layer with a texture to apply.")
    if len(layers) > MAX_LAYERS:
        raise ValueError("At most 4 layers.")
    wrap = None
    hard_flags = []
    out = []
    for i, L in enumerate(layers):
        s = _to_working(layer_settings({**_pick_all(settings, LAYER_KEYS), **_pick_all(L.get("settings") or {}, LAYER_KEYS)}), origin)
        img = L["image"]
        w, h = img.width, img.height
        if not w or not h:
            raise ValueError(f"Layer {i + 1}'s image is empty.")
        cover, hard = _cover_of(L.get("mask"), tri_count)
        stamp_depth = None
        if is_stamp_active(s):
            tmax = max(w, h, 1)
            frame = stamp_frame({**s, "textureAspectU": tmax / w, "textureAspectV": tmax / h}, bounds)
            if frame:
                frame["depth"] = build_stamp_depth(pos, frame)
                stamp_depth = frame["depth"]
                untouched = np.asarray(stamp_untouched_faces(pos, frame), np.uint8)
                if hard is not None:
                    untouched = untouched | hard
                hard = untouched
        if s["mappingMode"] == MODE_OUTLINE_WRAP and wrap is None:
            try:
                wrap = build_outline_wrap(pos)
            except (OutlineError, ValueError):
                wrap = None
        hard_flags.append(hard)
        exclude = None if cover is None else (1 - cover.astype(np.float64)).astype(F32)
        ls = {**g, **s}
        if wrap is not None and s["mappingMode"] == MODE_OUTLINE_WRAP:
            ls["outlineWrap"] = wrap
        out.append({"image": img, "settings": ls, "exclude": exclude, "hardFaces": hard,
                    "blendAdd": i > 0 and bool(L.get("blendAdd")), "stampDepth": stamp_depth})

    excluded = set()
    if hard_flags and all(h is not None for h in hard_flags):
        allh = np.logical_and.reduce([h.astype(bool) for h in hard_flags])
        excluded = set(np.nonzero(allh)[0].tolist())
    face_weights = (_combined_face_weights(pos, excluded, g)
                    if excluded or g["bottomAngleLimit"] > 0 or g["topAngleLimit"] > 0 else None)

    L0 = layers[0]
    paint0 = None if not L0.get("mask") else L0["mask"].get("paint")
    whole = paint0 is None or _is_whole_faces(paint0)
    single = len(out) == 1 and out[0]["stampDepth"] is None and whole
    if single:
        include = bool(L0.get("mask")) and L0["mask"].get("mode") == "include"
        painted = set()
        if paint0 is not None:
            painted = set(np.nonzero(np.asarray(paint0).reshape(-1, 3)[:, 0] >= 1)[0].tolist())
        s0 = out[0]["settings"]
        weights = (_combined_invertible(pos, painted, include, s0)
                   if painted or include or s0["bottomAngleLimit"] > 0 or s0["topAngleLimit"] > 0 else None)
        pin = {"positions": pos, "faceWeights": weights, "softExclude": None, "image": out[0]["image"],
               "layers": None, "settings": s0}
    else:
        pin = {"positions": pos, "faceWeights": face_weights, "softExclude": None, "image": None,
               "layers": out, "settings": {**g, **out[0]["settings"]}}
    pin["bounds"] = bounds
    pin["regularizeOpts"] = regularize_opts(g)
    pin["origin"] = origin
    pin["inputTriangles"] = tri_count
    return pin


def staged_progress(on_progress):
    if not on_progress:
        return None

    def prog(stage, p, info=None):
        a, b = STAGE_SHARE.get(stage, (0, 1))
        on_progress(stage, a + (b - a) * (p or 0), info)
    return prog


def finish_result(pin, sub, fin):
    pa = fin["positions"]
    if pin["origin"]:
        pa = _shift(pa, pin["origin"])
    warnings = []
    if sub["safetyCapHit"]:
        warnings.append("The subdivision hit its safety cap (10 M triangles): the texture is coarser than refineLength asks. "
                        "Raise refineLength or texture less of the part.")
    if fin["lockedOverBudget"]:
        warnings.append("The untextured faces alone exceed maxTriangles, so the budget could not be met with preserveUntextured on.")
    r = fin["repairStats"]
    if r and (r["open"] or r["nonManifold"]):
        warnings.append(f"The output has {r['open']} open and {r['nonManifold']} non-manifold edges — check it in the slicer.")
    return {"positions": pa, "normals": fin["normals"], "triangles": len(pa) // 9,
            "inputTriangles": pin["inputTriangles"], "warnings": warnings, "repair": r}


def texture_mesh(positions, layers, settings=None, on_progress=None, should_abort=None, centered=True):
    """textureMesh. layers, bottom first: {"image": maps.Image (prepared),
    "settings": layer keys, "mask": {"mode", "paint"} | None, "blendAdd", "visible"}.
    Returns {"positions", "normals", "triangles", "inputTriangles", "warnings",
    "repair"}, or None when aborted."""
    pin = prepare_input(positions, layers, settings, centered)
    prog = staged_progress(on_progress)
    try:
        sub = stage_subdivide(pin, prog, should_abort)
        disp = stage_displace(pin, sub, prog, should_abort)
        fin = stage_finish(pin, disp, "export", prog, should_abort)
    except Aborted:
        return None
    return finish_result(pin, sub, fin)
