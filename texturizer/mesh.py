# SPDX-License-Identifier: AGPL-3.0-only
# A body as the mesh a texture tool reads, and the textured mesh read back
# against the body face by face. No agent: plain FreeCAD and numpy.
import json
import os
import struct

from . import meshdist
from .refusal import Refused


def face_triangles(shape, tol):
    """One mesh of the whole part (faces meet edge to edge), face by face:
    the triangles, each wound outward as its face is in the solid, and each
    face's [start, end) among them."""
    import FreeCAD

    shape.tessellate(tol)
    tris, per_face = [], {}
    for i, f in enumerate(shape.Faces):
        pts, ft = f.tessellate(tol)
        if not ft:
            continue
        P = [(p.x, p.y, p.z) for p in pts]
        u0, u1, v0, v1 = f.ParameterRange
        n = f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
        a0, b0, c0 = (FreeCAD.Vector(*P[k]) for k in ft[0])
        flip = (b0 - a0).cross(c0 - a0).dot(n) < 0 and abs((b0 - a0).cross(c0 - a0).Length) > 1e-12
        start = len(tris)
        for t in ft:
            tri = [P[k] for k in t]
            tris.append(tri[::-1] if flip else tri)
        per_face[f"Face{i + 1}"] = [start, len(tris)]
    return tris, per_face


