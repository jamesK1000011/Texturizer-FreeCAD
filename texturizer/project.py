# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# The .bumpmesh project format (zip layout, settings.json snapshot, layer
# records, paint.json / mask.json, model.stl in its original pose with
# poseRotation, texture.png / texture-<i>.png), the persisted and per-layer
# key lists, the legacy scale migration and the load rules follow BumpMesh
# (stlTexturizer) by Stefan Hermann (CNC Kitchen), AGPL-3.0,
# github.com/CNCKitchen/stlTexturizer (js/main.js: getSettingsSnapshot, the
# Save Project handler, importProject, _importLayers, _layerSettingsFrom,
# _migrateSnapshotScaleToMm; js/paintTree.js: serialize / toJSON).
"""Save Project / Load Project in BumpMesh's own .bumpmesh format.

A .bumpmesh is a ZIP:

    settings.json     required: {version: 1, the active layer's settings and the
                      globals flattened (PERSISTED_KEYS), scaleUnit "mm",
                      activeMapName, layers: [{id, settings, activeMapName,
                      includeOnly, visible, blendAdd, texture?}], activeLayer,
                      paint?, poseRotation?}
    model.stl         optional (Save option "Model (STL)"), binary, original pose
    paint.json        with the model: every layer's paint (a PaintTree)
    mask.json         with the model: the active layer's paint as a face list
    texture.png       optional (Save option "Custom texture"): the active
                      layer's custom map (else the last custom one)
    texture-<i>.png   per layer using a custom map

Texturizer's extras (layer names, solo, B-rep face masks, the part and the
frame) ride in settings.json under "texturizer", which BumpMesh ignores.

    save(path, draft, ...)          write a project (options as BumpMesh's dialog)
    build(draft, ...) -> {name: bytes}
    load(path, draft, mode="all")   Load Project: "all" (Model + settings) or
                                    "settings" (Settings only)
    read(path) -> Project           the parsed zip, for the dialog (has_model)

Masks map to the draft as triangle lists: a draft mask's triangles index the
model's triangles (the order of model.stl), and the B-rep faces it names are
expanded through face_triangles when saving. BumpMesh's sub-triangle paint
(the circle brush) loads as the base triangles at least half painted.

zipfile only; no Qt or FreeCAD import.
"""
import base64
import copy
import io
import json
import math
import struct
import zipfile

import numpy as np

from . import controls, uvmath

PROJECT_VERSION = 1
PROJECT_MAX_IMPORT = 500 * 1024 * 1024  # BumpMesh's cap on imports
EXT = ".bumpmesh"
SAVE_OPTIONS = (("settings", "Settings", True), ("model", "Model (STL)", True), ("texture", "Custom texture", True))
LOAD_MODES = (("all", "Model + settings"), ("settings", "Settings only"))
LOAD_ALL, LOAD_SETTINGS = "all", "settings"
MODE_SURFACE_NORMAL, MODE_TRIPLANAR = 7, 5

# main.js LAYER_KEYS and PERSISTED_KEYS, in BumpMesh's order.
LAYER_KEYS = (
    "mappingMode", "scaleU", "scaleV", "lockScale",
    "offsetU", "offsetV", "rotation", "rotateAroundZ",
    "amplitude", "textureHeight", "invertDisplacement", "invertTexture", "textureSmoothing",
    "symmetricDisplacement", "mappingBlend", "seamBandWidth", "capAngle",
    "boundaryFalloff", "boundaryFalloffCurve",
    "snapSeamlessWrap", "cylinderCenterX", "cylinderCenterY", "cylinderRadius",
    "stampOnce", "stampCenterX", "stampCenterY", "stampCenterZ", "stampSide",
    "stampNormalX", "stampNormalY", "stampNormalZ",
)
PERSISTED_KEYS = (
    "mappingMode", "scaleU", "scaleV", "lockScale",
    "offsetU", "offsetV", "rotation", "rotateAroundZ",
    "amplitude", "textureHeight", "invertDisplacement",
    "invertTexture",
    "symmetricDisplacement", "noDownwardZ", "smoothBottom", "harvestFlatFaces", "harvestTol", "preserveUntextured",
    "textureSmoothing",
    "mappingBlend", "seamBandWidth", "capAngle", "boundaryFalloff", "boundaryFalloffCurve",
    "bottomAngleLimit", "topAngleLimit",
    "refineLength", "maxTriangles",
    "snapSeamlessWrap", "cylinderCenterX", "cylinderCenterY", "cylinderRadius",
    "cylinderPanelMinimized",
    "stampOnce", "stampCenterX", "stampCenterY", "stampCenterZ", "stampSide",
    "stampNormalX", "stampNormalY", "stampNormalZ",
)
GLOBAL_KEYS = tuple(k for k in PERSISTED_KEYS if k not in LAYER_KEYS)
STAMP_KEYS = ("stampCenterX", "stampCenterY", "stampCenterZ", "stampSide", "stampNormalX", "stampNormalY", "stampNormalZ")
# Global keys BumpMesh saves that the draft does not model, with BumpMesh's defaults.
GLOBAL_EXTRA_DEFAULTS = {"cylinderPanelMinimized": False}


