# SPDX-License-Identifier: AGPL-3.0-only
# Port of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0,
# github.com/CNCKitchen/stlTexturizer (engine/ and the js/ modules it uses).
"""BumpMesh's texturing engine in Python and numpy: no browser, Node or server.

Each module mirrors one of BumpMesh's JS files and keeps its setting names
and mapping codes (0 planar XY, 1 planar XZ, 2 planar YZ, 3 cylindrical,
4 spherical, 5 triplanar, 6 cubic, 7 surface normal, 8 outline wrap). This
file is the interface the add-on, its tools and its GUI use:

    library.list() / library.load(id)        the texture library
    estimate(mesh, settings)                 triangles, edge, memory, before running
    smart_resolution(mesh, settings)         BumpMesh's "Suggest values"
    fit_cylinder(mesh)                       the cylinder axis auto-fit
    texture(mesh, settings, mask, ...)       the printable textured mesh
    export(mesh, settings, mask, path, fmt)  STL or 3MF written
    Pipeline(mesh, settings, mask)           the same, stage by stage with reusable results
    preview(stage_inputs, progress, should_abort)  the staged CPU preview (GUI contract)
    from_triangles, flood, by_normal, by_box masks (exclusion.Mask)

A mesh is float32 positions (9 per triangle, or (n, 3, 3)), a (vertices,
faces) pair, or a dict with "positions" or "vertices" and "faces". Settings
are BumpMesh's (index.DEFAULTS); "layers" (up to 4, bottom first) each
{"texture", "settings", "mask", "blendAdd", "visible"}, or "texture" for one
layer; "frame": {"rotation": [x, y, z]} degrees, applied as BumpMesh's
Rotate (three.js Euler 'XYZ') and undone on the result. No FreeCAD import.
"""
import math
import threading

import numpy as np

from . import library
from ._js import F32
from .exclusion import Mask, build_adjacency, by_box, by_normal, flood, from_triangles
from .exportpipeline import Aborted, clamp_below_bottom, stage_displace, stage_finish, stage_subdivide
from .index import (DEFAULTS, ENGINE_VERSION, GLOBAL_KEYS, LAYER_KEYS, MAPPING_MODES, MAX_LAYERS, bounds_of,
                    finish_result, prepare_input, staged_progress)
from .maps import prepare_map
from .regularize import regularize_mesh
from .smartresolution import smart_resolution as _smart
from .subdivision import subdivide
from .subdivisionestimate import choose_preview_edge, compute_tri_edges, simulate_from_edges
from .writers import stl_bytes, threemf_bytes

CREDIT = "BumpMesh by Stefan Hermann (CNC Kitchen), github.com/CNCKitchen/stlTexturizer, AGPL-3.0"
STAGES = ("subdivide", "map", "displace", "colours")
BYTES_PER_TRIANGLE = 420  # peak working memory per subdivided triangle (measured on the port, rounded up)
PREVIEW_MIN_EDGE = 0.1
TEAL = np.array([0.22, 0.68, 0.68], F32)
MASK_COLOUR = np.array([0.85, 0.40, 0.15], F32)
GREY = np.array([0.55, 0.57, 0.59], F32)

__all__ = ["library", "Mask", "from_triangles", "flood", "by_normal", "by_box", "build_adjacency", "estimate",
           "smart_resolution", "fit_cylinder", "texture", "printable", "export", "Pipeline", "preview", "Engine", "DEFAULTS",
           "LAYER_KEYS", "GLOBAL_KEYS", "MAPPING_MODES", "MAX_LAYERS", "ENGINE_VERSION", "CREDIT", "STAGES"]


# ── inputs ────────────────────────────────────────────────────────────────

def as_positions(mesh):
    """Non-indexed float32 positions, 9 per triangle."""
    if isinstance(mesh, dict):
        if mesh.get("positions") is not None:
            return as_positions(mesh["positions"])
        return as_positions((mesh["vertices"], mesh["faces"]))
    if isinstance(mesh, (tuple, list)) and len(mesh) == 2:
        v = np.asarray(mesh[0], np.float64).reshape(-1, 3)
        f = np.asarray(mesh[1], np.int64).reshape(-1, 3)
        return v[f.reshape(-1)].astype(F32).reshape(-1)
    p = np.asarray(mesh, F32).reshape(-1)
    if len(p) % 9:
        raise ValueError("A mesh is whole triangles: 9 floats each.")
    return p


def frame_matrix(rotation):
    """BumpMesh's Rotate X / Y / Z (degrees): three.js Euler 'XYZ', R = Rx·Ry·Rz,
    i.e. about Z first, then Y, then X (fixed axes)."""
    x, y, z = (math.radians(float(a or 0)) for a in (rotation or (0, 0, 0)))
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rx @ Ry @ Rz


def _rotation_of(settings):
    fr = (settings or {}).get("frame") or {}
    rot = fr.get("rotation")
    if rot is None:
        rot = [(settings or {}).get(k, 0) for k in ("rotateX", "rotateY", "rotateZ")]
    return None if not any(rot) else frame_matrix(rot)


def _rotate(p, R, inverse=False):
    if R is None or p is None:
        return p
    M = R.T if inverse else R
    q = np.asarray(p, F32).reshape(-1, 3).astype(np.float64) @ M.T
    return q.astype(F32).reshape(-1)


