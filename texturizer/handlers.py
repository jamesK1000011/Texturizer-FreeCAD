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

from . import live, mesh, timing


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


def _local(s, p):
    """A path as the agent gave it: ~ expanded, a relative one in the output
    folder (where Agent Link's path check judges it, and where the other
    tools write)."""
    if not isinstance(p, str) or not p.strip():
        return p
    p = os.path.expanduser(p.strip())
    return p if os.path.isabs(p) else os.path.join(s.out_dir, p)

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
    stl = _local(s, a.get("path")) or os.path.join(s.out_dir, stem + "-for-texture.stl")
    os.makedirs(os.path.dirname(stl) or ".", exist_ok=True)
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


def stale_check(out, label, name):
    """A check of a Textured object that is out of date: its mesh is of the
    body as it was, measured against the body as it is, so the figures are
    of the change to the body and the texture alike. Never "ok" (found
    live: a block made 2 mm taller answered verdict ok, "2 mm deep at
    most", with every point 1.6 to 2 mm under its face)."""
    why = f"{label} is out of date: its body changed (texture_apply {{ update: \"{name}\" }} first)."
    out.setdefault("warnings", []).append(why)
    out["verdict"] = "check"
    out["outOfDate"] = True
    out["note"] = (f"Out of date: this is {label}'s mesh of the body as it was, measured against the body as it is now, so the figures "
                   f"mix the body's change with the texture — run texture_apply {{ update: \"{name}\" }}, then check again. "
                   "(As measured: " + str(out.get("note") or "").rstrip() + ")")
    return out


def texture_check(s, a):
    """A textured STL against its body. The mask: object (a Textured object
    texture_apply made: its stored draft or arguments, its body), mask
    (texture_prepare's -mask.json), the -mask.json beside path by name, or
    the Textured object path was exported from."""
    path = _local(s, a.get("path"))
    tol = num(a.get("tolerance", 0.02), "tolerance")
    doc = _doc(s)
    obj = None
    if a.get("mask"):
        a = dict(a, mask=_local(s, a["mask"]))
    if a.get("object") is not None:
        if a.get("mask"):
            raise ToolError("invalid_input", "object or mask, not both: a Textured object carries its own mask.")
        obj = live.find(doc, a["object"])
        if obj is None or "SourceBody" not in getattr(obj, "PropertiesList", []):
            raise ToolError("not_found", f"object {a['object']!r}: not a Textured object in this document "
                            f"(it has: {', '.join(o.Label for o in textured_objects(doc)) or 'none; texture_apply makes one'}). "
                            "For an STL textured outside FreeCAD give body, path and mask instead.")
    mask = a.get("mask")
    if obj is None and not mask:
        if not path:
            raise ToolError("invalid_input", "object: the Textured object texture_apply made (or path: its STL; "
                            "for an STL textured outside FreeCAD: body, path and texture_prepare's mask).")
        beside = os.path.splitext(path)[0].replace("-textured", "") + "-for-texture-mask.json"
        if os.path.isfile(beside):
            mask = beside
        else:
            obj = _exported_from(doc, path, a.get("body"))
            if obj is None:
                mask = beside  # refused below, naming both ways
    if obj is None:
        o = s.body(a.get("body"))
        return remember("texture_check", mesh.check(o.Shape, path, mask, tol, a.get("reach"), num(a.get("flat", 0.01), "flat"), o.Label))
    o = s.body(obj.SourceBody)
    if a.get("body") is not None and s.body(a["body"]).Name != o.Name:
        raise ToolError("invalid_input", f"body {a['body']!r}: {obj.Label} was made from {o.Label}.")
    m = _object_mask(obj, o)
    tmp = None
    if not path:
        # The object's own mesh, as texture_export writes it.
        import tempfile

        from .engine import writers

        fd, tmp = tempfile.mkstemp(suffix=".stl")
        with os.fdopen(fd, "wb") as fh:
            fh.write(writers.stl_bytes(live.mesh_positions(obj.Mesh)))
    try:
        out = mesh.check(o.Shape, path or tmp, m, tol, a.get("reach"), num(a.get("flat", 0.01), "flat"), o.Label)
    finally:
        if tmp:
            os.unlink(tmp)
    if tmp:
        out["file"] = f"{obj.Label} (its mesh)"
    out["object"] = obj.Name
    if getattr(obj, "OutOfDate", False):
        stale_check(out, obj.Label, obj.Name)
    return remember("texture_check", out)


# Where texture_export wrote each Textured object (this session): texture_check
# finds the object from its STL.
EXPORTED = {}


def _exported_from(doc, path, body=None):
    """The Textured object an STL came from: the one texture_export wrote it
    from, else the one whose label is the file's name (texture_export's
    default), else the only one of body."""
    objs = textured_objects(doc)
    name = EXPORTED.get(os.path.realpath(path))
    hit = [o for o in objs if o.Name == name]
    if not hit:
        stem = os.path.splitext(os.path.basename(path))[0]
        hit = [o for o in objs if o.Label == stem]
    if not hit and body is not None:
        hit = [o for o in objs if o.SourceBody == body or getattr(live.find(doc, o.SourceBody), "Label", None) == body]
    return hit[0] if len(hit) == 1 else None


def _object_mask(obj, body):
    """The faces a Textured object was textured on, from what it stores:
    the draft (each visible layer's faces; a painted include mask counts
    the faces it touches) or texture_apply's arguments; with the angle
    limits it ran with."""
    import json

    from . import engine

    names = [f"Face{i + 1}" for i in range(len(body.Shape.Faces))]
    textured = set()
    stored = getattr(obj, "TextureDraft", "") or ""
    args = json.loads(getattr(obj, "TextureArgs", "") or "{}")
    if stored:
        from .draft import Draft

        d = Draft.from_json(stored)
        ft = None
        for L in d.layers:
            if L.get("visible", True) is False:
                continue
            faces, tris = set(L["mask"]["faces"]), L["mask"]["triangles"]
            if L.get("includeOnly"):
                if tris:
                    ft = ft if ft is not None else live.mesh_for(body)[1]
                    hit = set(tris)
                    faces |= {f for f, ts in ft.items() if hit.intersection(ts)}
                textured |= faces
            else:
                textured |= set(names) - faces
    elif args:
        for L in _layer_specs(args, lambda q: pick_faces(body.Shape, q)):
            if L.get("visible", True) is False:
                continue
            if L.get("faces") is None:
                textured |= set(names)
                continue
            listed = pick_faces(body.Shape, L["faces"])
            textured |= set(listed) if (L.get("mode") or "exclude") == "include" else set(names) - set(listed)
    else:
        raise ToolError("invalid_state", f"{obj.Label}: made without stored settings, so its mask is not known; texture its body "
                        "again with texture_apply, or check it with body, path (its texture_export STL) and a mask.")
    run = json.loads(getattr(obj, "TextureSettings", "") or "{}")
    g = {k: run.get(k, engine.DEFAULTS[k]) for k in ("bottomAngleLimit", "topAngleLimit")}
    return {"textured": sorted(textured), "angles": {"bottom": g["bottomAngleLimit"], "top": g["topAngleLimit"]}}


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
        raise ToolError("invalid_input", f"category {cat!r}: one of {', '.join(engine.library.CATEGORIES + [engine.library.CUSTOM])} "
                        "(or leave it out and use search).")
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


# BumpMesh names the depth textureHeight (its settings and .bumpmesh projects;
# the engine signs it into amplitude with invertDisplacement); the draft's
# slider is amplitude, the unsigned height. texture_set takes either.
SET_ALIASES = {"textureHeight": "amplitude"}


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
        raise ToolError("not_found", f"texture {t!r}: not in the library; texture_list gives the ids (texture_list {{ search }} "
                        "finds one by name), or texture_set { texture: { file } } brings in your own PNG.") from None


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


LAYER_ARGS = ("texture", "settings", "faces", "mode", "blendAdd", "visible")


def _layer_specs(a, resolve):
    """texture_apply's layers, bottom first: the one texture (texture, faces,
    mode), or the layers given, each taking the top-level texture, faces and
    mode it does not give itself. A layer that gives other ones than the top
    level is refused, as is any argument a layer does not take: none is
    dropped without a word. resolve(faces) -> the face names, to compare."""
    from . import engine

    specs = a.get("layers")
    if specs is None:
        return [{"texture": a.get("texture"), "faces": a.get("faces"), "mode": a.get("mode", "exclude")}]
    if not isinstance(specs, list) or not specs:
        raise ToolError("invalid_input", "layers: a list of 1 to 4 layers, bottom first.")
    if len(specs) > engine.MAX_LAYERS:
        raise ToolError("invalid_input", "layers: at most 4 (BumpMesh's limit).")
    out = []
    for i, L in enumerate(specs):
        if not isinstance(L, dict):
            raise ToolError("invalid_input", f"layers.{i}: an object with {', '.join(LAYER_ARGS)}.")
        for k in L:
            if k in LAYER_ARGS:
                continue
            if k in engine.LAYER_KEYS or k in engine.GLOBAL_KEYS:
                raise ToolError("invalid_input", f"layers.{i}.{k}: a BumpMesh setting; give it in layers.{i}.settings "
                                "(or in settings, for every layer).")
            raise ToolError("invalid_input", f"layers.{i}.{k}: not a layer argument ({', '.join(LAYER_ARGS)}).")
        L = dict(L)
        for k in ("texture", "faces", "mode"):
            top = a.get(k)
            if top is None:
                continue
            if L.get(k) is None:
                L[k] = top
                continue
            same = {"texture": lambda x, y: _texture_id(x) == _texture_id(y),
                    "faces": lambda x, y: set(resolve(x)) == set(resolve(y)),
                    "mode": lambda x, y: x == y}[k](L[k], top)
            if not same:
                raise ToolError("invalid_input", f"layers.{i}.{k} {L[k]!r} and the top-level {k} {top!r} differ: give {k} "
                                f"once (top level for every layer that gives none, or on the layer).")
        out.append(L)
    return out


def _layers_from_args(s, a, o, per_face, n, settings):
    """texture_apply's own layers (texture, or layers with face masks). The
    layer settings in settings are every layer's defaults."""
    from . import engine

    specs = _layer_specs(a, lambda q: pick_faces(o.Shape, q))
    shared = {k: v for k, v in settings.items() if k in engine.LAYER_KEYS}
    layers, masked = [], []
    for i, L in enumerate(specs):
        if not L.get("texture"):
            raise ToolError("invalid_input", f"layers.{i}.texture: a library id (texture_list gives them), here or once at the top level.")
        mask, names = None, []
        if L.get("faces") is not None:
            mask, names = _mask_from(o.Shape, per_face, n, L["faces"], L.get("mode") or "exclude")
        elif L.get("mode") == "include":
            raise ToolError("invalid_input", f"layers.{i}.faces: mode include needs the faces to texture (names such as Face3, or a "
                            "query); leave mode out, or use exclude, to texture every face.")
        # layer: its index from 0, bottom first, as the layers were given
        # and as texture_mask, texture_set and texture_layer take it.
        masked.append({"layer": i, "mode": (L.get("mode") or "exclude") if names else None, "faces": names})
        ls = {**shared, **_numbers(L.get("settings"), f"layers.{i}.settings")}
        layers.append({"texture": _texture_id(L["texture"]), "settings": ls, "mask": mask,
                       "blendAdd": bool(L.get("blendAdd")) and i > 0, "visible": L.get("visible", True) is not False})
    return layers, masked