class Project:
    """A read .bumpmesh: files {name: bytes} and data (settings.json or None)."""

    def __init__(self, files):
        self.files = files
        raw = files.get("settings.json")
        self.data = json.loads(raw.decode("utf-8")) if raw else None

    @property
    def has_model(self):
        return "model.stl" in self.files

    @property
    def layered(self):
        return bool(self.data and isinstance(self.data.get("layers"), list) and self.data["layers"])


# -- STL ---------------------------------------------------------------------------
def stl_bytes(triangles):
    """Binary STL (80-byte header, uint32 count, 50 bytes a triangle) of
    (N, 3, 3) triangles, with face normals."""
    t = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 3)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    ln = np.linalg.norm(n, axis=1)
    n = n / np.where(ln > 0, ln, 1.0)[:, None]
    rec = np.zeros(len(t), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = n, t
    return b"Texturizer for FreeCAD (BumpMesh project model)".ljust(80, b" ") + struct.pack("<I", len(t)) + rec.tobytes()


def parse_stl(data):
    """(N, 3, 3) float32 triangles of a binary or ASCII STL."""
    if len(data) >= 84:
        n = struct.unpack_from("<I", data, 80)[0]
        if 84 + 50 * n == len(data):
            rec = np.frombuffer(data, dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")], count=n, offset=84)
            return rec["v"].copy()
    import re
    num = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
    vs = re.findall(r"vertex\s+(%s)\s+(%s)\s+(%s)" % (num, num, num), data.decode("latin-1"))
    return np.asarray(vs, dtype=np.float32).reshape(-1, 3, 3)


# -- pose ----------------------------------------------------------------------------
def euler_to_quat(deg):
    """three.js Quaternion.setFromEuler, order XYZ, degrees -> [x, y, z, w]."""
    x, y, z = (math.radians(float(a)) / 2 for a in deg)
    c1, c2, c3, s1, s2, s3 = math.cos(x), math.cos(y), math.cos(z), math.sin(x), math.sin(y), math.sin(z)
    return [s1 * c2 * c3 + c1 * s2 * s3, c1 * s2 * c3 - s1 * c2 * s3,
            c1 * c2 * s3 + s1 * s2 * c3, c1 * c2 * c3 - s1 * s2 * s3]


def quat_to_euler(q):
    """three.js Euler.setFromQuaternion, order XYZ -> degrees [x, y, z]."""
    x, y, z, w = (float(a) for a in q)
    ln = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / ln, y / ln, z / ln, w / ln
    m11, m12, m13 = 1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)
    m22, m23 = 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)
    m32, m33 = 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)
    ry = math.asin(max(-1.0, min(1.0, m13)))
    if abs(m13) < 0.9999999:
        rx, rz = math.atan2(-m23, m33), math.atan2(-m12, m11)
    else:
        rx, rz = math.atan2(m32, m22), 0.0
    return [math.degrees(rx), math.degrees(ry), math.degrees(rz)]


# -- paint -----------------------------------------------------------------------------
def _b64(u8):
    return base64.b64encode(bytes(u8)).decode("ascii")


