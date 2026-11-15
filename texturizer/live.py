# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The one texturing session of this FreeCAD window, shared by the sidebar,
the gallery, the commands and the agent's texture_* tools:

    draft()            the single draft.Draft every one of them edits, so a
                       texture_set moves the sidebar's sliders live
    mesh_for(part)     (positions, {face name: [triangle indices]}) of a
                       document object: a B-rep body tessellated face by face
                       (mesh.face_triangles), or a Mesh object's facets
    adapter(shape)     the same for one Part shape (no document)
    engine()           BumpMesh's engine with mesh_for wired in
    list_textures()    the engine's library (BumpMesh's presets we ship, and
                       your own maps from the user data folder)
    signature(obj)     a short geometry fingerprint: a Textured object is out
                       of date when its SourceBody's fingerprint changes
    GUI                the window side (window.Window) when FreeCAD's GUI runs,
                       else None: opens the sidebar, asks for files, shows a
                       preview. Headless, the tools run without it.

No Qt import here, and FreeCAD only inside the functions that need a document.
"""
import os

from . import draft as _draft
from . import mesh as _mesh

TOLERANCE = 0.02  # mm: the tessellation the tools and the preview share
GUI = None
TARGET = {"object": None}  # the Textured object the draft updates (Add to document / Update)
_DRAFT = None
_CACHE = {}


def draft():
    global _DRAFT
    if _DRAFT is None:
        _DRAFT = _draft.Draft()
    return _DRAFT


def reset():
    """A fresh session (tests; a new document)."""
    global _DRAFT
    _DRAFT = None
    _CACHE.clear()
    TARGET["object"] = None


# -- the part's mesh --------------------------------------------------------------
def adapter(shape, tol=TOLERANCE):
    """A B-rep shape as the engine takes it: float32 positions (9 per
    triangle) and each face's triangle indices, from mesh.face_triangles."""
    import numpy as np

    tris, per_face = _mesh.face_triangles(shape, tol)
    pos = np.asarray(tris, np.float32).reshape(-1)
    return pos, {name: list(range(a, b)) for name, (a, b) in per_face.items()}


def mesh_positions(m):
    """A Mesh.Mesh's facets as float32 positions."""
    import numpy as np

    pts, faces = m.Topology
    v = np.asarray([(p.x, p.y, p.z) for p in pts], np.float64).reshape(-1, 3)
    f = np.asarray(faces, np.int64).reshape(-1, 3)
    return v[f.reshape(-1)].astype(np.float32).reshape(-1)


def signature(obj):
    """Faces, volume, area and box, rounded: changes when the geometry does."""
    try:
        if getattr(obj, "Mesh", None) is not None and not getattr(obj, "Shape", None):
            m = obj.Mesh
            bb = m.BoundBox
            vals = [m.CountFacets, m.Volume, m.Area]
        else:
            sh = obj.Shape
            bb = sh.BoundBox
            vals = [len(sh.Faces), sh.Volume, sh.Area]
        vals += [bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax]
        return ";".join(f"{float(v):.6g}" for v in vals)
    except Exception:  # noqa: BLE001 — no geometry: never equal to a real one
        return ""


def find(doc, ref):
    """A document object by Name or Label (any case)."""
    if doc is None or ref is None:
        return None
    for o in doc.Objects:
        if o.Name == ref or o.Label == ref:
            return o
    low = str(ref).lower()
    for o in doc.Objects:
        if o.Label.lower() == low:
            return o
    return None


def _doc():
    try:
        import FreeCAD

        return FreeCAD.ActiveDocument
    except ImportError:
        return None


def mesh_for(part, doc=None, tol=TOLERANCE):
    """(positions, face_triangles) of the draft's part, cached until its
    geometry changes. A Mesh object has no B-rep faces: {}."""
    obj = part if hasattr(part, "Name") else find(doc or _doc(), part)
    if obj is None:
        raise LookupError(f"part {part!r}: not in the document")
    key = (getattr(getattr(obj, "Document", None), "Name", ""), obj.Name, tol)
    sig = signature(obj)
    hit = _CACHE.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    sh = getattr(obj, "Shape", None)
    if sh is not None and getattr(sh, "Faces", None):
        out = adapter(sh, tol)
    elif getattr(obj, "Mesh", None) is not None:
        out = (mesh_positions(obj.Mesh), {})
    else:
        raise LookupError(f"part {obj.Label!r}: no faces to texture")
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[key] = (sig, out)
    return out


def engine():
    return engine_with(mesh_for)


def engine_with(fn):
    """BumpMesh's engine with its mesh from fn(part) -> (positions, face_triangles)."""
    from . import engine as _engine

    return _engine.Engine(mesh_for=fn)


# -- the library ------------------------------------------------------------------
def user_dir():
    d = os.environ.get("TEXTURIZER_HOME")
    if d:
        return d
    try:
        import FreeCAD

        return os.path.join(FreeCAD.getUserAppDataDir(), "Texturizer")
    except Exception:  # noqa: BLE001
        return None


def custom_dir():
    d = user_dir()
    return os.path.join(d, "textures") if d else None


def list_textures():
    from .engine import library

    d = custom_dir()
    if d:
        library.add_custom_dir(d)
    return library.list()