def _mask_dict(mask, n_tri):
    if mask is None:
        return None
    if isinstance(mask, Mask):
        return mask.as_dict()
    if isinstance(mask, dict):
        if mask.get("paint") is not None:
            return {"mode": mask.get("mode", "exclude"), "paint": np.asarray(mask["paint"], F32)}
        tris = mask.get("triangles") or []
        mode = mask.get("mode") or ("include" if mask.get("includeOnly") else "exclude")
        soft = mask.get("soft")
        split = mask.get("split")
        if not len(tris) and mode == "exclude" and not soft and not split:
            return None
        m = from_triangles(n_tri, tris, mode).as_dict()
        if soft:  # the soft brush's coverage per corner (paint.py, BumpMesh's softMask)
            c = np.asarray(soft["corners"], np.int64)
            ok = (c >= 0) & (c < n_tri * 3)
            np.maximum.at(m["paint"], c[ok], np.asarray(soft["values"], F32)[ok])
        if split:  # the Precision brush's split triangles: sampled on the refined mesh (paint.sample_split)
            from ..paint import split_marker

            pm = m["paint"].reshape(-1, 3)
            sm = split_marker(mask, n_tri)
            on = (sm > 0).all(axis=1)
            pm[on] = sm[on]
        return m
    return from_triangles(n_tri, mask).as_dict()


def layer_specs(settings, mask=None):
    """[{texture, settings, mask, blendAdd, visible}] from settings (layers or texture)."""
    specs = settings.get("layers")
    if not specs:
        specs = [{"texture": settings.get("texture"), "settings": {k: settings[k] for k in LAYER_KEYS if k in settings},
                  "mask": mask}]
    elif mask is not None and specs[0].get("mask") is None:
        specs = [dict(specs[0], mask=mask)] + list(specs[1:])
    if any(s.get("solo") for s in specs):
        specs = [dict(s, visible=bool(s.get("solo"))) for s in specs]
    if len(specs) > MAX_LAYERS:
        raise ValueError("At most 4 layers.")
    return specs


def load_layers(specs, n_tri, base=None, prepared=True):
    """Engine layers: images loaded (and prepared: smoothing, invert), masks as paint."""
    out = []
    for s in specs:
        if s.get("visible") is False:
            continue
        tex = s.get("texture") or s.get("image")
        if tex is None:
            raise ValueError("A layer has no texture.")
        img = tex if hasattr(tex, "width") else library.load(tex)
        ls = {**(base or {}), **(s.get("settings") or {})}
        if prepared:
            img = prepare_map(img, ls.get("textureSmoothing") or 0, bool(ls.get("invertTexture")))
        m = s.get("mask")
        if isinstance(m, dict) and s.get("includeOnly") and "mode" not in m and m.get("paint") is None:
            m = dict(m, mode="include")
        out.append({"image": img, "settings": s.get("settings") or {}, "mask": _mask_dict(m, n_tri),
                    "blendAdd": bool(s.get("blendAdd")), "visible": True,
                    "split": bool(isinstance(m, dict) and m.get("split") and m.get("paint") is None)})
    return out


# ── before running ────────────────────────────────────────────────────────

def estimate(mesh, settings=None):
    """What a run will make: {"edge", "subdividedTriangles", "outputTriangles", "memoryBytes",
    "safetyCap"} at the settings' refineLength (BumpMesh's subdivisionEstimate)."""
    s = {**DEFAULTS, **(settings or {})}
    pos = as_positions(mesh)
    te = compute_tri_edges(pos)
    n = simulate_from_edges(te, s["refineLength"])
    return {"edge": s["refineLength"], "inputTriangles": len(pos) // 9, "subdividedTriangles": n,
            "outputTriangles": min(n, s["maxTriangles"]), "memoryBytes": n * BYTES_PER_TRIANGLE,
            "safetyCap": n >= 10_000_000}


def smart_resolution(mesh, settings=None, texture=None):
    """BumpMesh's Suggest values: {"edge" (refineLength), "maxTriangles", "diagnostics"}."""
    s = {**DEFAULTS, **(settings or {})}
    specs = layer_specs({**s, **({"texture": texture} if texture else {})})
    L = specs[0]
    img = library.load(L.get("texture"))
    ls = {**s, **(L.get("settings") or {})}
    img = prepare_map(img, ls.get("textureSmoothing") or 0, False)
    pos = _rotate(as_positions(mesh), _rotation_of(s))
    return _smart(pos, ls, img)


def fit_cylinder(mesh, settings=None):
    """main.js autoFitCylinderAxis: least-squares circle (Kasa) through the
    vertices of side faces (|n.z| < 0.5), in the texturing frame:
    {"cylinderCenterX", "cylinderCenterY", "cylinderRadius"}, or None."""
    pos = _rotate(as_positions(mesh), _rotation_of(settings or {}))
    t = pos.reshape(-1, 3, 3).astype(np.float64)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    ln = np.linalg.norm(n, axis=1)
    nz = np.where(ln > 0, n[:, 2] / np.where(ln > 0, ln, 1), 0)
    side = t[np.abs(nz) < 0.5].reshape(-1, 3)
    if len(side) < 10:
        return None
    x, y = side[:, 0], side[:, 1]
    z = x * x + y * y
    M = np.array([[(x * x).sum(), (x * y).sum(), x.sum()], [(x * y).sum(), (y * y).sum(), y.sum()],
                  [x.sum(), y.sum(), len(x)]])
    b = np.array([(x * z).sum(), (y * z).sum(), z.sum()])
    D = np.linalg.det(M)
    if abs(D) < 1e-12:
        return None
    A, B, C = np.linalg.solve(M, b)
    cx, cy = A / 2, B / 2
    r2 = C + cx * cx + cy * cy
    if not np.isfinite(r2) or r2 <= 0:
        return None
    r = math.sqrt(r2)
    bb = bounds_of(pos)
    if r > max(bb["size"][0], bb["size"][1]) * 5 or r < 1e-3:
        return None
    return {"cylinderCenterX": float(cx), "cylinderCenterY": float(cy), "cylinderRadius": r}