def paint_json(tri_count, layer_ids, painted, vert_count=None):
    """paint.json (PaintTree.toJSON of serialize({leafCov: true})) for whole
    base triangles: no splits (every code 0), one state stream per layer,
    no soft coverage. leafCov (all null) marks it weld-independent, so
    BumpMesh replays it onto its own weld of model.stl."""
    states = []
    for lid in layer_ids:
        s = np.zeros(tri_count, dtype=np.uint8)
        idx = [t for t in painted.get(lid, ()) if 0 <= t < tri_count]
        s[idx] = 1
        states.append(_b64(s))
    return {"version": 1, "baseTriCount": tri_count, "baseVertCount": vert_count if vert_count is not None else tri_count * 3,
            "nodeCount": tri_count, "vertCount": vert_count if vert_count is not None else tri_count * 3,
            "codes": _b64(np.zeros(tri_count, dtype=np.uint8)), "layerIds": list(layer_ids),
            "states": states, "cov": [None] * len(layer_ids), "leafCov": [None] * len(layer_ids)}


def decode_paint(j):
    """{layer id: sorted base triangles} of a paint.json: a base triangle
    counts when at least half its leaves are painted (hard state, or a soft
    leaf whose mean coverage reaches 0.5). None when the version is unknown."""
    if not j or j.get("version") != 1:
        return None
    codes = np.frombuffer(base64.b64decode(j["codes"]), dtype=np.uint8)
    states = [np.frombuffer(base64.b64decode(s), dtype=np.uint8) for s in j["states"]]
    soft = []
    for lc in j.get("leafCov") or [None] * len(states):
        if not lc:
            soft.append({})
            continue
        idx = np.frombuffer(base64.b64decode(lc["idx"]), dtype="<i4")
        val = np.frombuffer(base64.b64decode(lc["val"]), dtype="<f4").reshape(-1, 3)
        soft.append({int(i): float(v.mean()) for i, v in zip(idx, val)})
    n_base = int(j["baseTriCount"])
    out = {lid: [] for lid in j["layerIds"]}
    pos = 0
    for f in range(n_base):
        leaves, stack = [], [1]   # nodes still to read in this root's depth-first stream
        while stack:
            stack[-1] -= 1
            if stack[-1] == 0:
                stack.pop()
            code = int(codes[pos])
            mask = code & 7
            if mask == 0:
                leaves.append(pos)
            else:
                stack.append(1 + bin(mask).count("1"))
            pos += 1
        for li, lid in enumerate(j["layerIds"]):
            st, sc = states[li], soft[li]
            hit = sum(1 for p in leaves if st[p] or sc.get(p, 0.0) >= 0.5)
            if leaves and hit * 2 >= len(leaves):
                out[lid].append(f)
    return out


def _vert_count(triangles):
    if triangles is None:
        return None
    v = np.asarray(triangles, dtype=np.float32).reshape(-1, 3)
    return int(len(np.unique(v, axis=0)))


# -- draft -> settings.json ------------------------------------------------------------
def _layer_snapshot(L):
    """A draft layer's settings as BumpMesh's layer record settings."""
    s = L["settings"]
    out = {}
    h = float(s.get("amplitude", 0.5))
    for k in LAYER_KEYS:
        if k == "textureHeight":
            out[k] = h
        elif k == "amplitude":
            out[k] = (-1 if s.get("invertDisplacement") else 1) * h
        else:
            out[k] = s.get(k)
    out["stampOnce"] = bool(out.get("stampOnce"))
    out["scaleUnit"] = "mm"
    return out


def _texture_of(L):
    return L.get("texture") or None


def snapshot(draft, extras=None):
    """settings.json's payload for the draft (getSettingsSnapshot plus
    version), without the per-file references the Save adds."""
    A = draft.layer()
    active = _layer_snapshot(A)
    payload = {"version": PROJECT_VERSION}
    for k in PERSISTED_KEYS:
        if k in active:
            payload[k] = active[k]
        elif k in draft.settings:
            payload[k] = draft.settings[k]
        else:
            payload[k] = (extras or {}).get(k, GLOBAL_EXTRA_DEFAULTS.get(k))
    payload["scaleUnit"] = "mm"
    payload["activeMapName"] = _texture_of(A)
    payload["layers"] = [{"id": L["id"], "settings": _layer_snapshot(L), "activeMapName": _texture_of(L),
                          "includeOnly": bool(L.get("includeOnly")), "visible": bool(L.get("visible", True)),
                          "blendAdd": bool(L.get("blendAdd"))} for L in draft.layers]
    payload["activeLayer"] = draft.active
    payload["texturizer"] = {"version": 1, "part": draft.part, "frame": copy.deepcopy(draft.frame),
                             "layers": [{"name": L.get("name"), "solo": bool(L.get("solo")),
                                         "faces": list(L["mask"]["faces"])} for L in draft.layers]}
    return payload


