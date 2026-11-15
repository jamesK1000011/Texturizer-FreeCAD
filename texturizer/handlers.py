# SPDX-License-Identifier: AGPL-3.0-only
# Texturizer's tool handlers: {tool_name: fn(ctx, args)} for texture_list,
# texture_settings, texture_apply (BumpMesh's engine, texturizer.engine, a
# port of BumpMesh by Stefan Hermann, CNC Kitchen, AGPL-3.0), texture_prepare
# and texture_check (texturizer.mesh). ctx is the session the call runs in (Agent Link's,
# or Context below when Texturizer runs alone): its body(ref) and out_dir.
# No Agent Link import here: the family layer (fam_texture.py, loaded by
# Agent Link only) binds them with bound() and lends Agent Link's ToolError
# and its face queries with lend(); alone, faces are face names (the
# commands take them from the current selection). This is the one place an
# answer is shaped.
import functools
import math
import os

from . import mesh


class Refusal(Exception):
    """A request refused, in the shape of Agent Link's ToolError (code,
    message), used when no host lends one."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


ToolError = Refusal  # lent by the family layer


def num(v, name):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ToolError("invalid_input", f"{name}: a number.")
    return float(v)


def face_names(shape, q):
    """The face names q lists (a name, a list of names, or {"faces": [names]}),
    each on the shape; queries (by normal, area, ...) come with Agent Link."""
    items = q if isinstance(q, list) else [q]
    names = set()
    for item in items:
        if isinstance(item, dict) and set(item) == {"faces"}:
            item = item["faces"]
        for n in item if isinstance(item, list) else [item]:
            if not isinstance(n, str) or not n.startswith("Face"):
                raise ToolError("invalid_input", "faces: face names (Face1, Face2, …); select the faces, or install Agent Link for face queries.")
            names.add(n)
    have = {f"Face{i + 1}" for i in range(len(shape.Faces))}
    missing = sorted(names - have)
    if missing:
        raise ToolError("not_found", f"faces: {', '.join(missing)} not on this body (it has Face1 to Face{len(have)}).")
    return names


pick_faces = face_names  # lent by the family layer (Agent Link's face queries)

# The last answers, whoever asked (the agent, a command, the panel): read by
# the Texture panel. Newest first; a few prepared files kept.
LAST = {"prepared": [], "check": None, "applied": None}
KEEP = 5


def remember(tool, out):
    if tool == "texture_prepare":
        LAST["prepared"] = [out] + [p for p in LAST["prepared"] if p.get("stl") != out.get("stl")][: KEEP - 1]
    elif tool == "texture_check":
        LAST["check"] = out
    elif tool == "texture_apply":
        LAST["applied"] = out
    return out


def texture_prepare(s, a):
    o = s.body(a.get("body"))
    want = pick_faces(o.Shape, a.get("faces"))
    tol = num(a.get("tolerance", 0.02), "tolerance")
    stem = (a.get("name") or o.Label).replace(" ", "_")
    stl = a.get("path") or os.path.join(s.out_dir, stem + "-for-texture.stl")
    out = mesh.prepare(o.Shape, want, a.get("mode", "include"), tol, stl, o.Label)
    mode = out["mode"]
    out_path = os.path.splitext(stl)[0].replace("-for-texture", "") + "-textured.stl"
    bb = o.Shape.BoundBox
    return remember("texture_prepare", {
        "body": o.Label, **out,
        "next": {"tool": "bumpmesh_texture_file", "arguments": {"path": stl, "output": out_path, "layers": [{"texture": "a library name (bumpmesh_list_textures)", "settings": {"textureHeight": 0.5, "scaleU": 5},
                 "mask": {"mode": mode, "trianglesFile": out["mask"]}}], "settings": {"refineLength": round(max(0.05, min(5, bb.DiagonalLength / 250)), 2)}}},
        "note": f"{'Only' if mode == 'include' else 'All but'} {', '.join(out['faces'])} get the texture; say the faces that must not change back to the person first. Then texture_check {{ body: \"{o.Label}\", path: <the output> }}: the depth on each textured face, any face it leaked onto, and whether the mesh is closed.",
    })


def texture_check(s, a):
    o = s.body(a.get("body"))
    path = a.get("path")
    mask = a.get("mask") or (os.path.splitext(path)[0].replace("-textured", "") + "-for-texture-mask.json" if path else "")
    return remember("texture_check", mesh.check(o.Shape, path, mask, num(a.get("tolerance", 0.02), "tolerance"), a.get("reach"), num(a.get("flat", 0.01), "flat"), o.Label))


# ── BumpMesh's engine ──────────────────────────────────────────────────────

LIST_KEYS = ("id", "name", "category", "credit", "licence", "source_url", "defaults")


def texture_list(s, a):
    from . import engine

    cat = a.get("category")
    q = str(a.get("search") or "").lower()
    out = []
    for e in engine.library.list():
        if cat and e["category"] != cat:
            continue
        if q and q not in e["name"].lower():
            continue
        out.append({k: e[k] for k in LIST_KEYS})
    if cat and not out and cat not in engine.library.CATEGORIES + [engine.library.CUSTOM]:
        raise ToolError("invalid_input", f"category: one of {', '.join(engine.library.CATEGORIES + [engine.library.CUSTOM])}.")
    return {"count": len(out), "textures": out, "categories": engine.library.CATEGORIES,
            "credit": "BumpMesh's texture library (Stefan Hermann, CNC Kitchen); Hero Patterns by Steve Schoger (CC BY 4.0); "
                      "ambientCG and Poly Haven (CC0). Filter Forge textures are not included.",
            "note": "Use an id as texture in texture_apply; defaults.scaleU is the tile in mm on a 50 mm part "
                    "(BumpMesh scales it with the part: tileFraction x the part's largest size)."}


def _numbers(d, where):
    from . import engine

    known = set(engine.LAYER_KEYS) | set(engine.GLOBAL_KEYS)
    out = {}
    for k, v in (d or {}).items():
        if k not in known:
            raise ToolError("invalid_input", f"{where}.{k}: not a BumpMesh setting (texture_settings lists them).")
        if v is not None and not isinstance(v, (bool, str)) and not (isinstance(v, (int, float)) and math.isfinite(v)):
            raise ToolError("invalid_input", f"{where}.{k}: a number, true/false or a name.")
        out[k] = v
    mode = out.get("mappingMode")
    if mode is not None:
        if isinstance(mode, str):
            if mode not in engine.MAPPING_MODES:
                raise ToolError("invalid_input", f"{where}.mappingMode: 0-8 or one of {', '.join(engine.MAPPING_MODES)}.")
            out["mappingMode"] = engine.MAPPING_MODES[mode]
        elif mode not in range(9):
            raise ToolError("invalid_input", f"{where}.mappingMode: 0-8 (texture_settings lists them).")
    return out


def texture_settings(s, a):
    from . import engine

    modes = {v: k for k, v in engine.MAPPING_MODES.items()}
    out = {"defaults": dict(engine.DEFAULTS), "layerKeys": list(engine.LAYER_KEYS), "globalKeys": list(engine.GLOBAL_KEYS),
           "mappingModes": modes, "maxLayers": engine.MAX_LAYERS,
           "units": {"scaleU": "mm per tile", "scaleV": "mm per tile", "textureHeight": "mm", "refineLength": "mm (the edge the mesh is refined to)",
                     "rotation": "degrees", "capAngle": "degrees", "bottomAngleLimit": "degrees", "topAngleLimit": "degrees",
                     "boundaryFalloff": "mm", "harvestTol": "mm"},
           "credit": engine.CREDIT}
    if a.get("body") is not None or a.get("texture") is not None:
        o = s.body(a.get("body"))
        tris, _ = mesh.face_triangles(o.Shape, num(a.get("tolerance", 0.02), "tolerance"))
        settings = _numbers(a.get("settings"), "settings")
        if a.get("texture"):
            settings["texture"] = _texture_id(a["texture"])
        pos = _positions(tris)
        est = engine.estimate(pos, settings)
        sug = engine.smart_resolution(pos, settings) if settings.get("texture") else None
        bb = o.Shape.BoundBox
        out["body"] = {"label": o.Label, "size": [round(bb.XLength, 3), round(bb.YLength, 3), round(bb.ZLength, 3)],
                       "estimate": est,
                       "suggest": None if not sug else {"refineLength": sug["edge"], "maxTriangles": sug["maxTriangles"],
                                                          "pixelsPerEdge": sug["diagnostics"]["pixelsPerEdge"]},
                       "cylinder": engine.fit_cylinder(pos, settings)}
    return out


def _texture_id(t):
    from . import engine

    try:
        return engine.library.get(t)["id"]
    except KeyError:
        raise ToolError("not_found", f"texture {t!r}: not in the library (texture_list).") from None


def _positions(tris):
    import numpy as np

    return np.asarray(tris, np.float32).reshape(-1)


def _mask_from(shape, per_face, n, q, mode):
    from . import engine

    if mode not in ("include", "exclude"):
        raise ToolError("invalid_input", "mode: include (texture only these faces) or exclude (all but them).")
    names = pick_faces(shape, q)
    tris = []
    for nm in sorted(names):
        a0, a1 = per_face.get(nm, (0, 0))
        tris.extend(range(a0, a1))
    return engine.from_triangles(n, tris, mode), sorted(names)


def texture_apply(s, a):
    """BumpMesh's engine on a body: a new Mesh feature (and an STL / 3MF when asked)."""
    from . import engine

    o = s.body(a.get("body"))
    tol = num(a.get("tolerance", 0.02), "tolerance")
    tris, per_face = mesh.face_triangles(o.Shape, tol)
    pos = _positions(tris)
    n = len(pos) // 9
    settings = _numbers(a.get("settings"), "settings")
    specs = a.get("layers")
    if specs is None:
        if not a.get("texture"):
            raise ToolError("invalid_input", "texture: a library id (texture_list), or layers.")
        specs = [{"texture": a["texture"], "settings": {}, "faces": a.get("faces"), "mode": a.get("mode", "exclude")}]
    if not isinstance(specs, list) or not specs:
        raise ToolError("invalid_input", "layers: a list of 1 to 4 layers, bottom first.")
    if len(specs) > engine.MAX_LAYERS:
        raise ToolError("invalid_input", "layers: at most 4 (BumpMesh's limit).")
    layers, masked = [], []
    for i, L in enumerate(specs):
        if not isinstance(L, dict) or not L.get("texture"):
            raise ToolError("invalid_input", f"layers.{i}.texture: a library id (texture_list).")
        mask, names = None, []
        if L.get("faces") is not None:
            mask, names = _mask_from(o.Shape, per_face, n, L["faces"], L.get("mode", "exclude"))
        elif L.get("mode") == "include":
            raise ToolError("invalid_input", f"layers.{i}.faces: include needs the faces to texture.")
        masked.append({"layer": i + 1, "mode": L.get("mode", "exclude") if names else None, "faces": names})
        ls = _numbers(L.get("settings"), f"layers.{i}.settings")
        if i == 0 and a.get("layers") is None:
            ls = {**{k: v for k, v in settings.items() if k in engine.LAYER_KEYS}, **ls}
        layers.append({"texture": _texture_id(L["texture"]), "settings": ls, "mask": mask,
                       "blendAdd": bool(L.get("blendAdd")) and i > 0, "visible": L.get("visible", True) is not False})
    glob = {k: v for k, v in settings.items() if k in engine.GLOBAL_KEYS}
    run = {**glob, "layers": layers}
    if a.get("rotation") is not None:
        run["frame"] = {"rotation": [num(x, "rotation") for x in a["rotation"]]}
    est = engine.estimate(pos, run)
    if est["memoryBytes"] > (num(a.get("memoryLimitGB", 4), "memoryLimitGB") * (1 << 30)):
        raise ToolError("invalid_state", f"refineLength {est['edge']} mm would make {est['subdividedTriangles']:,} triangles "
                        f"(~{est['memoryBytes'] / 2 ** 30:.1f} GB): raise settings.refineLength (texture_settings suggests one).")
    res = engine.texture(pos, run)
    if res is None:
        raise ToolError("failed", "Stopped before it finished.")
    name = a.get("name") or f"{o.Label}_textured"
    obj = _add_mesh(s, o, name, res["positions"], engine.credit_of(run), run)
    out = {"body": o.Label, "object": obj.Name if obj is not None else None, "label": name,
           "triangles": res["triangles"], "inputTriangles": res["inputTriangles"], "repair": res["repair"],
           "warnings": res["warnings"], "layers": masked, "credit": engine.credit_of(run)}
    exp = a.get("export")
    if exp:
        path = exp if isinstance(exp, str) else exp.get("path")
        if not path:
            raise ToolError("invalid_input", "export: a path ending .stl or .3mf.")
        if not os.path.isabs(path):
            path = os.path.join(s.out_dir, path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        try:
            out["export"] = engine.export(pos, run, None, path, None if isinstance(exp, str) else exp.get("format"), result=res)
        except ValueError as e:
            raise ToolError("invalid_input", f"export: {e}") from None
        out["export"] = {k: out["export"][k] for k in ("path", "format", "triangles")}
    out["next"] = (f"texture_check {{ body: \"{o.Label}\", path: <an STL export> }} measures the depth face by face; "
                   "slice the export or the mesh object.")
    return remember("texture_apply", out)


def _add_mesh(s, body, name, positions, credit, run):
    """The textured mesh as a Mesh::Feature beside the body, carrying its credit
    and the settings it was made with."""
    import json
    import tempfile

    from .engine import writers

    try:
        import Mesh
    except ImportError:
        return None
    doc = getattr(s, "doc", None) or body.Document
    fd, tmp = tempfile.mkstemp(suffix=".stl")
    os.close(fd)
    try:
        with open(tmp, "wb") as fh:
            fh.write(writers.stl_bytes(positions))
        m = Mesh.Mesh(tmp)
    finally:
        os.unlink(tmp)
    obj = doc.addObject("Mesh::Feature", "Textured")
    obj.Label = name
    obj.Mesh = m
    for prop, val in (("Credit", credit), ("TextureSettings", json.dumps(_jsonable(run))), ("SourceBody", body.Name)):
        if prop not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", prop, "Texturizer", "")
        setattr(obj, prop, val)
    doc.recompute()
    return obj


def _jsonable(run):
    out = {k: v for k, v in run.items() if k != "layers"}
    out["layers"] = [{"texture": L["texture"], "settings": L["settings"], "blendAdd": L["blendAdd"],
                      "maskTriangles": None if L["mask"] is None else int(len(L["mask"].triangles)),
                      "maskMode": None if L["mask"] is None else L["mask"].mode} for L in run["layers"]]
    return out


HANDLERS = {
    "texture_list": texture_list,
    "texture_settings": texture_settings,
    "texture_apply": texture_apply,
    "texture_prepare": texture_prepare,
    "texture_check": texture_check,
}


def bound(ctx, handlers=None):
    """{tool_name: fn(args)} for one context: handlers (default all) with
    ctx given."""
    return {k: functools.partial(fn, ctx) for k, fn in (HANDLERS if handlers is None else handlers).items()}


def lend(**names):
    """Set what the host lends (ToolError=..., pick_faces=...) in the slots
    this module has."""
    g = globals()
    for k, v in names.items():
        if k not in g:
            raise AttributeError(f"{__name__} has no slot {k!r} to lend")
        g[k] = v


class Context:
    """The session a call runs in when Texturizer runs alone: the active
    document's bodies, files written to ~/Texturizer (or out_dir)."""

    def __init__(self, doc=None, out_dir=None):
        self._doc = doc
        self.out_dir = out_dir or os.path.expanduser("~/Texturizer")

    @property
    def doc(self):
        if self._doc is not None:
            return self._doc
        import FreeCAD

        return FreeCAD.ActiveDocument

    def bodies(self):
        doc = self.doc
        objs = doc.Objects if doc is not None else []
        return [o for o in objs if getattr(getattr(o, "Shape", None), "Solids", None) and getattr(getattr(o, "ViewObject", None), "Visibility", True)]

    def body(self, ref):
        doc = self.doc
        if ref is not None and doc is not None:
            for o in doc.Objects:
                if getattr(getattr(o, "Shape", None), "Solids", None) and (o.Label == ref or o.Name == ref or o.Label.lower() == str(ref).lower()):
                    return o
        bs = self.bodies()
        if ref is not None:
            raise ToolError("not_found", f"body {ref!r}: not a solid in this document — {', '.join(o.Label for o in bs) or 'none'}.")
        if not bs:
            raise ToolError("not_found", "No body in the document: make one first.")
        if len(bs) == 1:
            return bs[0]
        raise ToolError("invalid_input", f"body: say which — {', '.join(o.Label for o in bs)}.")


def local_runner(ctx=None):
    """runner(tool, args) -> (ok, result) with no agent: the same handler the
    family binds, in one undo transaction when the tool writes to the
    document, answered as Agent Link answers ({"error": {code, message}}
    when refused)."""
    ctx = ctx or Context()

    def run(tool, args):
        fn = HANDLERS.get(tool)
        if fn is None:
            return False, {"error": {"code": "not_found", "message": f"{tool}: not a Texturizer tool ({', '.join(sorted(HANDLERS))})."}}
        os.makedirs(ctx.out_dir, exist_ok=True)
        doc = ctx.doc
        if doc is not None and hasattr(doc, "openTransaction"):
            doc.openTransaction(f"Texturizer: {tool}")
        try:
            return True, fn(ctx, dict(args or {}))
        except Exception as e:  # noqa: BLE001 — a refusal is an answer
            if doc is not None and hasattr(doc, "abortTransaction"):
                doc.abortTransaction()
                doc = None
            if hasattr(e, "code") and hasattr(e, "message"):
                return False, {"error": {"code": e.code, "message": e.message}}
            return False, {"error": {"code": "internal", "message": f"{type(e).__name__}: {e}"}}
        finally:
            if doc is not None and hasattr(doc, "commitTransaction"):
                doc.commitTransaction()

    return run