# ── running ───────────────────────────────────────────────────────────────

class Pipeline:
    """The export pipeline by stage, results kept: subdivide → map → displace →
    finish (decimate, clamp, repair). rerun(from_stage) drops what follows.
    mode "bake" is BumpMesh's Bake Textures: no decimation, so the result
    keeps faceParentId and bakedTriangles (the output triangles that were
    textured, for Mask just-baked faces)."""

    ORDER = ("map", "subdivide", "displace", "finish")

    def __init__(self, mesh, settings=None, mask=None, progress=None, should_abort=None, mode="export"):
        if mode not in ("export", "bake"):
            raise ValueError("mode: export or bake.")
        self.mode = mode
        self.mesh = as_positions(mesh)
        self.settings = dict(settings or {})
        self.mask = mask
        self.progress = progress
        self.should_abort = should_abort
        self.results = {}

    def rerun(self, stage):
        for s in self.ORDER[self.ORDER.index(stage):]:
            self.results.pop(s, None)

    def _prog(self):
        return staged_progress(self.progress)

    def map(self):
        if "map" not in self.results:
            R = _rotation_of(self.settings)
            specs = layer_specs(self.settings, self.mask)
            mesh, self.flat_parent = self.mesh, None
            flat = _flatten_split(mesh, specs)
            if flat is not None:  # the Precision brush split triangles: its flattened mesh goes in
                mesh, specs, self.flat_parent = flat
            pos = _rotate(mesh, R)
            n = len(pos) // 9
            layers = load_layers(specs, n, base=self.settings)
            glob = {k: v for k, v in self.settings.items() if k in GLOBAL_KEYS or k in LAYER_KEYS}
            self.results["map"] = {"pin": prepare_input(pos, layers, glob), "R": R}
        return self.results["map"]

    def subdivide(self):
        if "subdivide" not in self.results:
            self.results["subdivide"] = stage_subdivide(self.map()["pin"], self._prog(), self.should_abort)
        return self.results["subdivide"]

    def displace(self):
        if "displace" not in self.results:
            self.results["displace"] = stage_displace(self.map()["pin"], self.subdivide(), self._prog(), self.should_abort)
        return self.results["displace"]

    def finish(self):
        if "finish" not in self.results:
            m = self.map()
            fin = stage_finish(m["pin"], self.displace(), self.mode, self._prog(), self.should_abort)
            res = finish_result(m["pin"], self.subdivide(), fin)
            if self.mode == "bake":
                parents = np.asarray(self.subdivide()["faceParentId"], np.int64)
                res["bakedTriangles"] = baked_triangles(m["pin"]["faceWeights"], parents)
                res["faceParentId"] = parents if self.flat_parent is None else self.flat_parent[parents]
            if m["R"] is not None:
                res["positions"] = _rotate(res["positions"], m["R"], inverse=True)
                res["normals"] = _rotate(res["normals"], m["R"], inverse=True)
            self.results["finish"] = res
        return self.results["finish"]

    def run(self):
        """The textured mesh, or None when aborted."""
        try:
            return self.finish()
        except Aborted:
            return None


def _flatten_split(mesh, specs):
    if not any(isinstance(L.get("mask"), dict) and L["mask"].get("split") for L in specs):
        return None
    from ..paint import flatten_masks

    return flatten_masks(as_positions(mesh), specs)


def baked_triangles(face_weights, parents):
    """bakeTextures' new exclusion set: every output triangle whose parent face
    was not excluded (user mask, include mode, angle masking) was textured.
    faceWeights[parent * 3] > 0.99 is the predicate subdivide skips on."""
    parents = np.asarray(parents, np.int64)
    if face_weights is None:
        return np.arange(len(parents), dtype=np.int64)
    w = np.asarray(face_weights).reshape(-1)
    return np.nonzero(~(w[parents * 3] > 0.99))[0].astype(np.int64)


def texture(mesh, settings=None, mask=None, progress=None, should_abort=None, mode="export"):
    """The textured, printable mesh: {"positions", "normals", "triangles", "inputTriangles",
    "warnings", "repair"}; None when aborted. progress(stage, fraction, info).
    mode "bake" skips decimation and adds faceParentId and bakedTriangles."""
    return Pipeline(mesh, settings, mask, progress, should_abort, mode).run()


def credit_of(settings):
    """The credit line an export carries: BumpMesh, plus CC BY textures' attribution."""
    parts = ["Textured with Texturizer for FreeCAD, " + CREDIT]
    for s in layer_specs(settings or {}):
        t = s.get("texture")
        if isinstance(t, str):
            try:
                e = library.get(t)
            except KeyError:
                continue
            if e["credit"] == "hero":
                parts.append(f'{e["name"]}: Hero Patterns by Steve Schoger, CC BY 4.0')
    return "; ".join(parts)