def _run_engine(s, a, pos, run, mode="export"):
    from . import engine

    est = engine.estimate(pos, run)
    if est["memoryBytes"] > (num(a.get("memoryLimitGB", 4), "memoryLimitGB") * (1 << 30)):
        raise ToolError("invalid_state", f"refineLength {est['edge']} mm would make {est['subdividedTriangles']:,} triangles "
                        f"(~{est['memoryBytes'] / 2 ** 30:.1f} GB, over memoryLimitGB {a.get('memoryLimitGB', 4)}): raise "
                        "settings.refineLength (texture_settings { body, texture } answers suggest.refineLength; "
                        "texture_preview { suggest: true } sets it in the draft), or texture fewer faces with a mask.")
    import json
    import time

    from . import timing

    # The live preview's worker would split the CPU with this run (live: an
    # Update after a CAD change took 26 s against 5 s, the preview of the
    # changed part subdividing beside it): it steps aside and reruns after.
    c = getattr(live.GUI, "controller", None)
    if c is not None and getattr(c, "running", None) is not None:
        c.gen += 1          # the running preview goes stale, stopping at its next check
        c._pending = True   # and runs again when it has
    marks, info = {}, {}
    t0 = time.perf_counter()

    def progress(stage, _f, i=None):
        marks.setdefault(stage, time.perf_counter())
        if i:
            info[stage] = i
    res = engine.texture(pos, run, mode=mode, progress=progress)
    t1 = time.perf_counter()
    res = engine.printable(res)
    if res is None:
        raise ToolError("failed", "The engine stopped before it finished (cancelled, or a newer run took its place): "
                        "nothing was changed; call it again.")
    stages = sorted(marks.items(), key=lambda kv: kv[1])
    ms = {st: round(((stages[k + 1][1] if k + 1 < len(stages) else t1) - t) * 1000)
          for k, (st, t) in enumerate(stages)}
    ms["printable"] = round((time.perf_counter() - t1) * 1000)
    # What the run was made of: settings that size it, triangle counts along
    # the way, and the time per stage (an Update and a fresh apply compare).
    res["engine"] = {"refineLength": res.get("refineLength", run.get("refineLength")), "maxTriangles": run.get("maxTriangles"),
                     "inputTriangles": res.get("inputTriangles"),
                     "subdivided": (info.get("subdivide2") or info.get("subdivide1") or {}).get("triCount"),
                     "displaced": (info.get("displace") or {}).get("triCount"), "output": res.get("triangles"),
                     "ms": ms, "previewYielded": c is not None and getattr(c, "_pending", False),
                     "pointsOnEdges": res.get("pointsOnEdges"), "capsFixed": res.get("capsFixed")}
    timing.report("texture_apply engine " + json.dumps(res["engine"]), (t1 - t0) * 1000)
    return res