def build(draft, model=None, include_model=True, include_texture=True, texture_png=None, face_triangles=None):
    """{zip name: bytes} for the draft, as BumpMesh's Save Project with its
    three options: Settings (always), Model (STL) when include_model and a
    model ((N, 3, 3) triangles in the document's pose; the frame rotation goes
    in poseRotation), Custom texture when include_texture and texture_png(id)
    gives PNG bytes for a layer's texture (None for a library texture).
    face_triangles(face) -> triangle indices expands B-rep face masks."""
    payload = snapshot(draft)
    files = {}
    tex = {}
    for i, L in enumerate(draft.layers):
        png = texture_png(_texture_of(L)) if (texture_png and _texture_of(L)) else None
        if png:
            tex[i] = png
    include_model = include_model and model is not None
    include_texture = include_texture and bool(tex)
    if include_model:
        tris = np.asarray(model, dtype=np.float64).reshape(-1, 3, 3)
        files["model.stl"] = stl_bytes(tris)
        rot = draft.frame.get("rotation") or [0, 0, 0]
        q = euler_to_quat(rot)
        if abs(q[3]) < 1 - 1e-12:
            payload["poseRotation"] = q
        painted = {}
        for L in draft.layers:
            t = set(int(x) for x in L["mask"]["triangles"])
            for f in L["mask"]["faces"]:
                t.update(int(x) for x in (face_triangles(f) if face_triangles else ()))
            painted[L["id"]] = sorted(t)
        # BumpMesh writes the paint whenever the model goes in; mask.json is the
        # active layer's paint as a face list, for older readers.
        n = len(tris)
        files["paint.json"] = json.dumps(paint_json(n, [L["id"] for L in draft.layers], painted,
                                                    _vert_count(tris))).encode("utf-8")
        payload["paint"] = "paint.json"
        A = draft.layer()
        exc = [t for t in painted[A["id"]] if t < n]
        if exc or A.get("includeOnly"):
            files["mask.json"] = json.dumps({"selectionMode": bool(A.get("includeOnly")), "excluded": exc}).encode("utf-8")
    if include_texture:
        a = draft.active
        src = a if a in tex else min(tex)
        files["texture.png"] = tex[src]
        payload["activeMapName"] = _texture_of(draft.layers[src])
        for i, png in tex.items():
            name = "texture-%d.png" % i
            files[name] = png
            payload["layers"][i]["texture"] = name
    files["settings.json"] = json.dumps(payload, indent=2).encode("utf-8")
    return files


def save(path, draft, **kw):
    """Write build(draft, **kw) to path (or a binary file object); returns
    the names written."""
    files = build(draft, **kw)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return list(files)


def default_name(part_label=None):
    """BumpMesh names the download after the model: <name>.bumpmesh."""
    return (part_label or "bumpmesh") + EXT


# -- settings.json -> draft ------------------------------------------------------------
def read(src):
    """A Project from a path, bytes or a binary file object (500 MB cap)."""
    if isinstance(src, (bytes, bytearray)):
        data = bytes(src)
    elif hasattr(src, "read"):
        data = src.read()
    else:
        import os
        if os.path.getsize(src) > PROJECT_MAX_IMPORT:
            raise ValueError("File too large (%.1f MB, max 500 MB)" % (os.path.getsize(src) / 1024 / 1024))
        with open(src, "rb") as fh:
            data = fh.read()
    if len(data) > PROJECT_MAX_IMPORT:
        raise ValueError("File too large (%.1f MB, max 500 MB)" % (len(data) / 1024 / 1024))
    if data[:4] != b"PK\x03\x04":
        raise ValueError("not a .bumpmesh project (not a ZIP); load a model file with Load Model…")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return Project({n: z.read(n) for n in z.namelist()})


