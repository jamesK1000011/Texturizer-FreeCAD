# SPDX-License-Identifier: AGPL-3.0-only
# The texture tools for agents: thin handlers over texturizer.mesh, their
# definitions in data/tools.json (Agent Link's tool-data format).
import os

from agentlink import facepick, hooks
from agentlink.families import picks

from . import mesh

GROUP = "texture"
INFO = "Surface textures (BumpMesh by CNC Kitchen): a body written face by face with a mask of the faces to texture, and the textured mesh checked against the body."
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def tools(session):
    s = session

    def texture_prepare(a):
        o = s.body(a.get("body"))
        q = a.get("faces")
        want = set()
        for k, item in enumerate(q if isinstance(q, list) else [q]):
            item = {"faces": [item]} if isinstance(item, str) else item
            want |= {n for n, _ in facepick.pick(o.Shape, item, f"faces.{k}" if isinstance(q, list) else "faces")}
        tol = picks.num(a.get("tolerance", 0.02), "tolerance")
        stem = (a.get("name") or o.Label).replace(" ", "_")
        stl = a.get("path") or os.path.join(s.out_dir, stem + "-for-texture.stl")
        out = mesh.prepare(o.Shape, want, a.get("mode", "include"), tol, stl, o.Label)
        mode = out["mode"]
        out_path = os.path.splitext(stl)[0].replace("-for-texture", "") + "-textured.stl"
        bb = o.Shape.BoundBox
        return {
            "body": o.Label, **out,
            "next": {"tool": "bumpmesh_texture_file", "arguments": {"path": stl, "output": out_path, "layers": [{"texture": "a library name (bumpmesh_list_textures)", "settings": {"textureHeight": 0.5, "scaleU": 5},
                     "mask": {"mode": mode, "trianglesFile": out["mask"]}}], "settings": {"refineLength": round(max(0.05, min(5, bb.DiagonalLength / 250)), 2)}}},
            "note": f"{'Only' if mode == 'include' else 'All but'} {', '.join(out['faces'])} get the texture; say the faces that must not change back to the person first. Then texture_check {{ body: \"{o.Label}\", path: <the output> }}: the depth on each textured face, any face it leaked onto, and whether the mesh is closed.",
        }

    def texture_check(a):
        o = s.body(a.get("body"))
        path = a.get("path")
        mask = a.get("mask") or (os.path.splitext(path)[0].replace("-textured", "") + "-for-texture-mask.json" if path else "")
        return mesh.check(o.Shape, path, mask, picks.num(a.get("tolerance", 0.02), "tolerance"), a.get("reach"), picks.num(a.get("flat", 0.01), "flat"), o.Label)

    return hooks.tools_from_data(DATA, GROUP, {"texture_prepare": texture_prepare, "texture_check": texture_check}, "Texturizer")