def _export_with(s, a, pos, run, res, out):
    from . import engine

    exp = a.get("export")
    if not exp:
        return
    path = exp if isinstance(exp, str) else exp.get("path")
    if not path:
        raise ToolError("invalid_input", "export: a path ending .stl or .3mf (relative: the output folder), or {path, format}.")
    path = _local(s, path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        e = engine.export(pos, run, None, path, None if isinstance(exp, str) else exp.get("format"), result=res)
    except ValueError as err:
        raise ToolError("invalid_input", f"export: {err}") from None
    out["export"] = {k: e[k] for k in ("path", "format", "triangles")}


def _apply_args(s, a, target=None):
    """texture_apply with its own texture / layers (no draft): kept as it was,
    and the arguments stored so Update can run them again."""
    from . import engine

    o = s.body(a.get("body"))
    tol = num(a.get("tolerance", 0.02), "tolerance")
    tris, per_face = mesh.face_triangles(o.Shape, tol)
    pos = _positions(tris)
    n = len(pos) // 9
    settings = _numbers(a.get("settings"), "settings")
    if a.get("layers") is None and not a.get("texture"):
        raise ToolError("invalid_input", "texture: a library id (texture_list gives them), or layers: [{texture, faces, mode, settings}].")
    layers, masked = _layers_from_args(s, a, o, per_face, n, settings)
    glob = {k: v for k, v in settings.items() if k in engine.GLOBAL_KEYS}
    run = {**glob, "layers": layers}
    if a.get("rotation") is not None:
        run["frame"] = {"rotation": [num(x, "rotation") for x in a["rotation"]]}
    res = _run_engine(s, a, pos, run)
    name = a.get("name") or (target.Label if target is not None else f"{o.Label}_textured")
    keep = {k: v for k, v in a.items() if k not in ("export", "update", "bake", "new")}
    obj = _add_mesh(s, o, name, res["positions"], engine.credit_of(run), run, target=target, args=keep)
    out = {"body": o.Label, "object": obj.Name if obj is not None else None, "label": name,
           "triangles": res["triangles"], "inputTriangles": res["inputTriangles"], "repair": res["repair"],
           "warnings": res["warnings"], "layers": masked, "credit": engine.credit_of(run), "engine": res.get("engine")}
    _export_with(s, a, pos, run, res, out)
    return out


def run_settings(d, face_triangles):
    """The engine's settings for a draft: every layer with its face masks as
    triangles (solo is the preview's only), the frame's rotation."""
    from . import engine

    dd = d.to_dict() if hasattr(d, "to_dict") else dict(d)
    for L in dd.get("layers") or []:
        L["solo"] = False
    s = engine._draft_settings(dd)
    s["layers"] = engine._resolve_masks(engine.layer_specs(s), face_triangles)
    return s


def _apply_draft(s, a, d, target=None, name=None, bake=False):
    """BumpMesh's engine on the draft's part: a new Textured object, or the
    target updated in place. bake: BumpMesh's bake pipeline (no decimation),
    out["bakedTriangles"] the textured triangles of the stored mesh."""
    import json

    from . import engine

    o = _part(s, None, d)
    pos, ft = live.mesh_for(o)
    run = run_settings(d, ft)
    if not any(L.get("visible", True) is not False for L in run["layers"]):
        raise ToolError("invalid_state", "Every layer is hidden, so nothing would be textured: show one with "
                        "texture_layer { action: \"visible\", layer: 0, value: true }.")
    bake = bake or bool(getattr(target, "Baked", False))
    res = _run_engine(s, a, pos, run, "bake" if bake else "export")
    name = name or (target.Label if target is not None else f"{o.Label}_textured")
    obj = _add_mesh(s, o, name, res["positions"], engine.credit_of(run), run, target=target,
                    draft_json=json.dumps(d.to_dict()), baked=bake, tess=(pos, ft))
    out = {"body": o.Label, "object": obj.Name if obj is not None else None, "label": name,
           "updated": target is not None, "triangles": res["triangles"], "inputTriangles": res["inputTriangles"],
           "repair": res["repair"], "warnings": res["warnings"], "credit": engine.credit_of(run), "engine": res.get("engine"),
           # layer: the draft's own index (from 0), as texture_mask { layer } takes it.
           "layers": [{"layer": i, "name": L.get("name"), "texture": L["texture"], "mode": "include" if L.get("includeOnly") else "exclude",
                       "faces": list(L["mask"]["faces"])} for i, L in enumerate(d.layers)]}
    _export_with(s, a, pos, run, res, out)
    if bake and obj is not None:
        out["bakedTriangles"] = _stored_triangles(obj, res["positions"], res["bakedTriangles"])
    return obj, out


def _stored_triangles(obj, positions, tris):
    """The engine's triangle indices as indices of obj's stored mesh: the same
    when the mesh kept the facet order, else matched by centroid."""
    import numpy as np

    tris = [int(t) for t in tris]
    try:
        stored = live.mesh_positions(obj.Mesh)
    except (AttributeError, TypeError, ValueError):
        return tris
    p = np.asarray(positions, np.float64).reshape(-1, 3, 3)
    q = np.asarray(stored, np.float64).reshape(-1, 3, 3)
    if len(p) == len(q) and np.allclose(p, q, atol=1e-4):
        return tris
    key = lambda c: tuple(np.round(c, 3))  # noqa: E731
    where = {}
    for i, c in enumerate(q.mean(axis=1)):
        where.setdefault(key(c), []).append(i)
    out = set()
    for c in p[tris].mean(axis=1):
        out.update(where.get(key(c), ()))
    return sorted(out)


def _hide_source(doc, out):
    """The source body hidden while its textured result is shown (as
    PartDesign hides the previous feature), said in the answer."""
    from . import linked

    obj = live.find(doc, out.get("object")) if out.get("object") else None
    if obj is None:
        return
    try:
        linked.sync_source(linked.source_of(obj), doc)
    except Exception:  # noqa: BLE001 — the result is made: visibility never fails it
        return
    if linked.shown(obj):
        out["bodyHidden"] = True
        out["note"] = (out.get("note", "") + " " if out.get("note") else "") + (
            linked.HIDDEN_NOTE[0].upper() + linked.HIDDEN_NOTE[1:] + "; hiding or deleting the result shows it again.")


def texture_apply(s, a):
    """Add to document / Update / Bake Textures: BumpMesh's engine on a body
    (the draft's, or the texture / layers given here). A Textured object keeps
    the settings it was made with, goes out of date when its body changes, and
    runs again only on Update."""
    d = live.draft()
    doc = _doc(s)
    from . import linked

    linked.follow_draft()   # never old face names on renumbered faces
    if a.get("update"):
        target = live.find(doc, a["update"])
        if target is None or "SourceBody" not in getattr(target, "PropertiesList", []):
            raise ToolError("not_found", f"update {a['update']!r}: not a Textured object in this document (it has: "
                            f"{', '.join(o.Label for o in textured_objects(doc)) or 'none'}); leave update out to make one.")
        stored = getattr(target, "TextureDraft", "") or ""
        if stored:
            from . import linked

            if not linked.is_linked(target):
                label = target.Label
                target = next((o for o in linked.convert(doc) if o.Label == label), target)
            included = linked.include_new(target, run=False) if a.get("include_new") else []
            out = _update_linked(s, a, target)
            if included:
                out["included"] = included
        else:
            import json

            args = json.loads(getattr(target, "TextureArgs", "") or "{}")
            if not args:
                raise ToolError("invalid_state", f"{target.Label}: made without stored settings, so there is nothing to run again; "
                                "texture its body afresh (texture_apply with body and texture, or the draft).")
            args["body"] = target.SourceBody
            out = _apply_args(s, args, target=target)
            out["updated"] = True
        out["outOfDate"] = False
        _after_apply(doc, out)
        out["next"] = (f"texture_check {{ object: \"{out['object']}\" }} measures it face by face against its body; "
                       "texture_export for an STL / 3MF.")
        return remember("texture_apply", out)
    if a.get("texture") or a.get("layers") is not None:
        out = _apply_args(s, a)
        _draft_follows_args(s, a, d, out)
        _after_apply(doc, out)
        out["next"] = ((f"texture_check {{ object: \"{out['object']}\" }}" if out.get("object") else
                        f"texture_check {{ body: \"{out['body']}\", path: <the export> }}")
                       + " measures the depth face by face; slice the export or the mesh object.")
        return remember("texture_apply", out)
    if a.get("draft") is not None:
        try:
            d.load(a["draft"])
        except (ValueError, KeyError, TypeError) as e:
            raise ToolError("invalid_input", f"draft: {e}") from None
    if a.get("body") is not None:
        _take_part(d, _part(s, a["body"], d), doc)
    if d.part is None or _draft_home(doc) is None:
        _take_part(d, _part(s, None, None), doc)
    target = None
    if not a.get("new") and not a.get("bake"):
        t = live.find(doc, live.TARGET["object"])
        if t is not None and getattr(t, "SourceBody", None) == d.part:
            target = t
    obj, out = _apply_draft(s, a, d, target=target, name=a.get("name"), bake=bool(a.get("bake")))
    baked = out.pop("bakedTriangles", None)
    if obj is not None:
        live.TARGET["object"] = obj.Name
    if a.get("bake") and obj is not None:
        # Bake Textures: the textured mesh becomes the part, with a fresh layer
        # on the same global settings (BumpMesh's bake). Mask just-baked faces
        # seeds that layer's exclude mask with the triangles textured this
        # round (faceParentId of faces that weren't excluded), so the next
        # pass doesn't double up. The Textured object keeps its own settings
        # and can still be updated.
        bake_mask = bool(d.settings.get("bakeMask"))
        keep = dict(d.settings)
        with d.batch():
            d.reset()
            d.update({k: v for k, v in keep.items() if k in d.settings})
            d.set_part(obj.Name)
            if bake_mask and baked:
                d.mask_add(triangles=baked, layer=0)
        live.TARGET["object"] = None
        out["baked"] = obj.Name
        out["maskedTriangles"] = len(baked or []) if bake_mask else 0
    _after_apply(doc, out)
    out["next"] = ("Update (texture_apply { update: \"%s\" }) after the body changes; texture_check { object: \"%s\" } "
                   "measures it face by face; texture_export for an STL / 3MF." % (out["object"] or "", out["object"] or ""))
    return remember("texture_apply", out)


def _after_apply(doc, out):
    """What texture_apply leaves in the window: the preview closed and
    switched off (its nodes kept in the draft's view), a new Textured
    result shown, its source hidden behind it; and out["window"], the facts a
    live run reads when the view looks wrong (Window.view_facts: the
    document in front, the eyes, the preview's nodes, the camera)."""
    _end_preview(out)
    obj = live.find(doc, out.get("object")) if out.get("object") else None
    if obj is not None and not out.get("updated"):
        from . import linked

        try:
            if not linked.shown(obj):
                linked._show(obj, True)   # a new result is made to be seen (Update keeps the person's eye)
                out["resultShown"] = True
        except Exception:  # noqa: BLE001 — the result is made: the view never fails it
            pass
    _hide_source(doc, out)
    _view_facts(doc, out, out.get("object"), _source_name(obj))
    return out


def _source_name(obj):
    from . import linked

    try:
        src = linked.source_of(obj) if obj is not None else None
    except Exception:  # noqa: BLE001
        src = None
    return getattr(src, "Name", None)


def _view_facts(doc, out, result=None, source=None):
    """out["window"]: the window's facts (no window: nothing). Not "view":
    Agent Link's echo reads that key as a drawing view it made."""
    fn = getattr(live.GUI, "view_facts", None)
    if fn is None:
        return out
    try:
        out["window"] = fn(doc, result, source)
    except Exception as e:  # noqa: BLE001 — facts only
        out["window"] = {"error": f"{type(e).__name__}: {e}"}
    return out


def _end_preview(out):
    """The sequence ends with texture_apply or texture_export: the preview
    is taken out of the 3D view (its nodes stayed over the new Textured
    object), the part shown as before; the next draft tool or the sidebar
    brings it back. out["previewClosed"] when one was showing."""
    g = live.GUI
    fn = getattr(g, "end_preview", None)
    if fn is None:
        return out
    try:
        if fn():
            out["previewClosed"] = True
    except Exception:  # noqa: BLE001 — the result is made: the view never fails it
        pass
    return out


def _draft_follows_args(s, a, d, out):
    """texture_apply with its own texture or layers: the draft (and so the
    sidebar, its layer list and texture) takes what was applied, so the
    panel shows the texture just used, not the draft's old one (Knurling
    beside a Hexagon result, round 4 0010). One undo step, in the call's
    transaction. The Textured object keeps its own arguments for Update."""
    from . import engine

    def names(sets):
        got = {}
        for k, v in (sets if isinstance(sets, dict) else {}).items():
            if k in SET_ALIASES:
                k, v = SET_ALIASES[k], abs(v) if isinstance(v, (int, float)) else v
            if k == "mappingMode" and isinstance(v, str):
                v = engine.MAPPING_MODES.get(v, v)
            got[k] = v
        return got

    o = s.body(a.get("body"))
    top = names(a.get("settings"))
    layers = []
    for row, spec in zip(out.get("layers") or [], _layer_specs(a, lambda q: pick_faces(o.Shape, q))):
        ls = {**top, **names(spec.get("settings"))}
        layers.append({"texture": _texture_id(spec.get("texture")), "settings": ls,
                       "includeOnly": row.get("mode") == "include", "blendAdd": bool(spec.get("blendAdd")) and row["layer"] > 0,
                       "visible": spec.get("visible", True) is not False,
                       "mask": {"faces": list(row.get("faces") or []), "triangles": []}})
    if not layers:
        return
    data = {"part": o.Name, "layers": layers, "active": 0, "settings": top}
    if a.get("rotation") is not None:
        data["frame"] = {"rotation": list(a["rotation"])}
    try:
        live.bind_doc(getattr(o, "Document", None) or _doc(s))
        d.load(data)
    except (ValueError, KeyError, TypeError):
        return
    live.TARGET["object"] = None   # made from its own arguments: the draft's Add to Document makes a new one
    out["draft"] = summary(d, s)


def _update_linked(s, a, target):
    """Update of a linked Textured object: its stored draft with its masks
    followed onto the body as it is (linked.prepare), here and now."""
    from . import engine, linked

    linked.ensure_props(target)   # an older object's Source becomes a PropertyLinkGlobal
    src = linked.source_of(target)
    if src is None:
        raise ToolError("not_found", f"{target.Label}: the body it textures is gone from the document, so it cannot be updated; "
                        "texture another body with texture_draft { part } then texture_apply { new: true }.")
    job = linked.prepare(target, src)
    if not any(L.get("visible", True) is not False for L in job.run_settings["layers"]):
        raise ToolError("invalid_state", "Every layer is hidden, so nothing would be textured: show one with "
                        "texture_layer { action: \"visible\", layer: 0, value: true }.")
    res = _run_engine(s, a, job.pos, job.run_settings, job.mode)
    linked.write(target, job, res)
    out = {"body": src.Label, "object": target.Name, "label": target.Label, "updated": True,
           "triangles": res["triangles"], "inputTriangles": res["inputTriangles"], "repair": res["repair"],
           "warnings": res["warnings"], "credit": engine.credit_of(job.run_settings), "state": linked.state(target),
           "engine": res.get("engine")}
    if job.report:
        out["maskReport"] = [r["message"] for r in job.report]
    if job.new_faces:
        out["newFaces"] = job.new_faces
        out["newFacesNote"] = linked.new_faces_text(job.new_faces)
    _export_with(s, a, job.pos, job.run_settings, res, out)
    return out


def _make_mesh(positions):
    """A Mesh.Mesh from float32 positions, or None without FreeCAD's Mesh."""
    import tempfile

    from .engine import writers

    try:
        import Mesh
    except ImportError:
        return None
    fd, tmp = tempfile.mkstemp(suffix=".stl")
    os.close(fd)
    try:
        with open(tmp, "wb") as fh:
            fh.write(writers.stl_bytes(positions))
        m = Mesh.Mesh(tmp)
        try:   # one winding throughout (FreeCAD's own check), whatever the input's faces were
            m.harmonizeNormals()
        except Exception:  # noqa: BLE001 — an older Mesh module
            pass
        return m
    finally:
        os.unlink(tmp)


PROPS = (("Credit", "App::PropertyString", "The credit this texture carries"),
         ("TextureSettings", "App::PropertyString", "BumpMesh's settings the mesh was made with (JSON)"),
         ("TextureDraft", "App::PropertyString", "The Texturizer draft it was made from (JSON): Update runs it again"),
         ("TextureArgs", "App::PropertyString", "The texture_apply arguments it was made with (JSON)"),
         ("SourceBody", "App::PropertyString", "The body it textures"),
         ("SourceSignature", "App::PropertyString", "The body's geometry when it was textured"),
         ("OutOfDate", "App::PropertyBool", "The body changed since: Update to texture it again"),
         ("Baked", "App::PropertyBool", "Made by Bake Textures: undecimated, so a mask on it keeps its triangles"))


def _add_mesh(s, body, name, positions, credit, run, target=None, draft_json="", args=None, baked=False, tess=None):
    """The textured mesh as a linked Textured feature beside the body (or the
    target updated; an old Mesh::Feature one converted first), carrying its
    credit, the settings it was made with, a link to its body, the body's
    fingerprint and, from a draft, its masks as geometry (linked.anchors,
    every face of the body as it is now known): it textures its body again
    when the body changes (linked.TexturedFeature)."""
    import json

    from . import linked

    # No document to put it in (a body that belongs to none) or no FreeCAD
    # Mesh module: no object, the export still made.
    doc = getattr(s, "doc", None) or getattr(body, "Document", None)
    if doc is None and target is None:
        return None
    m = _make_mesh(positions)
    if m is None:
        return None
    doc = doc or target.Document
    if target is not None and not linked.is_linked(target):
        label = target.Label
        target = next((o for o in linked.convert(doc) if o.Label == label), None)
    obj = target if target is not None else linked.make(doc)
    obj.Label = name
    obj.Mesh = m
    vals = {"Credit": credit, "TextureSettings": json.dumps(_jsonable(run)), "TextureDraft": draft_json or "",
            "TextureArgs": json.dumps(args) if args else "", "SourceBody": body.Name,
            "SourceSignature": live.signature(body), "OutOfDate": False, "Baked": bool(baked)}
    for prop, kind, doc_text in PROPS:
        if prop not in obj.PropertiesList:
            obj.addProperty(kind, prop, "Texturizer", doc_text)
        setattr(obj, prop, vals[prop])
    data = json.loads(draft_json) if draft_json else None
    pos, ft = tess if tess is not None else (None, None)
    linked.link(obj, body, data, pos, ft)
    _set_new(obj, [])
    doc.recompute()
    return obj


def _set_new(obj, faces):
    import json

    if getattr(obj, "NewFaces", None) != json.dumps(faces):
        obj.NewFaces = json.dumps(faces)


def _jsonable(run):
    def mask(m):
        if m is None:
            return None, None
        if isinstance(m, dict):
            return len(m.get("triangles") or []), m.get("mode")
        return int(len(m.triangles)), m.mode

    out = {k: v for k, v in run.items() if k != "layers"}
    out["layers"] = []
    for L in run["layers"]:
        n, mode = mask(L.get("mask"))
        out["layers"].append({"texture": L["texture"], "settings": L.get("settings") or {}, "blendAdd": bool(L.get("blendAdd")),
                              "visible": L.get("visible", True) is not False, "maskTriangles": n, "maskMode": mode})
    return out


def textured_objects(doc):
    return [o for o in (doc.Objects if doc is not None else []) if "SourceBody" in getattr(o, "PropertiesList", [])]


def mark_out_of_date(doc, changed=None):
    """Sets OutOfDate on the Textured objects whose SourceBody's geometry no
    longer matches (all, or those of the changed object). Never reruns.
    Returns the names now out of date."""
    out = []
    for t in textured_objects(doc):
        if changed is not None and t.SourceBody != getattr(changed, "Name", changed):
            continue
        src = live.find(doc, t.SourceBody)
        stale = src is None or live.signature(src) != t.SourceSignature
        if "OutOfDate" in t.PropertiesList and bool(t.OutOfDate) != stale:
            t.OutOfDate = stale
        if stale:
            out.append(t.Name)
    return out


class Observer:
    """A FreeCAD document observer: a body recomputed marks its Textured
    objects out of date (FreeCAD.addDocumentObserver)."""

    def slotChangedObject(self, obj, prop):  # noqa: N802 (FreeCAD)
        if prop in ("TextureState", "NewFaces") and live.GUI is not None:
            fn = getattr(live.GUI, "textured_changed", None)
            try:
                if fn is not None:
                    fn(obj)
            except Exception:  # noqa: BLE001 — never raise into FreeCAD
                pass
            return
        if prop != "Visibility" or live.GUI is None:
            return
        try:
            fn = getattr(live.GUI, "part_visibility", None)
            if fn is not None:
                fn(obj)
        except Exception:  # noqa: BLE001 — never raise into FreeCAD
            pass

    _follow = False

    def slotRecomputedObject(self, obj):  # noqa: N802 (FreeCAD)
        try:
            if getattr(obj, "Name", None) == live.draft().part:
                self._follow = True   # the sidebar's draft follows once the recompute is done
        except Exception:  # noqa: BLE001 — never raise into FreeCAD
            pass
        try:
            doc = obj.Document
            if any(t.SourceBody == obj.Name for t in textured_objects(doc)):
                mark_out_of_date(doc, obj)
        except Exception:  # noqa: BLE001 — never raise into FreeCAD
            pass

    def slotRecomputedDocument(self, doc):  # noqa: N802 (FreeCAD)
        """After the recompute (still inside the CAD step's transaction, so its
        Undo undoes the draft's move too): the draft onto its changed part."""
        if not self._follow:
            return
        self._follow = False
        try:
            from . import linked

            linked.follow_draft(doc)
        except Exception:  # noqa: BLE001
            pass

    # The preview belongs to the draft's document (window round 4: it stayed
    # in the Tutor's documents and hid their PointBox): another document in
    # front steps it aside, its own closing takes it away.
    def slotActivateDocument(self, doc):  # noqa: N802 (FreeCAD)
        fn = getattr(live.GUI, "document_changed", None)
        try:
            if fn is not None:
                fn(doc)
        except Exception:  # noqa: BLE001 — never raise into FreeCAD
            pass

    def slotDeletedDocument(self, doc):  # noqa: N802 (FreeCAD)
        fn = getattr(live.GUI, "document_closed", None)
        try:
            if fn is not None:
                fn(doc)
            else:
                live.document_closed(doc)
        except Exception:  # noqa: BLE001 — never raise into FreeCAD
            pass

    # The preview stands in for the body by hiding it; a save meanwhile
    # would keep the body hidden in the file, so it is saved shown.
    _hid = None

    def slotStartSaveDocument(self, doc, _name):  # noqa: N802 (FreeCAD)
        try:
            v = getattr(live.GUI, "view", None)
            c = getattr(live.GUI, "controller", None)
            if v is None or c is None or c.showing in (None, "body") or v.get_body_visible():
                return
            self._hid = v
            v.set_body_visible(True)
        except Exception:  # noqa: BLE001
            self._hid = None

    def slotFinishSaveDocument(self, doc, _name):  # noqa: N802 (FreeCAD)
        v, self._hid = self._hid, None
        if v is not None:
            try:
                v.set_body_visible(False)
                g = getattr(__import__("FreeCADGui"), "getDocument")(doc.Name)
                g.Modified = False  # a save just happened: the view's own change is not unsaved work
            except Exception:  # noqa: BLE001
                pass


# ── the draft (design 5.1): one Draft, shared with the sidebar ─────────────

def _doc(s):
    doc = getattr(s, "doc", None) or live._doc()
    if doc is not None:
        live.LAST_DOC["doc"] = doc
    return doc


def _draft_home(doc):
    """Where the draft's part is: doc when the draft is bound to it (or to
    none yet), else the draft's own document (None when that was closed):
    another document's object of the same Name is never taken for it."""
    return doc if live.in_front(doc) else live.draft_doc()


def _take_part(d, o, doc=None):
    """The draft takes o as its part, bound to o's document (the same Name
    in another document is another part: said as a part change)."""
    home = getattr(o, "Document", None) or doc
    moved = home is not None and not live.in_front(home)
    live.bind_doc(home)
    d.set_part(o.Name, force=moved)


def _part(s, ref, d=None):
    """The object a draft textures: ref (a body, or a Mesh object), else the
    draft's part (in the draft's own document), else the one body shown."""
    doc = _doc(s)
    if ref is None and d is not None and d.part:
        home = _draft_home(doc)
        o = live.find(home, d.part) if home is not None else None
        if o is not None:
            return o
    if ref is not None:
        o = live.find(doc, ref)
        if o is not None and (getattr(getattr(o, "Shape", None), "Faces", None) or getattr(o, "Mesh", None) is not None):
            return o
        return s.body(ref)
    # No part named and none in the draft: the last one this document's draft
    # used, else the one best visible solid; never a hidden object.
    o = live.choose_part(doc)
    if o is not None:
        return o
    cs = live.candidates(doc)
    if not cs:
        raise ToolError("not_found", "No visible solid or mesh in the document to texture: show or make one, then name it "
                        "(texture_draft { part }, or body in texture_apply).")
    raise ToolError("invalid_input", f"part / body: more than one could be meant; name one of {', '.join(o.Label for o in cs)} "
                    "(texture_draft { part }).")


def _watch():
    """The sidebar open in the window, so the draft moves where it can be
    watched; the draft's masks on its part as it is now (linked.follow_draft:
    a CAD change the observer did not see is caught before a tool reads them)."""
    from . import linked

    linked.follow_draft()
    if live.GUI is not None:
        try:
            live.GUI.open()   # from another thread the window side hands it to the GUI thread
        except Exception:  # noqa: BLE001 — a closed window never stops a tool
            pass


def _asks():
    """Whether a file dialog may be shown now: FreeCAD's window is there and
    this is its thread (a tool run as Agent Link's job never opens one: a
    dialog made on a worker thread stops FreeCAD)."""
    if live.GUI is None:
        return False
    from . import guithread

    return guithread.on_gui_thread()


def _layer(d, a):
    i = a.get("layer")
    if i is None:
        return d.active
    if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(d.layers):
        raise ToolError("invalid_input", f"layer {i!r}: 0 to {len(d.layers) - 1}, bottom first (the draft has {len(d.layers)}; "
                        "texture_layer { action: \"add\" } adds one, up to 4).")
    return i


def _gui(what):
    """The window side, or a refusal saying what was needed."""
    if live.GUI is None:
        raise ToolError("invalid_input", f"{what}: needs FreeCAD's window, and there is none; give the name, path or point "
                        "in the arguments instead.")
    return live.GUI


def mask_counts(m):
    """A layer mask as the agent reads it: its B-rep faces, and triangles, the
    source triangles masked by any means (whole, split by the Precision brush,
    or under soft coverage); brush: how many of them the brush split or
    painted soft, when it did (paint.mask_from_state keeps those out of the
    whole triangles)."""
    split = {int(r[0]) for r in m.get("split") or []}
    soft = {int(c) // 3 for c in (m.get("soft") or {}).get("corners") or []}
    out = {"faces": list(m["faces"]), "triangles": len(set(int(t) for t in m["triangles"]) | split | soft)}
    if split or soft:
        out["brush"] = {"split": len(split), "soft": len(soft)}
    return out


def summary(d, s=None):
    """What the draft holds, short: the part, the frame, each layer."""
    from . import controls

    out = {"part": d.part, "frame": dict(d.frame), "active": d.active, "layers": []}
    if s is not None and d.part:
        o = live.find(_doc(s), d.part)
        if o is not None:
            out["label"] = o.Label
    for i, L in enumerate(d.layers):
        changed = {k: v for k, v in L["settings"].items() if controls.BY_KEY.get(k, {}).get("default", v) != v}
        out["layers"].append({"layer": i, "name": L["name"], "texture": L["texture"], "visible": L["visible"],
                              "solo": L["solo"], "blend": "add" if L["blendAdd"] else "cover",
                              "mode": "include" if L["includeOnly"] else "exclude",
                              **mask_counts(L["mask"]), "settings": changed})
    g = {k: v for k, v in d.settings.items() if controls.BY_KEY.get(k, {}).get("default", v) != v}
    out["settings"] = g
    out["canUndo"], out["canRedo"] = d.can_undo(), d.can_redo()
    return out


def _bounds_of_part(s, d):
    import numpy as np

    pos, _ft = live.mesh_for(_part(s, None, d))
    p = np.asarray(pos, float).reshape(-1, 3)
    mn, mx = p.min(0), p.max(0)
    return {"min": mn.tolist(), "max": mx.tolist(), "size": (mx - mn).tolist(), "center": ((mn + mx) / 2).tolist()}


def _load_project(s, d, path, mode):
    from . import project

    note = []
    with d.batch():   # the project and its model: one step
        try:
            res = project.load(path, d, mode=mode)
        except (OSError, ValueError, KeyError) as e:
            raise ToolError("invalid_input", f"project: {e}") from None
        if res.get("model") is not None and res["mode"] == "all":
            obj = _add_plain_mesh(s, os.path.splitext(os.path.basename(path))[0], res["model"])
            if obj is not None:
                _take_part(d, obj, _doc(s))
                note.append(f"Model loaded as {obj.Label}.")
    return {"mode": res["mode"], "warnings": res.get("warnings") or [], "note": " ".join(note)}


def _add_plain_mesh(s, name, tris):
    import numpy as np

    m = _make_mesh(np.asarray(tris, np.float32).reshape(-1))
    doc = _doc(s)
    if m is None or doc is None:
        return None
    obj = doc.addObject("Mesh::Feature", "Model")
    obj.Label = name
    obj.Mesh = m
    doc.recompute()
    return obj


def texture_draft(s, a):
    """Start or read the draft: the part (a body, a Mesh, or a Textured object
    to texture again), a whole draft, Reset to defaults or Load Project. Opens
    the sidebar in the window."""
    import json

    d = live.draft()
    if a.get("reset"):
        d.reset()
    if a.get("draft") is not None:
        try:
            d.load(a["draft"])
        except (ValueError, KeyError, TypeError) as e:
            raise ToolError("invalid_input", f"draft: {e}") from None
    ref = a.get("part", a.get("body"))
    if a.get("load") == "selection":
        # Use Selected Part: the selected visible solid, else as a fresh
        # draft picks (live.choose_part). Models come from the document: an
        # STL is imported with FreeCAD's File > Import first.
        ref = _gui("load: selection").selected_object() or ref
        if ref is None:
            o = live.choose_part(_doc(s))
            if o is None:
                raise ToolError("invalid_input", "load: selection: nothing is selected and no one visible solid to take; "
                                "give part (an object's name or label; an STL is imported with File > Import first).")
            ref = o.Name
    if ref is not None or (d.part is None and not a.get("project")):
        o = _part(s, ref, None)
        stored = getattr(o, "TextureDraft", "") if "SourceBody" in getattr(o, "PropertiesList", []) else ""
        if stored:
            # A Textured object: its settings back in the draft, its body the part.
            data = json.loads(stored)
            data["part"] = o.SourceBody
            live.bind_doc(getattr(o, "Document", None) or _doc(s))
            d.load(data)
            live.TARGET["object"] = o.Name
        else:
            if o.Name != d.part or not live.in_front(getattr(o, "Document", None) or _doc(s)):
                live.TARGET["object"] = None
            _take_part(d, o, _doc(s))
    out = {}
    pj = a.get("project")
    if pj:
        if pj == "ask":
            g = _gui("project: ask")
            pj = g.ask_path("bumpmesh", save=False)
            mode = None
            if pj:
                from . import project

                try:
                    mode = a.get("mode") or g.ask_load_mode(project.read(pj).has_model)
                except (OSError, ValueError) as e:
                    raise ToolError("invalid_input", f"project: {e}") from None
            if not pj or not mode:
                return {"cancelled": True, "draft": summary(d, s)}
            a = dict(a, mode=mode)
        out["project"] = _load_project(s, d, _local(s, pj), a.get("mode", "all"))
    _watch()
    if d.part:
        pos, ft = live.mesh_for(_part(s, None, d))
        out["mesh"] = {"triangles": len(pos) // 9, "faces": len(ft)}
    out["draft"] = summary(d, s)
    out.update(_part_word(s, d))
    if out.get("part"):
        out["label"] = out["part"]   # the part's label (the words: an argument's part would come first)
    out["note"] = ("The sidebar shows this draft. texture_set (texture, settings), texture_mask (faces), texture_layer, "
                   "then texture_preview to look and texture_apply to add it to the document.")
    return out


def _pick_face(s, a, what):
    """(object, face name, face) from a.face, else the window's selection."""
    d = live.draft()
    name = a.get("face")
    if name is None:
        sel = _gui(what).selected_faces()
        if not sel or not sel[1]:
            raise ToolError("invalid_input", f"{what}: no face is picked in the 3D view; give face (a name such as Face3).")
        ref, faces = sel
        o = _part(s, ref, d)
        name = faces[0]
    else:
        o = _part(s, None, d)
    shape = getattr(o, "Shape", None)
    names = face_names(shape, name) if shape is not None else set()
    n = sorted(names)[0] if names else None
    if n is None:
        raise ToolError("not_found", f"{what}: no face {name!r} on {o.Label} (a face name such as Face3; a Mesh part has none).")
    return o, n, shape.Faces[int(n[4:]) - 1]


def _face_normal(f, at=None):
    u0, u1, v0, v1 = f.ParameterRange
    if at is not None:
        try:
            u, v = f.Surface.parameter(at)
            n = f.normalAt(u, v)
            return (n.x, n.y, n.z)
        except Exception:  # noqa: BLE001
            pass
    n = f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
    return (n.x, n.y, n.z)


def _custom_texture(path):
    """Upload custom map: the file copied into your textures folder (kept as
    it is), listed as category custom."""
    import shutil

    from .engine import library

    if not os.path.isfile(path):
        raise ToolError("not_found", f"texture file {path!r}: not found; give the full path of a PNG heightmap (or a library id from texture_list).")
    d = live.custom_dir()
    if d:
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, os.path.basename(path))
        if os.path.abspath(dst) != os.path.abspath(path):
            shutil.copyfile(path, dst)
        library.add_custom_dir(d)
        return "custom:" + os.path.basename(path)
    return library.get(path)["id"]


def texture_set(s, a):
    """Layer and global settings by BumpMesh's names, the texture, the frame
    (Rotate, Place on Face), the cylinder (Auto-fit, Reset, From face) and the
    stamp: a partial update, one undo step, shown live in the sidebar."""
    from . import controls, projection_extras
    from .engine import frame_matrix, library

    _watch()
    d = live.draft()
    i = _layer(d, a)
    done = {}
    with d.batch():
        if a.get("active") is not None:
            d.set_active(_layer(d, {"layer": a["active"]}))
            i = d.active
        tex = a.get("texture")
        if isinstance(tex, dict):
            path = tex.get("file") or (tex.get("upload") and _gui("texture: upload").ask_path("image", save=False))
            if not path:
                return {"cancelled": True, "draft": summary(d, s)}
            tex = _custom_texture(_local(s, path))
        if tex:
            e = library.get(_texture_id(tex))
            defs = dict(e.get("defaults") or {})
            if d.part:
                b = _bounds_of_part(s, d)
                defs["scaleU"] = defs["scaleV"] = library.default_scale_mm(defs.get("tileFraction"), b)
            d.set_texture(e["id"], layer=i, defaults={k: v for k, v in defs.items() if k in ("scaleU", "scaleV", "textureSmoothing")})
            done["texture"] = e["id"]
        sets = a.get("settings") or {}
        if not isinstance(sets, dict):
            raise ToolError("invalid_input", "settings: {name: value} by BumpMesh's names (texture_settings lists them).")
        for k, v in sets.items():
            name = k
            if k in SET_ALIASES:   # texture_apply's (and a .bumpmesh project's) name for the same control
                k, v = SET_ALIASES[k], abs(num(v, f"settings.{k}"))
            c = controls.BY_KEY.get(k)
            if c is None or c["scope"] not in ("layer", "global", "frame") or c["type"] in ("button", "heading", "note"):
                from . import engine

                if k in set(engine.LAYER_KEYS) | set(engine.GLOBAL_KEYS):
                    raise ToolError("invalid_input", f"settings.{k}: an engine tuning setting the draft does not hold "
                                                     "(texture_apply's settings take it).")
                raise ToolError("invalid_input", f"settings.{k}: not a BumpMesh setting (texture_settings lists them).")
            if k == "mappingMode" and isinstance(v, str):
                from . import engine

                if v not in engine.MAPPING_MODES:
                    raise ToolError("invalid_input", f"settings.mappingMode: 0-8 or one of {', '.join(engine.MAPPING_MODES)}.")
                v = engine.MAPPING_MODES[v]
            try:
                done[name] = d.set(k, v, layer=i)
            except (TypeError, ValueError) as e:
                raise ToolError("invalid_input", f"settings.{k}: {e}") from None
        fr = a.get("frame")
        if fr is not None:
            if not isinstance(fr, dict):
                raise ToolError("invalid_input", "frame: {rotation: [x, y, z]} degrees, {direction: [x, y, z]}, or {face: \"selected\" | FaceN}.")
            if fr.get("rotation") is not None:
                rot = [num(x, "frame.rotation") for x in fr["rotation"]]
                if len(rot) != 3:
                    raise ToolError("invalid_input", "frame.rotation: [x, y, z] degrees.")
                d.set_frame(rotation=rot)
            if fr.get("direction") is not None:
                # The projection gizmo's arrow: the planar projection's axis (Triplanar
                # and Cubic: the frame's Z) along this direction, the frame turned the least.
                from . import gizmo

                dv = fr["direction"]
                if not isinstance(dv, (list, tuple)) or len(dv) != 3:
                    raise ToolError("invalid_input", "frame.direction: [x, y, z], the way the projection looks along.")
                dv = [num(x, "frame.direction") for x in dv]
                if not any(dv):
                    raise ToolError("invalid_input", "frame.direction: not [0, 0, 0].")
                mode = d.layer(i)["settings"].get("mappingMode")
                d.set_frame(rotation=gizmo.point_projection(d.frame.get("rotation"), mode, dv))
            if fr.get("face") is not None:
                # Place on Face: the picked face down on the bed (its normal to -Z),
                # by turning the texturing frame; the body does not move.
                o, n, f = _pick_face(s, {"face": None if fr["face"] == "selected" else fr["face"]}, "frame.face")
                nx, ny, nz = _face_normal(f)
                r = projection_extras.align_rotation((-nx, -ny, -nz))
                d.set_frame(rotation=[r["rotateX"], r["rotateY"], r["rotateZ"]], face=n)
            done["frame"] = dict(d.frame)
        cyl = a.get("cylinder")
        if cyl is not None:
            if cyl == "reset":
                up = projection_extras.reset()
            elif cyl == "autofit":
                pos, _ft = live.mesh_for(_part(s, None, d))
                R = frame_matrix(d.frame.get("rotation"))
                import numpy as np

                p = (np.asarray(pos, float).reshape(-1, 3) @ R.T).reshape(-1, 9)
                up = projection_extras.auto_fit(p)
                if up is None:
                    raise ToolError("failed", "cylinder: autofit found no cylinder in the part's side faces; use cylinder: "
                                    "\"from_face\" with face (a cylindrical face's name), or set cylinderCenterX, "
                                    "cylinderCenterY and cylinderRadius in settings.")
            elif cyl == "from_face":
                o, n, f = _pick_face(s, a, "cylinder: from_face")
                R = frame_matrix(d.frame.get("rotation"))

                def to_frame(v, _direction):
                    import numpy as np

                    return tuple((R @ np.asarray(v, float)).tolist())

                try:
                    up = projection_extras.from_face(f.Surface, to_frame=to_frame)
                except (AttributeError, ValueError) as e:
                    raise ToolError("invalid_input", f"cylinder: from_face: {e}") from None
            else:
                raise ToolError("invalid_input", f"cylinder {cyl!r}: autofit, reset or from_face (with face).")
            done["cylinder"] = projection_extras.apply(d, up, layer=i)
        st = a.get("stamp")
        if st is not None:
            L = d.layer(i)["settings"]
            if st in ("on", "off", True, False):
                up = projection_extras.stamp_mode(L, st in ("on", True))
            elif st == "place" or isinstance(st, dict):
                pt = st.get("point") if isinstance(st, dict) else None
                nr = st.get("normal") if isinstance(st, dict) else None
                if pt is None:
                    pick = _gui("stamp: place").picked_point()
                    if not pick:
                        raise ToolError("invalid_input", "stamp: place: no spot was clicked on the part; give "
                                        "stamp: {point: [x, y, z], normal: [x, y, z]} instead.")
                    pt, nr = pick
                if nr is None:
                    raise ToolError("invalid_input", "stamp.normal: [x, y, z], the surface's outward direction at point.")
                if not L.get("stampOnce"):
                    projection_extras.apply(d, projection_extras.stamp_mode(L, True), layer=i)
                pos, _ft = live.mesh_for(_part(s, None, d))
                # The stamp's fields live in the texturing frame (the engine turns the part by Rotate).
                import numpy as np

                R = frame_matrix(d.frame.get("rotation"))
                pos = (np.asarray(pos, float).reshape(-1, 3) @ R.T).reshape(-1)
                pt, nr = (R @ np.asarray(pt, float)).tolist(), (R @ np.asarray(nr, float)).tolist()
                up = projection_extras.stamp_place(pt, nr, d.layer(i)["settings"], positions=pos)
            else:
                raise ToolError("invalid_input", "stamp: on, off, place, or {point, normal}.")
            done["stamp"] = projection_extras.apply(d, up, layer=i)
    out = {"layer": i, "set": done, "draft": summary(d, s), **_part_word(s, d)}
    if done.get("texture"):
        out["texture"] = done["texture"]
    names = [k for k in done if k != "texture"]
    if names:
        out["setNames"] = names
    return _follow_preview(out)


def _part_word(s, d):
    """{"part": the draft's part's label} for the window's words and
    selection (Agent Link's echo: PHRASES {part}, SELECTS part), or {}."""
    if not d.part:
        return {}
    try:
        home = _draft_home(_doc(s))
        o = live.find(home, d.part) if home is not None else None
    except Exception:  # noqa: BLE001 — words only
        o = None
    return {"part": o.Label if o is not None else d.part}


def _faces_by(shape, how, ref_faces, a):
    """Add by…: faces facing the same direction as the picked ones (within
    angle), inside their box, or on the same surface."""
    out = set()
    refs = [shape.Faces[int(n[4:]) - 1] for n in ref_faces]
    if not refs:
        raise ToolError("invalid_input", f"add_by {how}: needs the faces to start from; give faces (names such as Face3).")
    if how == "direction":
        lim = math.cos(math.radians(num(a.get("angle", 10), "angle")))
        dirs = [_face_normal(f) for f in refs]
        for k, f in enumerate(shape.Faces):
            n = _face_normal(f)
            if any(n[0] * r[0] + n[1] * r[1] + n[2] * r[2] >= lim for r in dirs):
                out.add(f"Face{k + 1}")
    elif how == "box":
        bb = refs[0].BoundBox
        for f in refs[1:]:
            bb.add(f.BoundBox)
        tol = 1e-6
        for k, f in enumerate(shape.Faces):
            fb = f.BoundBox
            if (fb.XMin >= bb.XMin - tol and fb.YMin >= bb.YMin - tol and fb.ZMin >= bb.ZMin - tol
                    and fb.XMax <= bb.XMax + tol and fb.YMax <= bb.YMax + tol and fb.ZMax <= bb.ZMax + tol):
                out.add(f"Face{k + 1}")
    elif how == "surface":
        keys = {_surface_key(f) for f in refs}
        for k, f in enumerate(shape.Faces):
            if _surface_key(f) in keys:
                out.add(f"Face{k + 1}")
    else:
        raise ToolError("invalid_input", "add_by: direction, box or surface.")
    return out


def _surface_key(f):
    s = f.Surface
    kind = type(s).__name__

    def r(v):
        return tuple(round(float(x), 5) for x in (v.x, v.y, v.z))

    if kind == "Plane":
        n = s.Axis
        return (kind, r(n), round(float(s.Position.dot(n)), 5))
    if kind in ("Cylinder", "Cone"):
        return (kind, r(s.Axis), round(float(s.Radius), 5), r(s.Center.cross(s.Axis)))
    if kind == "Sphere":
        return (kind, r(s.Center), round(float(s.Radius), 5))
    return (kind, id(f))


def _flood(shape, seeds, max_angle):
    """Fill: from the seed faces across edges whose faces meet at under
    max_angle degrees (at the edge's middle), as BumpMesh's bucket fill."""
    lim = math.cos(math.radians(max_angle))
    edges = {}
    for k, f in enumerate(shape.Faces):
        for e in f.Edges:
            edges.setdefault(e.hashCode(), []).append(k)
    todo = [int(n[4:]) - 1 for n in seeds]
    seen = set(todo)
    while todo:
        k = todo.pop()
        f = shape.Faces[k]
        for e in f.Edges:
            others = [j for j in edges.get(e.hashCode(), []) if j not in seen]
            if not others:
                continue
            p0, p1 = e.ParameterRange
            mid = e.valueAt((p0 + p1) / 2)
            n = _face_normal(f, mid)
            for j in others:
                m = _face_normal(shape.Faces[j], mid)
                if n[0] * m[0] + n[1] * m[1] + n[2] * m[2] >= lim:
                    seen.add(j)
                    todo.append(j)
    return {f"Face{k + 1}" for k in seen}


def texture_mask(s, a):
    """By Surface for one layer: Exclude / Include Only, faces added or
    removed (names, queries with Agent Link, or the picked ones), Add by…,
    Fill, Clear All. Lights the faces in the sidebar's list live."""
    _watch()
    d = live.draft()
    i = _layer(d, a)
    o = _part(s, None, d)
    shape = getattr(o, "Shape", None)
    mode = a.get("mode")
    with d.batch():
        if mode is not None:
            if mode not in ("include", "exclude"):
                raise ToolError("invalid_input", "mode: include (texture only these faces) or exclude (all but them).")
            d.set("includeOnly", mode == "include", layer=i)
        if a.get("clear"):
            d.mask_clear(layer=i)
        add = a.get("add")
        if add is not None:
            if add == "selected":
                sel = _gui("add: selected").selected_faces()
                faces = set((sel or (None, []))[1])
                if not faces:
                    raise ToolError("invalid_input", "add: selected: no faces are picked in the 3D view; name them instead "
                                    "(add: [\"Face3\"]).")
            else:
                if shape is None:
                    raise ToolError("invalid_input", "add: a Mesh part has no faces to name; use triangles, "
                                    "fill { triangles, maxAngle } or paint.")
                faces = pick_faces(shape, add)
            d.mask_add(faces=sorted(faces), layer=i)
        if a.get("triangles") is not None:
            tl = a["triangles"]
            n = len(live.mesh_for(o)[0]) // 9
            if not isinstance(tl, list) or any(isinstance(t, bool) or not isinstance(t, int) or not 0 <= t < n for t in tl):
                raise ToolError("invalid_input", f"triangles: a list of indices 0 to {n - 1} of the part's tessellation "
                                "(texture_draft answers mesh.triangles; a paint hit's triangle is one).")
            d.mask_add(triangles=tl, layer=i)
        if a.get("remove") is not None:
            if not getattr(shape, "Faces", None):
                raise ToolError("invalid_input", "remove: a Mesh part has no faces to name; use paint { erase: true }, "
                                "fill { triangles, erase: true } or clear.")
            d.mask_remove(faces=sorted(pick_faces(shape, a["remove"])), layer=i)
        if a.get("add_by") is not None:
            if not getattr(shape, "Faces", None):
                raise ToolError("invalid_input", "add_by: a Mesh part has no faces; use fill { triangles, maxAngle } or paint.")
            ref = a.get("faces")
            ref = sorted(face_names(shape, ref)) if ref is not None else list(((live.GUI and live.GUI.selected_faces()) or (None, []))[1])
            d.mask_add(faces=sorted(_faces_by(shape, a["add_by"], ref, a)), layer=i)
        if a.get("fill") is not None:
            f = a["fill"] if isinstance(a["fill"], dict) else {"faces": a["fill"]}
            if f.get("triangles") is not None or not getattr(shape, "Faces", None):
                _fill_triangles(d, i, o, f)
                f = None
        if a.get("fill") is not None and f is not None:
            seeds = f.get("faces")
            seeds = sorted(face_names(shape, seeds)) if seeds is not None else list(((live.GUI and live.GUI.selected_faces()) or (None, []))[1])
            if not seeds:
                raise ToolError("invalid_input", "fill: needs a seed face; give fill: {faces: [\"Face3\"], maxAngle: 20}.")
            ang = num(f.get("maxAngle", d.get("bucketThreshold") if "bucketThreshold" in d.ui else 20), "fill.maxAngle")
            d.mask_add(faces=sorted(_flood(shape, seeds, ang)), layer=i)
    before = list(d.mask(i)["faces"])
    strokes = _paint_strokes(d, i, o, a["paint"]) if a.get("paint") is not None else 0
    m = d.mask(i)
    L = d.layer(i)
    counts = mask_counts(m)
    out = {"layer": i, "mode": "include" if L["includeOnly"] else "exclude", **counts, "part": o.Label,
           **({"strokes": strokes} if strokes else {}),
           "note": ("Only these faces get the texture." if L["includeOnly"] else "These faces stay plain.") if m["faces"] or counts["triangles"] else
                   ("Nothing textured yet: add faces." if L["includeOnly"] else "Every face gets the texture.")}
    gone = brushed_off(before, m["faces"]) if strokes else []
    if gone:
        out["facesBrushedOff"] = gone
        out["note"] += (f" The brush took {', '.join(gone)} off the layer's faces"
                        + (f" ({counts['triangles']} of the part's triangles stay masked)" if counts["triangles"] else " and none of their triangles stays masked")
                        + ": mode standard paints or erases whole triangles of the part's tessellation (a flat face of a solid is often two), "
                          "mode precision splits them under the brush's rim.")
    return _follow_preview(out)


def brushed_off(before, after):
    """The faces a brush stroke took off a layer's face list, in order."""
    now = set(after)
    return [f for f in before if f not in now]


def _fill_triangles(d, i, o, f):
    """Fill on the triangles (a Mesh part, or fill.triangles): BumpMesh's
    bucket fill through the engine's Mask.flood; erase removes."""
    from . import paint

    seeds = f.get("triangles")
    if not seeds:
        raise ToolError("invalid_input", "fill: a Mesh part is filled from seed triangles: fill: {triangles: [index], maxAngle: 20} "
                        "(texture_draft answers mesh.triangles, the count).")
    pos, ft = live.mesh_for(o)
    srf = paint.surface_for(pos)
    seeds = seeds if isinstance(seeds, list) else [seeds]
    if any(isinstance(t, bool) or not isinstance(t, int) or not 0 <= t < srf.n for t in seeds):
        raise ToolError("invalid_input", f"fill.triangles: indices 0 to {srf.n - 1}.")
    ang = num(f.get("maxAngle", d.get("bucketThreshold")), "fill.maxAngle")
    erase = bool(f.get("erase"))
    paint.commit(d, srf, i, lambda st: [paint.fill(st, t, ang, erase) for t in seeds], ft)


def _paint_strokes(d, i, o, pa):
    """paint: {strokes, size, hardness, type, mode, snap, erase}: BumpMesh's
    brush (paint.py; mode precision refines under the stroke, standard paints
    whole triangles; snap faces paints whole CAD faces, kept by name), each
    stroke one undo step. Checked whole before any paints."""
    from . import paint

    if not isinstance(pa, dict) or not isinstance(pa.get("strokes"), list) or not pa["strokes"]:
        raise ToolError("invalid_input", "paint: {strokes: [[hit, ...], ...]}; a hit is {point: [x, y, z], normal, triangle} "
                        "or [x, y, z] on the part's surface, in mm.")
    mode = pa.get("mode", "precision" if d.get("brushPrecision") else "standard")
    if mode not in paint.Brush.MODES:
        raise ToolError("invalid_input", "paint.mode: precision or standard.")
    kind = pa.get("type", "circle" if d.get("brushIsRadius") else "single")
    if kind not in ("circle", "single"):
        raise ToolError("invalid_input", "paint.type: circle or single.")
    size = num(pa.get("size", d.get("brushSize")), "paint.size")
    hard = num(pa.get("hardness", d.get("brushHardness")), "paint.hardness")
    snap = pa.get("snap", "faces" if d.get("brushFaceSnap") else "off")
    if snap not in ("faces", "off", None):
        raise ToolError("invalid_input", "paint.snap: faces (every CAD face the brush touches, whole) or off.")
    snap = "faces" if snap == "faces" else None
    pos, ft = live.mesh_for(o)
    if snap and not ft:
        raise ToolError("invalid_input", "paint.snap faces needs a part with faces; this one is a mesh (use the brush without snap).")
    srf = paint.surface_for(pos)
    try:
        brush = paint.Brush(kind == "circle", size, hard, mode, snap)
        strokes = []
        for st in pa["strokes"]:
            if not isinstance(st, list) or not st:
                raise ValueError("a stroke is a list of hits")
            strokes.append([paint.parse_hit(srf, h) for h in st])
    except (ValueError, TypeError) as e:
        raise ToolError("invalid_input", f"paint: {e}.")
    erase = bool(pa.get("erase"))
    for st in strokes:
        paint.commit(d, srf, i, lambda state, st=st: paint.apply_stroke(state, st, brush, erase, ft), ft)
    return len(strokes)


def texture_layer(s, a):
    """The layer strip: add (BumpMesh's Add layer: a copy, Include Only),
    remove, move up / down, solo, visible, Cover / Add, rename, make active."""
    _watch()
    d = live.draft()
    act = a.get("action")
    if act == "add":
        j = d.add_layer()
        if j is None:
            raise ToolError("invalid_state", "The draft already has 4 layers (BumpMesh's limit): texture_layer { action: "
                            "\"remove\", layer } first, or texture_apply { bake: true } to bake what is there and start fresh.")
    else:
        i = _layer(d, a)
        v = a.get("value")
        if act == "remove":
            if not d.remove_layer(i):
                raise ToolError("invalid_state", "The last layer stays (a draft has at least one); texture_draft { reset: true } "
                                "puts it back to BumpMesh's defaults.")
        elif act == "move":
            if not d.move_layer(i, int(num(a.get("delta", v if v is not None else -1), "delta"))):
                raise ToolError("invalid_input", f"move: layer {i} cannot go there; delta -1 moves it up the strip, +1 down, "
                                f"within 0 to {len(d.layers) - 1}.")
        elif act in ("solo", "visible"):
            d.set_layer(i, **{act: bool(True if v is None else v)})
        elif act == "blend":
            if v not in ("cover", "add"):
                raise ToolError("invalid_input", "blend: value \"cover\" (replaces the layers below where it applies) or \"add\" (adds to them).")
            if i == 0 and v == "add":
                raise ToolError("invalid_input", "blend: layer 0 is the bottom layer and always covers (BumpMesh); "
                                "set add on a layer above it.")
            d.set_layer(i, blendAdd=v == "add")
        elif act == "name":
            d.set_layer(i, name=str(v or ""))
        elif act == "active":
            d.set_active(i)
        else:
            raise ToolError("invalid_input", "action: add, remove, move, solo, visible, blend, name or active.")
    out = {"draft": summary(d, s), "layer": j if act == "add" else d.active if act == "active" else i,
           "layerCount": len(d.layers), **_part_word(s, d)}
    return _follow_preview(out)


def texture_preview(s, a):
    """Build or refresh the CPU preview (the same engine as the export), wait
    for it, and say what it made; Suggest values; Cancel."""
    import time

    from . import engine, preview

    _watch()
    d = live.draft()
    if a.get("cancel"):
        stopped = live.GUI.cancel_preview() if live.GUI is not None else True
        out = {"cancelled": True}
        if stopped is False:
            out["note"] = ("The preview's worker had not stopped within 2 s: it ends at its next check and its result is dropped; "
                           "no timer of the preview is left waiting.")
        home = live.draft_doc()
        t = live.find(home, live.TARGET["object"]) if home is not None else None
        return _view_facts(home, out, getattr(t, "Name", None), d.part)
    if "turntable" in a:
        return _turntable(a["turntable"])
    o = _part(s, None, d)
    with timing.stage("texture_preview mesh_for (tessellation, cached by shape)"):
        pos, ft = live.mesh_for(o)
    if a.get("suggest"):
        run = run_settings(d, ft)
        L = run["layers"][d.active] if d.active < len(run["layers"]) else run["layers"][0]
        sug = engine.smart_resolution(pos, {**run, "layers": [L]})
        d.update({"refineLength": sug["edge"], "maxTriangles": sug["maxTriangles"]})
        return {"suggest": {"refineLength": sug["edge"], "maxTriangles": sug["maxTriangles"],
                            "pixelsPerEdge": sug["diagnostics"].get("pixelsPerEdge")}, "draft": summary(d, s)}
    q = a.get("quality") or d.settings.get("previewQuality") or "draft"
    if q not in preview.TRI_BUDGET:
        raise ToolError("invalid_input", f"quality {q!r}: draft (up to 500 000 triangles) or fine (up to 1 500 000).")
    budget = num(a.get("wait", PREVIEW_BUDGET_S), "wait")
    if not 0 <= budget <= PREVIEW_BUDGET_MAX_S:
        raise ToolError("invalid_input", f"wait: 0 to {PREVIEW_BUDGET_MAX_S:g} seconds (the call answers within the client's time; "
                        "the preview goes on building past it and shows itself when it is done).")
    if live.GUI is not None and hasattr(live.GUI, "build_preview") and _on_gui():
        if q != d.settings.get("previewQuality"):
            d.update({"previewQuality": q})   # the sidebar's Quality shows what is built
        got = live.GUI.build_preview(budget)
        if got is not None:
            return _preview_answer(s, d, o, pos, ft, q, got)
    t0 = time.time()
    res = engine.preview({"draft": d.to_dict(), "tri_budget": preview.TRI_BUDGET[q], "memory_limit": preview.MEMORY_LIMIT,
                          "cache_dir": live.cache_dir()}, mesh=pos, face_triangles=ft)
    if res is None:
        raise ToolError("failed", "The preview stopped before it finished (cancelled, or the draft changed under it): call texture_preview again.")
    ms = int((time.time() - t0) * 1000)
    for name, t in res.get("timings") or ():
        timing.report(f"texture_preview {name} (engine)", t)
    timing.report("texture_preview engine (total)", ms)
    m = res["mesh"]
    if live.GUI is not None:
        with timing.stage(f"texture_preview show ({len(m['faces'])} triangles)"):
            live.GUI.show_result(res)
    with timing.stage("texture_preview estimate"):
        est = engine.estimate(pos, run_settings(d, ft))
    out = {"part": o.Label, "quality": q, "previewEdge": round(float(m["edge"]), 4), "triangles": int(len(m["faces"])),
           "vertices": int(len(m["vertices"])), "inputTriangles": len(pos) // 9, "ms": ms,
           "export": {"edge": est["edge"], "triangles": est["outputTriangles"], "memoryMB": round(est["memoryBytes"] / 2 ** 20)},
           "warnings": [w for w in [res.get("note")] if w]}
    finer = preview_finer(out["previewEdge"], est["edge"])
    if finer:
        out["warnings"].append(finer)
    out["next"] = ("Agent Link's view_snapshot shows it; texture_apply adds it to the document." if live.GUI is not None
                   else "texture_apply adds it to the document.")
    return out


# texture_preview waits this long for the preview, then answers with what
# it has (the client gives a call about 80 s; Agent Link's own echo and the
# draw come on top). The build goes on past it and shows itself when done.
PREVIEW_BUDGET_S = 45.0
PREVIEW_BUDGET_MAX_S = 60.0
# texture_set, texture_mask and texture_layer, while the CPU preview shows:
# how long they wait for it to follow the draft before they answer.
FOLLOW_BUDGET_S = 20.0


def _on_gui():
    from . import guithread

    return guithread.on_gui_thread()


def _preview_answer(s, d, o, pos, ft, q, got):
    """texture_preview's answer from the window's build (Window.build_preview):
    what it made, or, past the time budget, that it is still building and
    how to go on."""
    from . import engine

    out = {"part": o.Label, "quality": q, "inputTriangles": len(pos) // 9, "ms": got.get("ms", 0)}
    if got.get("error"):
        raise ToolError("failed", f"The preview failed: {got['error']}. texture_preview {{ suggest: true }} sets a refine length "
                        "and triangle budget the part can take; then call texture_preview again.")
    res = got.get("result")
    if not got.get("done") or not isinstance(res, dict):
        out.update({"building": True, "verdict": "still building",
                    "warnings": [], "next": "texture_preview {} again waits for it and says what it made; texture_preview "
                                            "{ cancel: true } stops it; texture_preview { quality: \"draft\" } builds a coarser one sooner."})
        out["note"] = (f"The preview was not done within {out['ms'] / 1000:.0f} s: it goes on building and shows in the 3D view when it "
                       "is done" + (" (a coarse first look shows now)." if got.get("coarse") else "."))
        return out
    m = res.get("mesh") or {}
    for name, t in res.get("timings") or ():
        timing.report(f"texture_preview {name} (engine)", t)
    est = engine.estimate(pos, run_settings(d, ft))
    out.update({"previewEdge": round(float(m.get("edge") or 0), 4), "triangles": int(len(m.get("faces") or ())),
                "vertices": int(len(m.get("vertices") or ())), "reused": bool(got.get("reused")),
                "export": {"edge": est["edge"], "triangles": est["outputTriangles"], "memoryMB": round(est["memoryBytes"] / 2 ** 20)},
                "warnings": [w for w in [res.get("note")] if w]})
    finer = preview_finer(out["previewEdge"], est["edge"])
    if finer:
        out["warnings"].append(finer)
    out["next"] = "Agent Link's view_snapshot shows it; texture_apply adds it to the document."
    return out


def _follow_preview(out):
    """While the CPU preview shows, it follows the draft before a draft
    tool answers (texture_set, texture_mask, texture_layer: the picture
    after the call showed the old texture, round 4 0015-0019); the shader's
    Quick look follows at once by itself. out["preview"] says how it went."""
    g = live.GUI
    c = getattr(g, "controller", None)
    if g is None or c is None or not hasattr(g, "build_preview") or not _on_gui():
        return out
    if getattr(c, "showing", None) is None or not getattr(c, "cpu_mode", False):
        return out
    got = g.build_preview(FOLLOW_BUDGET_S, show=False)
    if not got:
        return out
    res = got.get("result") if got.get("done") else None
    if isinstance(res, dict):
        out["preview"] = {"triangles": int(len((res.get("mesh") or {}).get("faces") or ())), "ms": got.get("ms", 0)}
    elif got.get("error"):
        out["preview"] = {"error": got["error"]}
    else:
        out["preview"] = {"building": True, "ms": got.get("ms", 0),
                          "note": "The preview is still catching up with the draft; it shows in the 3D view when it is done."}
    return out


def preview_finer(preview_edge, export_edge):
    """A sentence when the preview is refined finer than texture_apply will
    refine: BumpMesh's preview edge is never coarser than the part's largest
    size / 80 (previewPipeline.js choosePreviewEdge), so on a small part
    with a coarse refineLength the picture shows detail the result will not
    have (found live: previewEdge 0.5 against refineLength 0.8). None when
    the preview is as coarse as the export or coarser."""
    try:
        p, e = float(preview_edge), float(export_edge)
    except (TypeError, ValueError):
        return None
    if not p < e * 0.999:
        return None
    return (f"The preview is finer than the result will be: it is refined to {p:g} mm edges, texture_apply to refineLength {e:g} mm, "
            f"so fine detail that shows here may be lost. Set settings.refineLength to {p:g} (texture_set) for a result like this picture.")


def _turntable(v):
    """texture_preview { turntable }: true spins the 3D view about the part's
    vertical axis (the gallery's and the View toolbar's Turntable), false
    stops it; a number of degrees turns the view once by that much, for a
    series of renders (turn, snapshot, turn, ...)."""
    from . import turntable

    tt = turntable.shared()
    if isinstance(v, bool):
        if not v:
            tt.stop()
            return {"turntable": {"spinning": False}}
        if not tt.start():
            raise ToolError("invalid_state", "turntable: no 3D view to spin; it needs FreeCAD's window with the document's 3D view open.")
        return {"turntable": {"spinning": True, "degreesPerSecond": 15},
                "next": "Any click, wheel or SpaceMouse move in the view stops it; turntable false stops it."}
    if isinstance(v, (int, float)) and math.isfinite(float(v)):
        tt.stop()
        if not turntable.turn(float(v)):
            raise ToolError("invalid_state", "turntable: no 3D view to turn; it needs FreeCAD's window with the document's 3D view open.")
        return {"turntable": {"spinning": False, "turned": float(v)},
                "next": "Agent Link's view_snapshot or view_render shows the view; turn again for the next frame."}
    raise ToolError("invalid_input", "turntable: true (spin), false (stop) or degrees to turn the view once.")


def _ask_or_default(s, a, kind, default_name):
    path = a.get("path")
    if path:
        return _local(s, path)
    if _asks() and a.get("ask", True):
        return live.GUI.ask_path(kind, save=True, default=os.path.join(s.out_dir, default_name))
    return os.path.join(s.out_dir, default_name)


def texture_export(s, a):
    """Export STL / 3MF (the Textured object, or the draft run by the
    engine) and Save Project (.bumpmesh, BumpMesh's own format)."""
    from . import engine, project

    fmt = str(a.get("format") or "").lower()
    if fmt not in ("stl", "3mf", "bumpmesh"):
        raise ToolError("invalid_input", "format: stl or 3mf (the textured mesh), or bumpmesh (the draft as a BumpMesh project).")
    d = live.draft()
    doc = _doc(s)
    if fmt == "bumpmesh":
        if not a.get("path") and _asks() and a.get("ask", True):
            opts = live.GUI.ask_save_options()
            if opts is None:
                return {"cancelled": True}
            a = {**a, **opts}
        o = _part(s, None, d) if (d.part and a.get("model", True) is not False) else None
        label = o.Label if o is not None else None
        path = _ask_or_default(s, a, "bumpmesh", project.default_name(label))
        if not path:
            return {"cancelled": True}
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        model, ft = None, {}
        if o is not None and a.get("model", True):
            pos, ft = live.mesh_for(o)
            model = pos.reshape(-1, 3, 3)

        def png(tex):
            try:
                e = engine.library.get(tex)
            except KeyError:
                return None
            if e.get("category") == "custom" and str(e.get("image", "")).lower().endswith(".png"):
                with open(e["image"], "rb") as fh:
                    return fh.read()
            return None

        names = project.save(path, d, model=model, include_model=model is not None, include_texture=a.get("texture", True) is not False,
                             texture_png=png, face_triangles=lambda f: ft.get(f, []))
        return {"path": path, "file": os.path.basename(path), "format": "bumpmesh", "files": names,
                "note": "BumpMesh's Save Project format: Load Project opens it here or in BumpMesh."}
    obj = live.find(doc, a["object"]) if a.get("object") else None
    if a.get("object") and obj is None:
        raise ToolError("not_found", f"object {a['object']!r}: not in this document (Textured objects here: "
                        f"{', '.join(o.Label for o in textured_objects(doc)) or 'none; texture_apply makes one'}).")
    if obj is not None and getattr(obj, "Mesh", None) is None:
        raise ToolError("invalid_input", f"object {a['object']!r}: not a mesh; give a Textured object (texture_apply makes one), "
                        "or leave object out to export the draft's part textured.")
    base = (obj.Label if obj is not None else (_part(s, None, d).Label + "_textured"))
    path = _ask_or_default(s, a, fmt, f"{base}.{fmt}")
    if not path:
        return {"cancelled": True}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if obj is not None:
        from .engine import writers

        pos = live.mesh_positions(obj.Mesh)
        credit = getattr(obj, "Credit", "") or engine.CREDIT
        data = (writers.stl_bytes(pos, header="Texturizer for FreeCAD; BumpMesh (CNC Kitchen) AGPL-3.0") if fmt == "stl"
                else writers.threemf_bytes(pos, {"Application": "Texturizer for FreeCAD", "Copyright": credit}))
        with open(path, "wb") as fh:
            fh.write(data)
        out = {"path": path, "file": os.path.basename(path), "format": fmt, "triangles": len(pos) // 9, "object": obj.Name,
               "label": obj.Label, "credit": credit}
        if fmt == "stl":
            EXPORTED[os.path.realpath(path)] = obj.Name
            out["next"] = f"texture_check {{ object: \"{obj.Name}\" }} (or {{ path }} of this STL) measures it face by face."
        if getattr(obj, "OutOfDate", False):
            out["warnings"] = [f"{obj.Label} is out of date: its body changed (Update first)."]
        return _end_preview(out)
    o = _part(s, None, d)
    pos, ft = live.mesh_for(o)
    run = run_settings(d, ft)
    est = engine.estimate(pos, run)
    if est["memoryBytes"] > (num(a.get("memoryLimitGB", 4), "memoryLimitGB") * (1 << 30)):
        raise ToolError("invalid_state", f"refineLength {est['edge']} mm would make {est['subdividedTriangles']:,} triangles "
                        f"(~{est['memoryBytes'] / 2 ** 30:.1f} GB, over memoryLimitGB {a.get('memoryLimitGB', 4)}): raise it with "
                        "texture_preview { suggest: true } or texture_set { settings: { refineLength } }.")
    e = engine.export(pos, run, None, path, fmt)
    if e is None:
        raise ToolError("failed", "The engine stopped before it finished (cancelled): no file was written; call texture_export again.")
    return _end_preview({"path": e["path"], "file": os.path.basename(e["path"]), "format": fmt, "triangles": e["triangles"],
                         "object": o.Name, "label": o.Label, "warnings": e["warnings"], "credit": e["credit"]})

HANDLERS = {
    "texture_list": texture_list,
    "texture_settings": texture_settings,
    "texture_draft": texture_draft,
    "texture_set": texture_set,
    "texture_mask": texture_mask,
    "texture_layer": texture_layer,
    "texture_preview": texture_preview,
    "texture_apply": texture_apply,
    "texture_export": texture_export,
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