def _migrate_scale(snap, bounds):
    """_migrateSnapshotScaleToMm: legacy relative scale fractions to mm."""
    if not snap or snap.get("scaleUnit") == "mm":
        return snap
    if snap.get("scaleU") is None and snap.get("scaleV") is None:
        return snap
    out = dict(snap, scaleUnit="mm")
    b = bounds or {"min": (0, 0, 0), "size": (50, 50, 50)}
    mode = out.get("mappingMode")
    mode = MODE_TRIPLANAR if mode is None else mode
    ref_u, ref_v = uvmath.scale_reference_lengths(mode, {"cylinderRadius": out.get("cylinderRadius")}, b)
    angular = mode in (3, 4)
    legacy = None
    if not angular and out.get("fixedWorldTextureScale") and float(out.get("referenceExtentMm") or 0) > 0:
        legacy = float(out["referenceExtentMm"])
    ru, rv = (legacy or ref_u), (legacy or ref_v)
    if out.get("scaleU") is not None:
        out["scaleU"] = float("%.4g" % (out["scaleU"] * ru))
    if out.get("scaleV") is not None:
        out["scaleV"] = float("%.4g" % (out["scaleV"] * rv))
    out.pop("fixedWorldTextureScale", None)
    out.pop("referenceExtentMm", None)
    return out


def _layer_settings_from(saved, bounds):
    """_layerSettingsFrom onto the draft's defaults: the draft layer's settings."""
    from .draft import layer_defaults

    s = layer_defaults()
    saved = saved if isinstance(saved, dict) else {}
    if saved.get("scaleUnit") != "mm":
        saved = _migrate_scale(dict(saved), bounds)
    for k in LAYER_KEYS:
        if k not in saved:
            continue
        v = saved[k]
        if k in ("amplitude", "textureHeight"):
            continue
        if k in s:
            s[k] = v
    if "textureHeight" in saved and saved["textureHeight"] is not None:
        s["amplitude"] = float(saved["textureHeight"])
    elif saved.get("amplitude") is not None:
        s["amplitude"] = abs(float(saved["amplitude"]))
    if "stampOnce" not in saved:   # layers saved before stamps existed tile
        s["stampOnce"] = False
        s.update({k: None for k in STAMP_KEYS})
    if s.get("mappingMode") == MODE_SURFACE_NORMAL and not s.get("stampOnce"):
        s["mappingMode"] = MODE_TRIPLANAR   # Surface normal is for stamps only
    return s


def _bounds_of(tris):
    if tris is None or not len(tris):
        return None
    return uvmath.bounds_of(np.asarray(tris, dtype=float).reshape(-1, 3))