def printable(res):
    """The textured mesh as Texturizer hands it to FreeCAD and to files: the
    engine's (BumpMesh's) result without what FreeCAD's Mesh evaluation
    calls points on edges (MeshEvalPointOnEdge, 0.001 mm): edges shorter
    than that collapsed where the mesh stays manifold, then cap triangles
    split out. BumpMesh's own output has these too, so texture() keeps them
    and stays bit-identical to it. In place, once; returns res, with
    pointsOnEdges: what FreeCAD would still flag."""
    if res is None or res.get("capsFixed") is not None:
        return res
    from .meshrepair import collapse_short_edges, fix_caps, points_on_edges

    pa, short = collapse_short_edges(res["positions"])
    pa, n = fix_caps(pa)
    res["capsFixed"], res["shortEdgesCollapsed"] = n, short
    res["pointsOnEdges"] = points_on_edges(pa)   # what FreeCAD's evaluation would still flag
    n = n + short
    if short:
        res["triangles"] = len(pa) // 9
        if isinstance(res.get("repair"), dict):
            res["repair"] = dict(res["repair"], tris=len(pa) // 9)
    if n:
        T = np.asarray(pa, np.float64).reshape(-1, 3, 3)
        fn = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
        ln = np.linalg.norm(fn, axis=1)
        fn /= np.where(ln > 0, ln, 1)[:, None]
        res["positions"] = pa
        res["normals"] = np.repeat(fn.astype(F32), 3, axis=0).reshape(-1)
    return res


def export(mesh, settings, mask, path, fmt=None, progress=None, should_abort=None, result=None):
    """Texture and write STL or 3MF (fmt from the extension when None):
    {"path", "format", "triangles", "warnings", "repair"}; None when aborted."""
    fmt = (fmt or path.rsplit(".", 1)[-1]).lower()
    if fmt not in ("stl", "3mf"):
        raise ValueError("Export writes stl or 3mf.")
    res = printable(result or texture(mesh, settings, mask, progress, should_abort))
    if res is None:
        return None
    credit = credit_of(settings)
    if fmt == "stl":
        data = stl_bytes(res["positions"], res["normals"], header="Texturizer for FreeCAD; BumpMesh (CNC Kitchen) AGPL-3.0")
    else:
        data = threemf_bytes(res["positions"], {"Application": "Texturizer for FreeCAD", "Copyright": credit})
    with open(path, "wb") as fh:
        fh.write(data)
    return {"path": path, "format": fmt, "triangles": res["triangles"], "inputTriangles": res["inputTriangles"],
            "warnings": res["warnings"], "repair": res["repair"], "credit": credit}


# ── preview (GUI contract) ──────────────────────────────────────────────────

def _draft_settings(draft):
    """BumpMesh settings from a draft dict (draft.to_dict())."""
    s = dict(draft.get("settings") or {})
    s["frame"] = draft.get("frame") or {}
    layers = []
    for L in draft.get("layers") or []:
        layers.append({"texture": L.get("texture"), "settings": dict(L.get("settings") or {}),
                       "mask": dict(L.get("mask") or {}, includeOnly=bool(L.get("includeOnly"))),
                       "includeOnly": bool(L.get("includeOnly")), "blendAdd": bool(L.get("blendAdd")),
                       "visible": L.get("visible", True), "solo": bool(L.get("solo"))})
    s["layers"] = layers
    return s


def _resolve_masks(layers, face_triangles):
    """Draft masks hold face names and triangle indices: face names to triangles."""
    for L in layers:
        m = L.get("mask") or {}
        tris = set(int(t) for t in m.get("triangles") or [])
        for f in m.get("faces") or []:
            tris.update(int(t) for t in (face_triangles or {}).get(f, []))
        L["mask"] = {"triangles": sorted(tris), "includeOnly": bool(L.get("includeOnly")),
                     "mode": "include" if L.get("includeOnly") else "exclude"}
        if m.get("soft"):
            L["mask"]["soft"] = m["soft"]
        if m.get("split"):
            L["mask"]["split"] = m["split"]
    return layers


def _check(should_abort):
    if should_abort and should_abort():
        raise Aborted()


def _preview_edge(pos, s, tri_budget, memory_limit):
    """(edge, predicted triangles, note): previewPipeline.js choosePreviewEdge,
    coarsened further to stay under memory_limit. Kept per input, so a kept
    subdivision is found without simulating the count again."""
    import hashlib

    floor_edge = float(s.get("refineLength") or DEFAULTS["refineLength"])
    h = hashlib.blake2b(np.ascontiguousarray(pos, F32).tobytes(), digest_size=16).hexdigest()
    k = (h, len(pos), repr(floor_edge), int(tri_budget), int(memory_limit))
    hit = _EDGES.get(k)
    if hit is not None:
        return hit
    b = bounds_of(pos)
    max_edge = max(PREVIEW_MIN_EDGE, max(b["size"]) / 80)
    te = compute_tri_edges(pos)
    edge = choose_preview_edge(pos, floor_edge, max_edge, tri_budget, te)
    note = None
    est = simulate_from_edges(te, edge)
    if est * BYTES_PER_TRIANGLE > memory_limit:
        while est * BYTES_PER_TRIANGLE > memory_limit:
            edge *= max(1.05, math.sqrt(est * BYTES_PER_TRIANGLE / memory_limit))
            est = simulate_from_edges(te, edge)
        note = f"Preview coarsened to {edge:.2f} mm edges to stay under {memory_limit / 2 ** 30:.1f} GiB."
    out = (float(edge), int(est), note)
    if len(_EDGES) > 32:
        _EDGES.clear()
    _EDGES[k] = out
    return out


_EDGES = {}
# The coarse first look (a part not yet subdivided): edges COARSE_MUL x the
# preview's, longer still to stay under COARSE_TRIS, and no sliver clean-up
# (regularize, the cold preview's slow step). It is a preview of the preview:
# the full one replaces it.
COARSE_MUL = 3.0
COARSE_TRIS = 20_000


def coarse_edge(pos, edge):
    """The coarse first look's edge for a preview edge."""
    te = compute_tri_edges(pos)
    from .subdivisionestimate import solve_budget_edge

    return float(max(edge * COARSE_MUL, solve_budget_edge(te, COARSE_TRIS, edge * COARSE_MUL)))


def _preview_subdivide(pos, s, layers, tri_budget, memory_limit, prog, should_abort, coarse=False, times=None):
    """The preview's subdivide stage: subdivide, regularize, subdivide again
    (BumpMesh's preview), or with coarse a single coarse subdivide. times, a
    list, gets (stage, ms) for the timing probe."""
    import time as _t

    n = len(pos) // 9
    edge, est, note = _preview_edge(pos, s, tri_budget, memory_limit)
    times = times if times is not None else []
    if coarse:
        edge = coarse_edge(pos, edge)
        est = simulate_from_edges(compute_tri_edges(pos), edge)
        note = None
    prog("subdivide", 0.05)
    t = _t.perf_counter()
    sub = subdivide(pos, edge, lambda p, *_: prog("subdivide", 0.05 + 0.5 * p), None, fast=True,
                    should_abort=should_abort)
    if sub is None:
        raise Aborted()
    times.append(("subdivide1", (_t.perf_counter() - t) * 1000.0))
    parents = sub["faceParentId"]
    if not coarse and s.get("regularizeEnabled", DEFAULTS["regularizeEnabled"]):
        g = {**DEFAULTS, **s}
        from .index import regularize_opts
        t = _t.perf_counter()
        reg = regularize_mesh(sub["position"], parents, edge, regularize_opts(g), should_abort=should_abort)
        if reg is None:
            raise Aborted()
        times.append(("regularize", (_t.perf_counter() - t) * 1000.0))
        _check(should_abort)
        weights = None
        if g.get("preserveUntextured"):
            hard = _excluded_faces(layers, n)
            if hard is not None:
                weights = np.repeat(hard[np.asarray(reg["faceParentId"])].astype(F32), 3)
        prog("subdivide", 0.7)
        t = _t.perf_counter()
        resub = subdivide(reg["position"], edge * g["regularizeSecondPassMul"], None, weights, fast=True,
                          should_abort=should_abort)
        if resub is None:
            raise Aborted()
        times.append(("subdivide2", (_t.perf_counter() - t) * 1000.0))
        parents = np.asarray(reg["faceParentId"])[resub["faceParentId"]].astype(np.int32)
        sub = resub
    prog("subdivide", 1.0)
    return {"positions": sub["position"], "normals": sub["normal"], "excludeWeight": sub.get("excludeWeight"),
            "faceParentId": parents, "edge": edge, "predictedTriangles": est, "note": note, "coarse": bool(coarse)}


# Subdivided meshes kept by what they are made from: the part's triangles,
# the settings the subdivide stage reads, the budget, and (with Preserve
# Untextured) the faces no layer textures. Nothing of a texture, its scale or
# the frame goes in, so switching textures, an Undo of a texture pick or a
# second preview of the same part reuses it instead of subdividing again.
SUBDIVIDED_KEEP = 4
_SUBDIVIDED = {}
_SUBDIVIDE_KEYS = ("refineLength", "preserveUntextured", "maxTriangles", "harvestFlatFaces", "harvestTol")


_SUB_LOCK = threading.Lock()
_SUB_BUSY = {}   # key -> Event: a subdivide of that key running on another thread
LAST_SUBDIVIDE = {}   # the probe's: key hash, hit / miss / waited, the parts of the key


def _subdivided(key, make, cache_dir=None):
    """The kept subdivision for key (in memory, else in cache_dir); else
    made once: a second thread asking for the same key while it is made
    waits for it instead of making it again beside it (two at once split
    the CPU and both take twice as long)."""
    while True:
        with _SUB_LOCK:
            hit = _SUBDIVIDED.get(key)
            if hit is not None:
                _SUBDIVIDED[key] = _SUBDIVIDED.pop(key)   # most recent last
                LAST_SUBDIVIDE.update(how="hit")
                return hit
            busy = _SUB_BUSY.get(key)
            if busy is None:
                busy = _SUB_BUSY[key] = threading.Event()
                mine = True
            else:
                mine = False
        if not mine:
            busy.wait()
            LAST_SUBDIVIDE.update(how="waited")
            continue
        try:
            hit = disk_load(cache_dir, key)
            how = "disk"
            if hit is None:
                hit = make()
                how = "miss"
                if not hit.get("coarse"):
                    disk_save(cache_dir, key, hit)
            with _SUB_LOCK:
                _SUBDIVIDED[key] = hit
                while len(_SUBDIVIDED) > SUBDIVIDED_KEEP:
                    _SUBDIVIDED.pop(next(iter(_SUBDIVIDED)))
            LAST_SUBDIVIDE.update(how=how)
            return hit
        finally:
            with _SUB_LOCK:
                _SUB_BUSY.pop(key, None)
            busy.set()


def _subdivide_key(pos, s, specs, n, tri_budget, memory_limit, coarse=False):
    """What a preview subdivision is made from: the part's triangles, the
    subdivide settings, the edge the budget and memory limit come to (not
    the budget itself: Draft and Fine share one mesh when they reach the
    same edge), with Preserve Untextured the faces no layer textures, and
    whether it is the coarse first look."""
    import hashlib

    g = {**DEFAULTS, **s}
    def norm(v):   # 1 and 1.0 are one setting (a slider's float, the JSON's int after an Undo)
        return repr(float(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else repr(v)

    keep = tuple((k, norm(g.get(k))) for k in sorted(g) if k in _SUBDIVIDE_KEYS or k.startswith("regularize"))
    hard = _excluded_faces(specs, n) if g.get("preserveUntextured") else None
    h = hashlib.blake2b(np.ascontiguousarray(pos, F32).tobytes(), digest_size=16)
    if hard is not None:
        h.update(b"hard")
        h.update(np.ascontiguousarray(hard).tobytes())
    edge = _preview_edge(pos, s, tri_budget, memory_limit)[0]
    key = (h.hexdigest(), len(pos), keep, repr(edge), bool(coarse))
    import hashlib as _h

    LAST_SUBDIVIDE.clear()
    LAST_SUBDIVIDE.update(key=_h.blake2b(repr(key).encode(), digest_size=4).hexdigest(), mesh=key[0][:8],
                          settings=_h.blake2b(repr(keep).encode(), digest_size=4).hexdigest(),
                          hard=hard is not None and int(hard.sum()), budget=int(tri_budget), edge=round(edge, 4),
                          coarse=bool(coarse))
    return key


# Kept on disk too (Engine(cache_dir=...): the user data folder's cache), so
# the first preview of a part already seen, after a restart, is warm. One
# .npz a key, named by its hash, the key stored inside and compared; the
# newest DISK_KEEP kept.
DISK_KEEP = 8
_DISK_FIELDS = ("positions", "normals", "excludeWeight", "faceParentId")


def _key_name(key):
    import hashlib

    return "preview-" + hashlib.blake2b(repr(key).encode(), digest_size=10).hexdigest() + ".npz"


def disk_load(cache_dir, key):
    import os

    if not cache_dir:
        return None
    path = os.path.join(cache_dir, _key_name(key))
    try:
        with np.load(path, allow_pickle=False) as z:
            if str(z["key"]) != repr(key):
                return None
            out = {k: (z[k] if k in z.files else None) for k in _DISK_FIELDS}
            out.update(edge=float(z["edge"]), predictedTriangles=int(z["predictedTriangles"]),
                       note=str(z["note"]) or None, coarse=bool(z["coarse"]))
        os.utime(path)
        return out
    except (OSError, KeyError, ValueError):
        return None


def disk_save(cache_dir, key, sub):
    import os

    if not cache_dir:
        return
    try:
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, _key_name(key))
        arrays = {k: np.asarray(sub[k]) for k in _DISK_FIELDS if sub.get(k) is not None}
        tmp = path + ".tmp.npz"
        np.savez(tmp, key=np.asarray(repr(key)), edge=np.asarray(sub["edge"]),
                 predictedTriangles=np.asarray(sub["predictedTriangles"]), note=np.asarray(sub.get("note") or ""),
                 coarse=np.asarray(bool(sub.get("coarse"))), **arrays)
        os.replace(tmp, path)
        files = sorted((f for f in os.listdir(cache_dir) if f.startswith("preview-") and f.endswith(".npz")),
                       key=lambda f: os.path.getmtime(os.path.join(cache_dir, f)))
        for f in files[:-DISK_KEEP]:
            os.remove(os.path.join(cache_dir, f))
    except OSError:
        pass


def kept(stage_inputs, mesh=None):
    """True when this preview's subdivision is kept (in memory or in the
    disk cache): the full preview is quick and needs no coarse first look.
    Also True when the part is small enough that the full one is as quick
    as a coarse one would be."""
    mesh = mesh if mesh is not None else stage_inputs.get("mesh")
    if mesh is None:
        return False
    pos = as_positions(mesh)
    s = _draft_settings(stage_inputs.get("draft") or {})
    specs = _resolve_masks(layer_specs(s), stage_inputs.get("face_triangles") or
                           (mesh.get("face_triangles") if isinstance(mesh, dict) else None))
    tb, ml = stage_inputs.get("tri_budget") or 500_000, stage_inputs.get("memory_limit") or (1 << 30)
    edge, est, _note = _preview_edge(pos, s, tb, ml)
    if est <= COARSE_TRIS:
        return True
    key = _subdivide_key(pos, s, specs, len(pos) // 9, tb, ml)
    with _SUB_LOCK:
        if key in _SUBDIVIDED:
            return True
    import os

    cd = stage_inputs.get("cache_dir")
    return bool(cd) and os.path.exists(os.path.join(cd, _key_name(key)))


def _excluded_faces(layers, n):
    """main.js _previewExcludedFaces: source faces no visible layer textures (hard masks)."""
    hard = None
    for L in layers:
        if L.get("visible") is False:
            continue
        m = L.get("mask") or {}
        tris = np.asarray(m.get("triangles") or [], np.int64)
        h = np.zeros(n, bool)
        if m.get("mode") == "include":
            h[:] = True
            h[tris] = False
        else:
            h[tris] = True
        hard = h if hard is None else hard & h
    return None if hard is None or not hard.any() else hard.astype(np.uint8)


def preview(stage_inputs, progress=None, should_abort=None, mesh=None, face_triangles=None):
    """The staged CPU preview (GUI design 3.1-3.3). stage_inputs: {"draft", "rerun_from",
    "cache", "tri_budget", "memory_limit"} and the part's mesh as stage_inputs["mesh"]
    (or the mesh argument), face names to triangles in "face_triangles". Optional:
    "coarse" (the quick first look: coarser edges, no sliver clean-up),
    "subdivide_abort" (a should_abort for the subdivide stage alone, so a
    change that reruns from a later stage lets a cold subdivide finish and
    keeps it), "cache_dir" (the subdivision kept on disk). Returns
    {"stages", "mesh": {"vertices", "faces", "normals", "colours", "weights"},
    "note", "coarse", "timings": [(stage, ms)]} or None when aborted."""
    prog = progress or (lambda *a: None)
    try:
        return _preview(stage_inputs, prog, should_abort, mesh, face_triangles)
    except Aborted:
        return None


def _preview(si, prog, should_abort, mesh, face_triangles):
    import time as _t

    T = []   # (stage, ms): the timing probe's stage lines
    tick = [_t.perf_counter()]

    def lap(name):
        now = _t.perf_counter()
        T.append((name, (now - tick[0]) * 1000.0))
        tick[0] = now

    draft = si.get("draft") or {}
    start = si.get("rerun_from") or "subdivide"
    if start not in STAGES:
        raise ValueError(f"rerun_from is one of {STAGES}.")
    coarse = bool(si.get("coarse"))
    cache = dict(si.get("cache") or {})
    if coarse:
        cache = {}   # its own subdivision: nothing of a full run's stages
    mesh = mesh if mesh is not None else si.get("mesh")
    if mesh is None:
        raise ValueError("preview needs the part's mesh (stage_inputs['mesh']).")
    ft = face_triangles if face_triangles is not None else (mesh.get("face_triangles") if isinstance(mesh, dict) else None)
    ft = ft or si.get("face_triangles")
    pos = as_positions(mesh)
    n = len(pos) // 9
    s = _draft_settings(draft)
    specs = _resolve_masks(layer_specs(s), ft)
    ran = {}
    first = STAGES.index(start)
    note = None
    lap("inputs")

    # subdivide: in the part's own frame (rotation and centring are rigid).
    if first <= 0 or "subdivide" not in cache:
        _check(should_abort)
        tb, ml = si.get("tri_budget") or 500_000, si.get("memory_limit") or (1 << 30)
        key = _subdivide_key(pos, s, specs, n, tb, ml, coarse)
        sub_abort = should_abort if coarse else (si.get("subdivide_abort") or should_abort)
        made = []
        hit = _subdivided(key, lambda: _preview_subdivide(pos, s, specs, tb, ml, prog, sub_abort, coarse=coarse, times=made),
                          None if coarse else si.get("cache_dir"))
        if hit is not None:
            prog("subdivide", 1.0)
        T.extend(made)
        lap(f"subdivide total ({LAST_SUBDIVIDE.get('how', '?')}{', coarse' if coarse else ''})")
        ran["subdivide"] = dict(hit)
        first = 0
    sub = ran.get("subdivide") or cache["subdivide"]
    note = sub.get("note")
    _check(should_abort)

    # map: the texturing frame, bounds, layers with masks, stamps, outline wrap.
    if first <= 1 or "map" not in cache:
        prog("map", 0.0)
        R = _rotation_of(s)
        rpos = _rotate(pos, R)
        raw = load_layers(specs, n, base=s, prepared=False)
        visible = [x for x in specs if x.get("visible") is not False]
        glob = {k: v for k, v in s.items() if k in GLOBAL_KEYS}
        pin = prepare_input(rpos, raw, glob)
        origin = pin["origin"]
        spos = _rotate(sub["positions"], R)
        snrm = _rotate(sub["normals"], R)
        if origin:
            spos = (spos.reshape(-1, 3).astype(np.float64) - np.asarray(origin)).astype(F32).reshape(-1)
        ran["map"] = {"pin": pin, "R": R, "positions": spos, "normals": snrm, "bounds": bounds_of(rpos),
                      "layers": [{"texture": x.get("texture"), "settings": x.get("settings") or {}} for x in visible]}
        prog("map", 1.0)
        first = 1
        lap("map")
    mp = ran.get("map") or cache["map"]
    _check(should_abort)

    # displace: effective maps (smoothing, invert), heights, per-vertex layer weights.
    if first <= 2 or "displace" not in cache:
        prog("displace", 0.0)
        pin = dict(mp["pin"])
        maps_out = []
        if pin.get("layers"):
            layers = []
            for L, spec in zip(pin["layers"], mp["layers"]):
                img = prepare_map(L["image"], L["settings"].get("textureSmoothing") or 0, bool(L["settings"].get("invertTexture")))
                layers.append(dict(L, image=img))
                maps_out.append(img.data[:, :, 0].copy())
            pin["layers"] = layers
        else:
            st = pin["settings"]
            img = prepare_map(pin["image"], st.get("textureSmoothing") or 0, bool(st.get("invertTexture")))
            pin["image"] = img
            maps_out.append(img.data[:, :, 0].copy())
        parents = np.asarray(sub["faceParentId"])
        fw = pin.get("faceWeights")
        weights_ex = None if fw is None else np.repeat(np.asarray(fw, F32).reshape(-1, 3)[parents][:, 0], 3)
        # frame: the smoothing kept from the drag's last run is turned, not rerun (displacement._smoothed).
        geom = {"position": mp["positions"], "normal": mp["normals"], "excludeWeight": weights_ex, "frame": mp["R"]}
        collect = {}
        from .displacement import apply_displacement, apply_displacement_layers
        from .softmask import interpolate_from_parents
        bounds = pin["bounds"]
        st = pin["settings"]
        cb = lambda p: prog("displace", 0.1 + 0.85 * p)
        if pin.get("layers"):
            refined = []
            shown = [x for x in specs if x.get("visible") is not False]
            for L, spec in zip(pin["layers"], shown):
                hf = None if L.get("hardFaces") is None else np.asarray(L["hardFaces"], np.uint8)[parents]
                ex = None if L.get("exclude") is None else interpolate_from_parents(geom["position"], parents, pin["positions"], L["exclude"])
                m = spec.get("mask") or {}
                if m.get("split"):  # the Precision brush: its tree sampled per refined corner (_sampledPaint)
                    from ..paint import sample_split

                    got = sample_split(pos, m, m.get("mode") == "include", sub["positions"], parents)
                    if got is not None:
                        rows, sx, sh = got
                        ex = np.zeros(len(parents) * 3, F32) if ex is None else np.array(ex, F32)
                        ex.reshape(-1, 3)[rows] = sx
                        hf = np.zeros(len(parents), np.uint8) if hf is None else np.array(hf, np.uint8)
                        hf[rows] = hf[rows] | sh
                refined.append({"image": L["image"], "settings": L["settings"], "blendAdd": L["blendAdd"],
                                "hardFaces": hf, "stampDepth": L.get("stampDepth"), "exclude": ex})
            # The preview skips the untangling (export only): it keeps the live bar.
            out = apply_displacement_layers(geom, refined, st, bounds, cb, collect, untangled=False)
        else:
            out = apply_displacement(geom, pin["image"], st, bounds, cb, collect, untangled=False)
        _check(should_abort)
        pa = np.asarray(out["position"], F32).copy()
        na = np.asarray(out["normal"], F32).copy()
        if (st.get("bottomAngleLimit") or 0) > 0:
            clamp_below_bottom(pa, na, float(bounds["min"][2]))
        vid = collect["vid"]
        nv = int(vid.max()) + 1 if len(vid) else 0
        W = np.zeros((nv, 4), F32)
        for i, w in enumerate(collect.get("weights", [])[:4]):
            W[:, i] = w
        ran["displace"] = {"positions": pa, "normals": na, "vid": vid, "weights": W, "maps": maps_out}
        prog("displace", 1.0)
        first = 2
        lap("displace")
    dp = ran.get("displace") or cache["displace"]
    _check(should_abort)

    # colours: BumpMesh's preview tint (teal textured, orange masked by the active layer).
    prog("colours", 0.0)
    active = int(draft.get("active", 0) or 0)
    layered = len(mp["layers"]) > 1
    W = dp["weights"]
    a = W[:, min(active, 3)] if W.shape[0] else np.zeros(0, F32)
    other = GREY if layered else MASK_COLOUR
    col = (TEAL[None, :] * a[:, None] + other[None, :] * (1 - a[:, None])).astype(F32)
    ran["colours"] = {"colours": col}
    prog("colours", 1.0)
    lap("colours")

    vid = dp["vid"]
    nv = W.shape[0]
    first_corner = np.full(nv, -1, np.int64)
    first_corner[vid[::-1]] = np.arange(len(vid))[::-1]
    P = dp["positions"].reshape(-1, 3).astype(np.float64)
    if mp["pin"]["origin"]:
        P = P + np.asarray(mp["pin"]["origin"])
    V = P[first_corner].astype(F32)
    # np.add.at(Nn, vid, normals) as three bincounts: the same sums in the same order, faster.
    cn = dp["normals"].reshape(-1, 3).astype(np.float64)
    Nn = np.stack([np.bincount(vid, cn[:, k], nv) for k in range(3)], 1) if nv else np.zeros((0, 3))
    ln = np.linalg.norm(Nn, axis=1)
    Nn = (Nn / np.where(ln == 0, 1, ln)[:, None]).astype(F32)
    R = mp["R"]
    if R is not None:
        V = _rotate(V, R, inverse=True).reshape(-1, 3)
        Nn = _rotate(Nn, R, inverse=True).reshape(-1, 3)
    drawable = {"vertices": V, "faces": vid.reshape(-1, 3).astype(np.int32), "normals": Nn,
                "face_parent": np.asarray(sub["faceParentId"], np.int64),
                "colours": ran["colours"]["colours"], "weights": W, "maps": dp["maps"],
                "bounds": mp["bounds"], "edge": sub["edge"], "coarse": coarse}
    lap("drawable")
    if coarse:
        ran = {}   # nothing of it is reused by the full run
    return {"stages": ran, "mesh": drawable, "note": note, "coarse": coarse, "timings": T}


class Engine:
    """engine.preview(stage_inputs, progress, should_abort) for the GUI's
    PreviewController, with the mesh supplied by mesh_for(part) ->
    (positions, {face name: [triangle indices]}): the engine imports no FreeCAD."""

    def __init__(self, mesh_for=None, cache_dir=None):
        self.mesh_for = mesh_for
        self.cache_dir = cache_dir

    def _inputs(self, stage_inputs):
        si = dict(stage_inputs)
        if self.cache_dir and not si.get("cache_dir"):
            si["cache_dir"] = self.cache_dir
        mesh, ft = si.get("mesh"), si.get("face_triangles")
        if mesh is None and self.mesh_for is not None:
            mesh, ft = self.mesh_for((si.get("draft") or {}).get("part"))
        return si, mesh, ft

    def preview(self, stage_inputs, progress=None, should_abort=None):
        si, mesh, ft = self._inputs(stage_inputs)
        return preview(si, progress, should_abort, mesh, ft)

    def kept(self, stage_inputs):
        """The full preview is quick (its subdivision kept, or a small part):
        the controller skips the coarse first look."""
        si, mesh, ft = self._inputs(stage_inputs)
        si["face_triangles"] = ft
        return kept(si, mesh)

    estimate = staticmethod(estimate)
    export = staticmethod(export)
    list = staticmethod(library.list)
