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

from . import live, mesh


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
    """A textured STL against its body. The mask: object (a Textured object
    texture_apply made: its stored draft or arguments, its body), mask
    (texture_prepare's -mask.json), the -mask.json beside path by name, or
    the Textured object path was exported from."""
    path = a.get("path")
    tol = num(a.get("tolerance", 0.02), "tolerance")
    doc = _doc(s)
    obj = None
    if a.get("object") is not None:
        if a.get("mask"):
            raise ToolError("invalid_input", "object or mask, not both: a Textured object carries its own mask.")
        obj = live.find(doc, a["object"])
        if obj is None or "SourceBody" not in getattr(obj, "PropertiesList", []):
            raise ToolError("not_found", f"object {a['object']!r}: not a Textured object in this document "
                            f"({', '.join(o.Label for o in textured_objects(doc)) or 'none'}).")
    mask = a.get("mask")
    if obj is None and not mask:
        if not path:
            raise ToolError("invalid_input", "object: the Textured object texture_apply made (or path: its STL).")
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
        out.setdefault("warnings", []).append(f"{obj.Label} is out of date: its body changed (texture_apply {{ update: \"{obj.Name}\" }} first).")
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
        raise ToolError("invalid_state", f"{obj.Label}: made without stored settings; texture its body again.")
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
            raise ToolError("invalid_input", f"layers.{i}.texture: a library id (texture_list).")
        mask, names = None, []
        if L.get("faces") is not None:
            mask, names = _mask_from(o.Shape, per_face, n, L["faces"], L.get("mode") or "exclude")
        elif L.get("mode") == "include":
            raise ToolError("invalid_input", f"layers.{i}.faces: include needs the faces to texture.")
        masked.append({"layer": i + 1, "mode": (L.get("mode") or "exclude") if names else None, "faces": names})
        ls = {**shared, **_numbers(L.get("settings"), f"layers.{i}.settings")}
        layers.append({"texture": _texture_id(L["texture"]), "settings": ls, "mask": mask,
                       "blendAdd": bool(L.get("blendAdd")) and i > 0, "visible": L.get("visible", True) is not False})
    return layers, masked


def _run_engine(s, a, pos, run, mode="export"):
    from . import engine

    est = engine.estimate(pos, run)
    if est["memoryBytes"] > (num(a.get("memoryLimitGB", 4), "memoryLimitGB") * (1 << 30)):
        raise ToolError("invalid_state", f"refineLength {est['edge']} mm would make {est['subdividedTriangles']:,} triangles "
                        f"(~{est['memoryBytes'] / 2 ** 30:.1f} GB): raise settings.refineLength (texture_settings suggests one).")
    res = engine.texture(pos, run, mode=mode)
    if res is None:
        raise ToolError("failed", "Stopped before it finished.")
    return res


