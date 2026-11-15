# SPDX-License-Identifier: AGPL-3.0-only
# Texturizer's tool handlers: {tool_name: fn(ctx, args)} for texture_prepare
# and texture_check, over texturizer.mesh (based on BumpMesh by Stefan
# Hermann, CNC Kitchen). ctx is the session the call runs in (Agent Link's,
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
LAST = {"prepared": [], "check": None}
KEEP = 5


def remember(tool, out):
    if tool == "texture_prepare":
        LAST["prepared"] = [out] + [p for p in LAST["prepared"] if p.get("stl") != out.get("stl")][: KEEP - 1]
    elif tool == "texture_check":
        LAST["check"] = out
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


HANDLERS = {
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
