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
    face's [start, end) among them.

    A face's winding is checked against the face's own normal where its
    largest triangle is (Surface.parameter there), not at the middle of its
    parameter range: on a hole's cylinder the middle is across the axis,
    where the normal points the other way, and the whole wall came out
    flipped (live: 3276 flipped facets, the textured wall pushed into the
    part). Then the whole mesh is made consistent across its shared edges
    and outward (orient_consistently)."""
    import FreeCAD

    shape.tessellate(tol)
    tris, per_face = [], {}
    for i, f in enumerate(shape.Faces):
        pts, ft = f.tessellate(tol)
        if not ft:
            continue
        P = [(p.x, p.y, p.z) for p in pts]
        k, best = 0, -1.0
        for j, t in enumerate(ft):
            a, b, c = (P[x] for x in t)
            e1 = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
            e2 = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
            cr = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
            ar = cr[0] * cr[0] + cr[1] * cr[1] + cr[2] * cr[2]
            if ar > best:
                k, best, ck = j, ar, cr
        a, b, c = (P[x] for x in ft[k])
        n = _normal_near(f, ((a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3, (a[2] + b[2] + c[2]) / 3), FreeCAD)
        flip = best > 1e-24 and (ck[0] * n.x + ck[1] * n.y + ck[2] * n.z) < 0
        start = len(tris)
        for t in ft:
            tri = [P[x] for x in t]
            tris.append(tri[::-1] if flip else tri)
        per_face[f"Face{i + 1}"] = [start, len(tris)]
    if tris:
        tris = orient_consistently(tris)
    return tris, per_face


def _normal_near(f, c, FreeCAD):
    """The face's normal at the point of it nearest c; at the middle of its
    parameter range when the surface can't say (a stand-in face)."""
    try:
        u, v = f.Surface.parameter(FreeCAD.Vector(*c))
        return f.normalAt(u, v)
    except Exception:  # noqa: BLE001
        u0, u1, v0, v1 = f.ParameterRange
        return f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)


def orient_consistently(tris, quant=1e5):
    """tris (n, 3, 3) wound the same way across every shared edge (a
    triangle meets each neighbour on an edge it runs the other way), and
    each closed piece outward (positive volume). Faces wound right stay as
    they are; a wrongly wound face is turned with its piece."""
    import numpy as np

    from .engine.meshindex import weld

    T = np.asarray(tris, np.float64).reshape(-1, 3, 3)
    vid, _n, _f = weld(T.reshape(-1, 3), quant)
    F = vid.reshape(-1, 3)
    n = len(F)
    edges = {}
    for t in range(n):
        a, b, c = F[t].tolist()
        for p, q in ((a, b), (b, c), (c, a)):
            if p != q:
                edges.setdefault((min(p, q), max(p, q)), []).append((t, p < q))
    flip = np.zeros(n, bool)
    seen = np.zeros(n, bool)
    nbrs = [[] for _ in range(n)]
    closed_edge = {}
    for key, uses in edges.items():
        if len(uses) == 2:
            (t1, d1), (t2, d2) = uses
            same = d1 == d2   # both run the edge the same way: one is wound wrong
            nbrs[t1].append((t2, same))
            nbrs[t2].append((t1, same))
        for t, _d in uses:
            closed_edge[t] = closed_edge.get(t, True) and len(uses) == 2
    for seed in range(n):
        if seen[seed]:
            continue
        piece, todo = [seed], [seed]
        seen[seed] = True
        while todo:
            t = todo.pop()
            for o, same in nbrs[t]:
                if not seen[o]:
                    seen[o] = True
                    flip[o] = flip[t] ^ same
                    piece.append(o)
                    todo.append(o)
        piece = np.array(piece)
        if all(closed_edge.get(t, False) for t in piece.tolist()):
            Q = T[piece].copy()
            Q[flip[piece]] = Q[flip[piece]][:, ::-1]
            vol = np.einsum("ij,ij->i", Q[:, 0], np.cross(Q[:, 1], Q[:, 2])).sum()
            if vol < 0:
                flip[piece] = ~flip[piece]
    out = T.copy()
    out[flip] = out[flip][:, ::-1]
    return out.tolist()


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


def check(shape, path, mask, tol=0.02, reach=None, flat=0.01, label=""):
    """A textured STL measured against the body face by face: how deep the
    texture stands on the faces it was for, whether it leaked onto any
    other, and whether the mesh stayed closed. Changes nothing. mask: the
    -mask.json texture_prepare wrote, or a dict {faces, mode} or {textured}
    (the faces meant for texture), with angles {bottom, top} (BumpMesh's
    angle limits: a face they cover whole is to stay plain)."""
    import numpy as np

    if not path or not os.path.isfile(path):
        raise Refused(f"path {path!r}: the textured STL.", "not_found")
    if isinstance(mask, dict):
        m = mask
    else:
        if not os.path.isfile(mask):
            raise Refused(f"mask {mask!r}: the -mask.json texture_prepare wrote (give mask), or give object: the Textured object texture_apply made.", "not_found")
        with open(mask, encoding="utf-8") as fh:
            m = json.load(fh)
    tris, per_face = face_triangles(shape, tol)
    if m.get("stlTriangles") is not None and len(tris) != m.get("stlTriangles"):
        raise Refused(f"{label} tessellates to {len(tris)} triangles, the mask was made from {m.get('stlTriangles')}: the body changed since texture_prepare, or a different tolerance — give the one texture_prepare used.", "invalid_state")
    T = np.array(tris, float)
    Vb = T.reshape(-1, 3)
    Fb = np.arange(len(Vb)).reshape(-1, 3)
    face_of = np.empty(len(T), dtype=object)
    names = sorted(per_face, key=lambda k: per_face[k][0])
    for name in names:
        face_of[per_face[name][0]:per_face[name][1]] = name
    if m.get("textured") is not None:
        textured = set(m["textured"])
    else:
        listed = set(m.get("faces") or [])
        textured = listed if m.get("mode", "include") == "include" else set(names) - listed
    angled = []
    ang = m.get("angles") or {}
    bal, tal = float(ang.get("bottom") or 0), float(ang.get("top") or 0)
    if textured and (bal > 0 or tal > 0):
        # BumpMesh's angle masks (index._angle_masked): a face they cover
        # whole gets no texture.
        n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
        ln = np.linalg.norm(n, axis=1)
        nz = np.where(ln > 1e-12, n[:, 2] / np.where(ln > 1e-12, ln, 1), 0.0)
        a = np.degrees(np.arccos(np.clip(np.abs(nz), 0, 1)))
        off = np.where(nz < 0, (bal > 0) & (a <= bal), (tal > 0) & (a <= tal))
        angled = sorted(f for f in textured if f in per_face and off[per_face[f][0]:per_face[f][1]].all())
        textured -= set(angled)
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
    if angled:
        out["angleMasked"] = angled
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