def _export_with(s, a, pos, run, res, out):
    from . import engine

    exp = a.get("export")
    if not exp:
        return
    path = exp if isinstance(exp, str) else exp.get("path")
    if not path:
        raise ToolError("invalid_input", "export: a path ending .stl or .3mf.")
    if not os.path.isabs(path):
        path = os.path.join(s.out_dir, path)
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
        raise ToolError("invalid_input", "texture: a library id (texture_list), or layers.")
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
           "warnings": res["warnings"], "layers": masked, "credit": engine.credit_of(run)}
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
        raise ToolError("invalid_state", "Every layer is hidden: show one (texture_layer).")
    bake = bake or bool(getattr(target, "Baked", False))
    res = _run_engine(s, a, pos, run, "bake" if bake else "export")
    name = name or (target.Label if target is not None else f"{o.Label}_textured")
    obj = _add_mesh(s, o, name, res["positions"], engine.credit_of(run), run, target=target,
                    draft_json=json.dumps(d.to_dict()), baked=bake)
    out = {"body": o.Label, "object": obj.Name if obj is not None else None, "label": name,
           "updated": target is not None, "triangles": res["triangles"], "inputTriangles": res["inputTriangles"],
           "repair": res["repair"], "warnings": res["warnings"], "credit": engine.credit_of(run),
           "layers": [{"layer": i + 1, "texture": L["texture"], "mode": "include" if L.get("includeOnly") else "exclude",
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


def texture_apply(s, a):
    """Add to document / Update / Bake Textures: BumpMesh's engine on a body
    (the draft's, or the texture / layers given here). A Textured object keeps
    the settings it was made with, goes out of date when its body changes, and
    runs again only on Update."""
    d = live.draft()
    doc = _doc(s)
    if a.get("update"):
        target = live.find(doc, a["update"])
        if target is None or "SourceBody" not in getattr(target, "PropertiesList", []):
            raise ToolError("not_found", f"update {a['update']!r}: not a Textured object in this document.")
        stored = getattr(target, "TextureDraft", "") or ""
        if stored:
            from .draft import Draft

            dd = Draft.from_json(stored)
            dd.part = target.SourceBody
            _obj, out = _apply_draft(s, a, dd, target=target)
        else:
            import json

            args = json.loads(getattr(target, "TextureArgs", "") or "{}")
            if not args:
                raise ToolError("invalid_state", f"{target.Label}: made without stored settings; texture its body again.")
            args["body"] = target.SourceBody
            out = _apply_args(s, args, target=target)
            out["updated"] = True
        out["outOfDate"] = False
        out["next"] = (f"texture_check {{ object: \"{out['object']}\" }} measures it face by face against its body; "
                       "texture_export for an STL / 3MF.")
        return remember("texture_apply", out)
    if a.get("texture") or a.get("layers") is not None:
        out = _apply_args(s, a)
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
        d.set_part(_part(s, a["body"], d).Name)
    if d.part is None:
        d.set_part(_part(s, None, d).Name)
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
    out["next"] = ("Update (texture_apply { update: \"%s\" }) after the body changes; texture_check { object: \"%s\" } "
                   "measures it face by face; texture_export for an STL / 3MF." % (out["object"] or "", out["object"] or ""))
    return remember("texture_apply", out)


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
        return Mesh.Mesh(tmp)
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


def _add_mesh(s, body, name, positions, credit, run, target=None, draft_json="", args=None, baked=False):
    """The textured mesh as a Mesh::Feature beside the body (or the target
    updated), carrying its credit, the settings it was made with, its body
    and the body's fingerprint."""
    import json

    # No document to put it in (a body that belongs to none) or no FreeCAD
    # Mesh module: no object, the export still made.
    doc = getattr(s, "doc", None) or getattr(body, "Document", None)
    if doc is None and target is None:
        return None
    m = _make_mesh(positions)
    if m is None:
        return None
    doc = doc or target.Document
    obj = target if target is not None else doc.addObject("Mesh::Feature", "Textured")
    obj.Label = name
    obj.Mesh = m
    vals = {"Credit": credit, "TextureSettings": json.dumps(_jsonable(run)), "TextureDraft": draft_json or "",
            "TextureArgs": json.dumps(args) if args else "", "SourceBody": body.Name,
            "SourceSignature": live.signature(body), "OutOfDate": False, "Baked": bool(baked)}
    for prop, kind, doc_text in PROPS:
        if prop not in obj.PropertiesList:
            obj.addProperty(kind, prop, "Texturizer", doc_text)
        setattr(obj, prop, vals[prop])
    doc.recompute()
    return obj


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

    def slotRecomputedObject(self, obj):  # noqa: N802 (FreeCAD)
        try:
            doc = obj.Document
            if any(t.SourceBody == obj.Name for t in textured_objects(doc)):
                mark_out_of_date(doc, obj)
        except Exception:  # noqa: BLE001 — never raise into FreeCAD
            pass


# ── the draft (design 5.1): one Draft, shared with the sidebar ─────────────

def _doc(s):
    return getattr(s, "doc", None) or live._doc()


def _part(s, ref, d=None):
    """The object a draft textures: ref (a body, or a Mesh object), else the
    draft's part, else the one body shown."""
    doc = _doc(s)
    if ref is None and d is not None and d.part:
        o = live.find(doc, d.part)
        if o is not None:
            return o
    if ref is not None:
        o = live.find(doc, ref)
        if o is not None and (getattr(getattr(o, "Shape", None), "Faces", None) or getattr(o, "Mesh", None) is not None):
            return o
    return s.body(ref)


def _watch():
    """The sidebar open in the window, so the draft moves where it can be watched."""
    if live.GUI is not None:
        try:
            live.GUI.open()
        except Exception:  # noqa: BLE001 — a closed window never stops a tool
            pass


def _layer(d, a):
    i = a.get("layer")
    if i is None:
        return d.active
    if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(d.layers):
        raise ToolError("invalid_input", f"layer: 0 to {len(d.layers) - 1} (the draft has {len(d.layers)}).")
    return i


def _gui(what):
    """The window side, or a refusal saying what was needed."""
    if live.GUI is None:
        raise ToolError("invalid_input", f"{what}: needs FreeCAD's window; name it in the arguments instead.")
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

    try:
        res = project.load(path, d, mode=mode)
    except (OSError, ValueError, KeyError) as e:
        raise ToolError("invalid_input", f"project: {e}") from None
    note = []
    if res.get("model") is not None and res["mode"] == "all":
        obj = _add_plain_mesh(s, os.path.splitext(os.path.basename(path))[0], res["model"])
        if obj is not None:
            d.part = obj.Name  # the project's model, in the draft's undo step
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
        ref = _gui("load: selection").selected_object() or ref
    if ref is not None or (d.part is None and not a.get("project")):
        o = _part(s, ref, None)
        stored = getattr(o, "TextureDraft", "") if "SourceBody" in getattr(o, "PropertiesList", []) else ""
        if stored:
            # A Textured object: its settings back in the draft, its body the part.
            data = json.loads(stored)
            data["part"] = o.SourceBody
            d.load(data)
            live.TARGET["object"] = o.Name
        else:
            if o.Name != d.part:
                live.TARGET["object"] = None
            d.set_part(o.Name)
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
        out["project"] = _load_project(s, d, pj, a.get("mode", "all"))
    _watch()
    if d.part:
        pos, ft = live.mesh_for(_part(s, None, d))
        out["mesh"] = {"triangles": len(pos) // 9, "faces": len(ft)}
    out["draft"] = summary(d, s)
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
            raise ToolError("invalid_input", f"{what}: pick a face in the 3D view first, or give face.")
        ref, faces = sel
        o = _part(s, ref, d)
        name = faces[0]
    else:
        o = _part(s, None, d)
    shape = getattr(o, "Shape", None)
    names = face_names(shape, name) if shape is not None else set()
    n = sorted(names)[0] if names else None
    if n is None:
        raise ToolError("not_found", f"{what}: no face {name!r}.")
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
        raise ToolError("not_found", f"texture file {path!r}: not found.")
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
            tex = _custom_texture(path)
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
            c = controls.BY_KEY.get(k)
            if c is None or c["scope"] not in ("layer", "global", "frame") or c["type"] in ("button", "heading", "note"):
                raise ToolError("invalid_input", f"settings.{k}: not a BumpMesh setting (texture_settings lists them).")
            if k == "mappingMode" and isinstance(v, str):
                from . import engine

                if v not in engine.MAPPING_MODES:
                    raise ToolError("invalid_input", f"settings.mappingMode: 0-8 or one of {', '.join(engine.MAPPING_MODES)}.")
                v = engine.MAPPING_MODES[v]
            try:
                done[k] = d.set(k, v, layer=i)
            except (TypeError, ValueError) as e:
                raise ToolError("invalid_input", f"settings.{k}: {e}") from None
        fr = a.get("frame")
        if fr is not None:
            if not isinstance(fr, dict):
                raise ToolError("invalid_input", "frame: {rotation: [x, y, z]} degrees, or {face: \"selected\" | FaceN}.")
            if fr.get("rotation") is not None:
                rot = [num(x, "frame.rotation") for x in fr["rotation"]]
                if len(rot) != 3:
                    raise ToolError("invalid_input", "frame.rotation: [x, y, z] degrees.")
                d.set_frame(rotation=rot)
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
                    raise ToolError("failed", "Auto-fit: no cylinder found in the side faces.")
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
                raise ToolError("invalid_input", "cylinder: autofit, reset or from_face.")
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
                        raise ToolError("invalid_input", "stamp: click the spot on the part first, or give point and normal.")
                    pt, nr = pick
                if nr is None:
                    raise ToolError("invalid_input", "stamp.normal: [x, y, z].")
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
    return {"layer": i, "set": done, "draft": summary(d, s)}


def _faces_by(shape, how, ref_faces, a):
    """Add by…: faces facing the same direction as the picked ones (within
    angle), inside their box, or on the same surface."""
    out = set()
    refs = [shape.Faces[int(n[4:]) - 1] for n in ref_faces]
    if not refs:
        raise ToolError("invalid_input", f"add_by {how}: pick a face first, or give faces.")
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
                    raise ToolError("invalid_input", "add: selected: pick faces in the 3D view first, or name them.")
            else:
                if shape is None:
                    raise ToolError("invalid_input", "add: a Mesh part has no faces to name; use triangles.")
                faces = pick_faces(shape, add)
            d.mask_add(faces=sorted(faces), layer=i)
        if a.get("triangles") is not None:
            d.mask_add(triangles=[int(t) for t in a["triangles"]], layer=i)
        if a.get("remove") is not None:
            faces = pick_faces(shape, a["remove"]) if shape is not None else set()
            d.mask_remove(faces=sorted(faces), layer=i)
        if a.get("add_by") is not None:
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
                raise ToolError("invalid_input", "fill: a seed face (faces), or pick one.")
            ang = num(f.get("maxAngle", d.get("bucketThreshold") if "bucketThreshold" in d.ui else 20), "fill.maxAngle")
            d.mask_add(faces=sorted(_flood(shape, seeds, ang)), layer=i)
    strokes = _paint_strokes(d, i, o, a["paint"]) if a.get("paint") is not None else 0
    m = d.mask(i)
    L = d.layer(i)
    counts = mask_counts(m)
    return {"layer": i, "mode": "include" if L["includeOnly"] else "exclude", **counts,
            **({"strokes": strokes} if strokes else {}),
            "note": ("Only these faces get the texture." if L["includeOnly"] else "These faces stay plain.") if m["faces"] or counts["triangles"] else
                    ("Nothing textured yet: add faces." if L["includeOnly"] else "Every face gets the texture.")}


def _fill_triangles(d, i, o, f):
    """Fill on the triangles (a Mesh part, or fill.triangles): BumpMesh's
    bucket fill through the engine's Mask.flood; erase removes."""
    from . import paint

    seeds = f.get("triangles")
    if not seeds:
        raise ToolError("invalid_input", "fill: seed triangles on a Mesh part ({triangles: [i], maxAngle}).")
    pos, ft = live.mesh_for(o)
    srf = paint.surface_for(pos)
    seeds = seeds if isinstance(seeds, list) else [seeds]
    if any(isinstance(t, bool) or not isinstance(t, int) or not 0 <= t < srf.n for t in seeds):
        raise ToolError("invalid_input", f"fill.triangles: indices 0 to {srf.n - 1}.")
    ang = num(f.get("maxAngle", d.get("bucketThreshold")), "fill.maxAngle")
    erase = bool(f.get("erase"))
    paint.commit(d, srf, i, lambda st: [paint.fill(st, t, ang, erase) for t in seeds], ft)


def _paint_strokes(d, i, o, pa):
    """paint: {strokes, size, hardness, type, mode, erase}: BumpMesh's brush
    (paint.py; mode precision refines under the stroke, standard paints whole
    triangles), each stroke one undo step. Checked whole before any paints."""
    from . import paint

    if not isinstance(pa, dict) or not isinstance(pa.get("strokes"), list) or not pa["strokes"]:
        raise ToolError("invalid_input", "paint: {strokes: [[hit, ...], ...]}, a hit {point, normal, triangle}.")
    mode = pa.get("mode", "precision" if d.get("brushPrecision") else "standard")
    if mode not in paint.Brush.MODES:
        raise ToolError("invalid_input", "paint.mode: precision or standard.")
    kind = pa.get("type", "circle" if d.get("brushIsRadius") else "single")
    if kind not in ("circle", "single"):
        raise ToolError("invalid_input", "paint.type: circle or single.")
    size = num(pa.get("size", d.get("brushSize")), "paint.size")
    hard = num(pa.get("hardness", d.get("brushHardness")), "paint.hardness")
    pos, ft = live.mesh_for(o)
    srf = paint.surface_for(pos)
    try:
        brush = paint.Brush(kind == "circle", size, hard, mode)
        strokes = []
        for st in pa["strokes"]:
            if not isinstance(st, list) or not st:
                raise ValueError("a stroke is a list of hits")
            strokes.append([paint.parse_hit(srf, h) for h in st])
    except (ValueError, TypeError) as e:
        raise ToolError("invalid_input", f"paint: {e}.")
    erase = bool(pa.get("erase"))
    for st in strokes:
        paint.commit(d, srf, i, lambda state, st=st: paint.apply_stroke(state, st, brush, erase), ft)
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
            raise ToolError("invalid_state", "At most 4 layers (BumpMesh's limit).")
    else:
        i = _layer(d, a)
        v = a.get("value")
        if act == "remove":
            if not d.remove_layer(i):
                raise ToolError("invalid_state", "The last layer stays.")
        elif act == "move":
            if not d.move_layer(i, int(num(a.get("delta", v if v is not None else -1), "delta"))):
                raise ToolError("invalid_input", "move: no layer there.")
        elif act in ("solo", "visible"):
            d.set_layer(i, **{act: bool(True if v is None else v)})
        elif act == "blend":
            if v not in ("cover", "add"):
                raise ToolError("invalid_input", "blend: cover or add.")
            if i == 0 and v == "add":
                raise ToolError("invalid_input", "blend: the bottom layer covers (BumpMesh).")
            d.set_layer(i, blendAdd=v == "add")
        elif act == "name":
            d.set_layer(i, name=str(v or ""))
        elif act == "active":
            d.set_active(i)
        else:
            raise ToolError("invalid_input", "action: add, remove, move, solo, visible, blend, name or active.")
    return {"draft": summary(d, s)}


def texture_preview(s, a):
    """Build or refresh the CPU preview (the same engine as the export), wait
    for it, and say what it made; Suggest values; Cancel."""
    import time

    from . import engine, preview

    _watch()
    d = live.draft()
    if a.get("cancel"):
        if live.GUI is not None:
            live.GUI.cancel_preview()
        return {"cancelled": True}
    o = _part(s, None, d)
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
        raise ToolError("invalid_input", "quality: draft (500 k) or fine (1.5 M).")
    t0 = time.time()
    res = engine.preview({"draft": d.to_dict(), "tri_budget": preview.TRI_BUDGET[q], "memory_limit": preview.MEMORY_LIMIT},
                         mesh=pos, face_triangles=ft)
    if res is None:
        raise ToolError("failed", "Stopped before it finished.")
    ms = int((time.time() - t0) * 1000)
    m = res["mesh"]
    if live.GUI is not None:
        live.GUI.show_result(res)
    est = engine.estimate(pos, run_settings(d, ft))
    out = {"part": o.Label, "quality": q, "previewEdge": round(float(m["edge"]), 4), "triangles": int(len(m["faces"])),
           "vertices": int(len(m["vertices"])), "inputTriangles": len(pos) // 9, "ms": ms,
           "export": {"edge": est["edge"], "triangles": est["outputTriangles"], "memoryMB": round(est["memoryBytes"] / 2 ** 20)},
           "warnings": [w for w in [res.get("note")] if w]}
    out["next"] = ("Agent Link's view_snapshot shows it; texture_apply adds it to the document." if live.GUI is not None
                   else "texture_apply adds it to the document.")
    return out


def _ask_or_default(s, a, kind, default_name):
    path = a.get("path")
    if path:
        return path if os.path.isabs(path) else os.path.join(s.out_dir, path)
    if live.GUI is not None and a.get("ask", True):
        return live.GUI.ask_path(kind, save=True, default=os.path.join(s.out_dir, default_name))
    return os.path.join(s.out_dir, default_name)


def texture_export(s, a):
    """Export STL / 3MF (the Textured object, or the draft run by the
    engine) and Save Project (.bumpmesh, BumpMesh's own format)."""
    from . import engine, project

    fmt = str(a.get("format") or "").lower()
    if fmt not in ("stl", "3mf", "bumpmesh"):
        raise ToolError("invalid_input", "format: stl, 3mf or bumpmesh.")
    d = live.draft()
    doc = _doc(s)
    if fmt == "bumpmesh":
        if not a.get("path") and live.GUI is not None and a.get("ask", True):
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
        return {"path": path, "format": "bumpmesh", "files": names,
                "note": "BumpMesh's Save Project format: Load Project opens it here or in BumpMesh."}
    obj = live.find(doc, a["object"]) if a.get("object") else None
    if a.get("object") and obj is None:
        raise ToolError("not_found", f"object {a['object']!r}: not in this document.")
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
        out = {"path": path, "format": fmt, "triangles": len(pos) // 9, "object": obj.Name, "credit": credit}
        if fmt == "stl":
            EXPORTED[os.path.realpath(path)] = obj.Name
            out["next"] = f"texture_check {{ object: \"{obj.Name}\" }} (or {{ path }} of this STL) measures it face by face."
        if getattr(obj, "OutOfDate", False):
            out["warnings"] = [f"{obj.Label} is out of date: its body changed (Update first)."]
        return out
    o = _part(s, None, d)
    pos, ft = live.mesh_for(o)
    run = run_settings(d, ft)
    est = engine.estimate(pos, run)
    if est["memoryBytes"] > (num(a.get("memoryLimitGB", 4), "memoryLimitGB") * (1 << 30)):
        raise ToolError("invalid_state", f"refineLength {est['edge']} mm would make {est['subdividedTriangles']:,} triangles: raise it (texture_preview suggest).")
    e = engine.export(pos, run, None, path, fmt)
    if e is None:
        raise ToolError("failed", "Stopped before it finished.")
    return {"path": e["path"], "format": fmt, "triangles": e["triangles"], "warnings": e["warnings"], "credit": e["credit"]}

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