def read_stl(path):
    """An STL's triangles as an (n, 3, 3) array, binary or ASCII."""
    import numpy as np

    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) >= 84:
        n = int.from_bytes(data[80:84], "little")
        if 84 + 50 * n == len(data):
            rec = np.frombuffer(data, dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")], count=n, offset=84)
            return rec["v"].astype(float)
    words = data.decode("ascii", "replace").split()
    vals = []
    for k, w in enumerate(words):
        if w == "vertex":
            vals += [float(words[k + 1]), float(words[k + 2]), float(words[k + 3])]
    return np.array(vals, float).reshape(-1, 3, 3)


def write_stl(path, tris, header=b"Texturizer for FreeCAD"):
    """Binary STL of tris ([[p0, p1, p2], …]), normals from the winding."""
    with open(path, "wb") as fh:
        fh.write(header[:80].ljust(80, b" "))
        fh.write(struct.pack("<I", len(tris)))
        for t in tris:
            (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
            ux, uy, uz, vx, vy, vz = bx - ax, by - ay, bz - az, cx - ax, cy - ay, cz - az
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            ln = (nx * nx + ny * ny + nz * nz) ** 0.5
            if ln > 0:
                nx, ny, nz = nx / ln, ny / ln, nz / ln
            fh.write(struct.pack("<12fH", nx, ny, nz, *t[0], *t[1], *t[2], 0))


def prepare(shape, faces, mode, tol, stl, label=""):
    """The part ready for a texture: stl written face by face from one mesh,
    and beside it <stl>-mask.json with the triangles of faces (include: only
    them; exclude: all but them). Answers what was written."""
    if mode not in ("include", "exclude"):
        raise Refused("mode: include (texture only these faces) or exclude (texture all but these).")
    tris, per_face = face_triangles(shape, tol)
    want = sorted(set(faces))
    mask = [k for name in want if name in per_face for k in range(*per_face[name])]
    write_stl(stl, tris, b"Texturizer for FreeCAD texture_prepare")
    mask_path = os.path.splitext(stl)[0] + "-mask.json"
    with open(mask_path, "w", encoding="utf-8") as fh:
        json.dump({"triangles": mask, "stlTriangles": len(tris), "mode": mode, "faces": want, "body": label}, fh)
    return {"stl": stl, "triangles": len(tris), "mask": mask_path, "maskTriangles": len(mask), "mode": mode, "faces": want}


def check(shape, path, mask_path, tol=0.02, reach=None, flat=0.01, label=""):
    """A textured STL measured against the body face by face: how deep the
    texture stands on the faces it was for, whether it leaked onto any
    other, and whether the mesh stayed closed. Changes nothing."""
    import numpy as np

    if not path or not os.path.isfile(path):
        raise Refused(f"path {path!r}: the textured STL.", "not_found")
    if not os.path.isfile(mask_path):
        raise Refused(f"mask {mask_path!r}: the -mask.json texture_prepare wrote (give mask).", "not_found")
    with open(mask_path, encoding="utf-8") as fh:
        m = json.load(fh)
    tris, per_face = face_triangles(shape, tol)
    if len(tris) != m.get("stlTriangles"):
        raise Refused(f"{label} tessellates to {len(tris)} triangles, the mask was made from {m.get('stlTriangles')}: the body changed since texture_prepare, or a different tolerance — give the one texture_prepare used.", "invalid_state")
    T = np.array(tris, float)
    Vb = T.reshape(-1, 3)
    Fb = np.arange(len(Vb)).reshape(-1, 3)
    face_of = np.empty(len(T), dtype=object)
    names = sorted(per_face, key=lambda k: per_face[k][0])
    for name in names:
        face_of[per_face[name][0]:per_face[name][1]] = name
    listed = set(m.get("faces") or [])
    textured = listed if m.get("mode", "include") == "include" else set(names) - listed
    is_tex = np.array([f in textured for f in face_of])
    Tx = read_stl(path)
    if not len(Tx):
        raise Refused(f"{path}: no triangles.", "invalid_state")
    P, inv = np.unique(np.round(Tx.reshape(-1, 3), 5), axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    Ft = inv.reshape(-1, 3)
    bb = shape.BoundBox
    reach = float(reach or max(2.0, 0.02 * bb.DiagonalLength))
    best, dist, signed = meshdist.nearest(P, Vb, Fb, reach)
    found = best >= 0
    near_tex = np.zeros(len(P), bool)
    if is_tex.any():
        onto = found & is_tex[np.maximum(best, 0)]
        depth = float(np.percentile(np.abs(signed[onto]), 99)) if onto.any() else 0.0
        # A displaced point by a corner can lie nearer the next face: it is
        # the texture's while it is within its depth of a textured face.
        sub = np.nonzero(is_tex)[0]
        b2, _d2, _s2 = meshdist.nearest(P, Vb, Fb[sub], depth + flat)
        near_tex = b2 >= 0
    else:
        depth = 0.0
    faces = {}
    for name in names:
        sel = found & (face_of[np.maximum(best, 0)] == name)
        if not sel.any():
            continue
        d = signed[sel]
        moved = np.abs(d) > flat
        row = {"points": int(sel.sum()), "moved": round(float(moved.mean()), 3)}
        if moved.any():
            row.update(mean=round(float(d.mean()), 3), p95=round(float(np.percentile(np.abs(d), 95)), 3), out=round(float(d.max()), 3), into=round(float(d.min()), 3))
        faces[name] = row
    tex_rows = {k: v for k, v in faces.items() if k in textured}
    leak = found & ~is_tex[np.maximum(best, 0)] & (np.abs(signed) > flat) & ~near_tex
    leaked = sorted({face_of[b] for b in best[leak]})
    # The mesh closed: every edge on two triangles.
    e = np.sort(np.concatenate([Ft[:, [0, 1]], Ft[:, [1, 2]], Ft[:, [2, 0]]]), axis=1)
    _u, counts = np.unique(e, axis=0, return_counts=True)
    open_edges = int((counts == 1).sum())
    bad_edges = int((counts > 2).sum())
    bare = [k for k in textured if k in per_face and faces.get(k, {}).get("moved", 0) < 0.2]
    ok = not leaked and not bare and not open_edges and not bad_edges and int((~found).sum()) == 0
    out = {"body": label, "file": path, "verdict": "ok" if ok else "check", "triangles": int(len(Tx)), "points": int(len(P)),
           "texturedFaces": tex_rows, "depth": round(depth, 3)}
    if leaked:
        out["leaked"] = {"faces": leaked, "points": int(leak.sum()), "worst": round(float(np.abs(signed[leak]).max()), 3)}
    if bare:
        out["bare"] = sorted(bare)
    if (~found).any():
        out["far"] = {"points": int((~found).sum()), "reach": round(reach, 3), "note": "Farther than reach from the body: a texture deeper than reach (give reach), or the STL is not of this body."}
    if open_edges or bad_edges:
        out["mesh"] = {"openEdges": open_edges, "overSharedEdges": bad_edges, "note": "Not closed: a slicer may refuse it or fill it wrong — repair before printing."}
    notes = []
    if leaked:
        notes.append(f"The texture reached {', '.join(leaked)}, which were to stay plain: check the mask (mode, faces) and run the texture again.")
    if bare:
        notes.append(f"{', '.join(sorted(bare))} were to be textured but under a fifth of their points moved: a texture too fine for refineLength, or a mask that missed them.")
    if ok:
        notes.append(f"Textured on {', '.join(sorted(tex_rows))} only, {depth:.3g} mm deep at most; the mesh is closed.")
    out["note"] = " ".join(notes)
    return out