def load(src, draft, mode=LOAD_ALL, import_texture=None, bounds=None):
    """Load Project into draft (one undo step). mode "all" (Model + settings)
    takes the project's model, masks and pose; "settings" (Settings only)
    keeps the draft's part and frame, and, as BumpMesh, its masks only for a
    project from before layers (layered settings bring their own layers).
    A project without a model always loads as "settings".

    import_texture(name, png_bytes) -> texture id adds a bundled custom map
    to the library (default: the map's name is used as the id).
    bounds (uvmath style) converts legacy relative scales; the model's or
    a 50 mm cube's by default, as BumpMesh.

    Returns {"mode", "model": (N, 3, 3) float32 triangles or None,
    "textures": {layer: (name, png bytes)}, "warnings": [..]}.
    The caller puts the model in the document (and sets draft.part)."""
    proj = src if isinstance(src, Project) else read(src)
    data = proj.data or {}
    if mode not in (LOAD_ALL, LOAD_SETTINGS):
        raise ValueError("mode is 'all' or 'settings'")
    if not proj.has_model:
        mode = LOAD_SETTINGS
    warnings = []
    model = parse_stl(proj.files["model.stl"]) if mode == LOAD_ALL else None
    if bounds is None:
        bounds = _bounds_of(model)   # None: BumpMesh's 50 mm cube
    top = _migrate_scale(dict(data), bounds) if data else {}
    tz = data.get("texturizer") if isinstance(data.get("texturizer"), dict) else {}
    tz_layers = tz.get("layers") if isinstance(tz.get("layers"), list) else []

    new = draft.to_dict()
    g = dict(new["settings"])
    for k in GLOBAL_KEYS:
        if k in top and k in g:
            g[k] = top[k]
    new["settings"] = g
    textures = {}

    def texture_for(i, d, png_name):
        png = proj.files.get(png_name) if png_name else None
        if png:
            name = d.get("activeMapName") or png_name
            textures[i] = (name, png)
            return import_texture(name, png) if import_texture else name
        return d.get("activeMapName") or controls.DEFAULT_TEXTURE

    layers = []
    if proj.layered:
        files = data["layers"][:controls.MAX_LAYERS]
        for i, d in enumerate(files):
            d = d if isinstance(d, dict) else {}
            s = _layer_settings_from(d.get("settings"), bounds)
            x = tz_layers[i] if i < len(tz_layers) and isinstance(tz_layers[i], dict) else {}
            L = {"id": int(d["id"]) if isinstance(d.get("id"), (int, float)) else i + 1,
                 "texture": texture_for(i, d, d.get("texture")), "visible": d.get("visible") is not False,
                 "blendAdd": bool(d.get("blendAdd")), "includeOnly": bool(d.get("includeOnly")), "settings": s,
                 "solo": bool(x.get("solo")), "mask": {"faces": [], "triangles": []}}
            if x.get("name"):
                L["name"] = x["name"]
            if mode == LOAD_ALL:
                L["mask"]["faces"] = [f for f in x.get("faces", []) if isinstance(f, str)]
            layers.append(L)
        active = max(0, min(len(layers) - 1, int(data.get("activeLayer") or 0)))
        if mode == LOAD_ALL:
            n = len(model)
            restored = False
            if data.get("paint") and data["paint"] in proj.files:
                try:
                    pj = json.loads(proj.files[data["paint"]].decode("utf-8"))
                    by_id = decode_paint(pj)
                    if by_id is not None:
                        if int(pj.get("baseTriCount", -1)) != n:
                            warnings.append("The saved paint does not match the loaded model")
                        else:
                            ids = [d.get("id") for d in files]
                            for i, L in enumerate(layers):
                                L["mask"]["triangles"] = sorted(by_id.get(ids[i], []))
                            restored = True
                except (ValueError, KeyError, TypeError) as e:
                    warnings.append("Could not restore the paint: %s" % e)
            if not restored:   # pre-paint-tree layered projects: one face list per layer
                for i, d in enumerate(files):
                    m = _json_file(proj, d.get("mask"), warnings)
                    if isinstance(m, dict):
                        layers[i]["includeOnly"] = bool(m.get("selectionMode"))
                        layers[i]["mask"]["triangles"] = _face_list(m, n)
    else:
        # A project from before layers: one layer, described by the top level.
        s = _layer_settings_from(top, bounds)
        cur = draft.layer()
        L = {"id": cur["id"] if mode == LOAD_SETTINGS else 1, "texture": None, "visible": True, "blendAdd": False,
             "includeOnly": cur["includeOnly"] if mode == LOAD_SETTINGS else False, "settings": s, "solo": False,
             "mask": copy.deepcopy(cur["mask"]) if mode == LOAD_SETTINGS else {"faces": [], "triangles": []}}
        if proj.files.get("texture.png"):
            name = data.get("activeMapName") or "imported-texture.png"
            textures[0] = (name, proj.files["texture.png"])
            L["texture"] = import_texture(name, proj.files["texture.png"]) if import_texture else name
        else:
            L["texture"] = data.get("activeMapName") or cur["texture"]
        if mode == LOAD_ALL:
            m = _json_file(proj, "mask.json", warnings)
            if isinstance(m, dict):
                L["includeOnly"] = bool(m.get("selectionMode"))
                L["mask"]["triangles"] = _face_list(m, len(model))
        layers, active = [L], 0

    new["layers"] = layers
    new["active"] = active
    if mode == LOAD_ALL:
        fr = tz.get("frame") if isinstance(tz.get("frame"), dict) else None
        if fr and isinstance(fr.get("rotation"), list):
            new["frame"] = {"rotation": [float(a) for a in fr["rotation"]], "face": fr.get("face")}
        elif isinstance(data.get("poseRotation"), list) and len(data["poseRotation"]) == 4:
            new["frame"] = {"rotation": quat_to_euler(data["poseRotation"]), "face": None}
        else:
            new["frame"] = {"rotation": [0.0, 0.0, 0.0], "face": None}
    draft.load(new, undoable=True)
    return {"mode": mode, "model": model, "textures": textures, "warnings": warnings}


def _json_file(proj, name, warnings):
    if not name or name not in proj.files:
        return None
    try:
        return json.loads(proj.files[name].decode("utf-8"))
    except ValueError as e:
        warnings.append("Could not restore a mask: %s" % e)
        return None


def _face_list(m, n):
    """_importLegacyMask's face list (soft corners are not kept: the draft
    has no soft coverage)."""
    ex = m.get("excluded") if isinstance(m.get("excluded"), list) else []
    return sorted(set(int(i) for i in ex if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < n))
