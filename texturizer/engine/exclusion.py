# SPDX-License-Identifier: AGPL-3.0-only
# Port of js/exclusion.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# Face adjacency with dihedral angles (welded on the 0.1 µm grid), the bucket
# fill BumpMesh's Fill tool runs, and the per-corner exclusion weights the
# pipeline reads (1 = left plain). Also the mask a layer carries
# ({mode: exclude | include, paint per corner}) and the ways to make one:
# from a set of triangles, by fill, by facing direction, by box.
import math
from collections import deque

import numpy as np

from ._js import F32
from .meshindex import weld

QUANT = 1e4


def _tri(positions):
    return np.asarray(positions, F32).reshape(-1, 3, 3).astype(np.float64)


def face_normals(positions):
    """Unit face normals ((B − A) × (C − A), Vector3#normalize), float32."""
    t = _tri(positions)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    ln = np.sqrt((n * n).sum(1))
    return (n * (1 / np.where(ln == 0, 1, ln))[:, None]).astype(F32)


def build_adjacency(positions):
    """buildAdjacency: {"adjacency": [[(neighbor, angleDeg)]], "faceNormals", "centroids",
    "vertId", "vertCount", "openEdgeCount", "nonManifoldEdgeCount", "shellCount", "shellId"}."""
    t = _tri(positions)
    n_tri = len(t)
    fn = face_normals(positions).astype(np.float64)
    vid, nv, _ = weld(t.reshape(-1, 3), QUANT)
    V = vid.reshape(-1, 3)
    collapsed = (V[:, 0] == V[:, 1]) | (V[:, 1] == V[:, 2]) | (V[:, 0] == V[:, 2])
    edges = {}
    for ti in np.nonzero(~collapsed)[0].tolist():
        a, b, c = V[ti].tolist()
        for x, y in ((a, b), (a, c), (b, c)):
            k = (x, y) if x < y else (y, x)
            lst = edges.get(k)
            if lst is None:
                edges[k] = [ti]
            else:
                lst.append(ti)
    adjacency = [[] for _ in range(n_tri)]
    root = list(range(n_tri))

    def find(x):
        while root[x] != x:
            root[x] = root[root[x]]
            x = root[x]
        return x

    open_e = nonman = 0
    for tris in edges.values():
        r0 = find(tris[0])
        for o in tris[1:]:
            r = find(o)
            if r != r0:
                root[r] = r0
        if len(tris) == 1:
            open_e += 1
            continue
        if len(tris) > 2:
            nonman += 1
        a, b = tris[0], tris[1]
        dot = max(-1.0, min(1.0, float(fn[a, 0] * fn[b, 0] + fn[a, 1] * fn[b, 1] + fn[a, 2] * fn[b, 2])))
        ang = math.acos(dot) * (180 / math.pi)
        adjacency[a].append((b, ang))
        adjacency[b].append((a, ang))
    shell = [-1] * n_tri
    count = 0
    for i in range(n_tri):
        if not collapsed[i] and find(i) == i:
            shell[i] = count
            count += 1
    for i in range(n_tri):
        if not collapsed[i]:
            shell[i] = shell[find(i)]
    return {"adjacency": adjacency, "faceNormals": fn.astype(F32), "centroids": t.mean(axis=1).astype(F32),
            "vertId": vid, "vertCount": nv, "openEdgeCount": open_e, "nonManifoldEdgeCount": nonman,
            "shellCount": count, "shellId": np.array(shell, np.int64)}


def bucket_fill(seed, adjacency, threshold_deg):
    """bucketFill: the faces reached from seed across edges at most threshold_deg."""
    seen = {seed}
    q = deque([seed])
    while q:
        cur = q.popleft()
        for nb, ang in adjacency[cur]:
            if nb not in seen and ang <= threshold_deg:
                seen.add(nb)
                q.append(nb)
    return seen


def build_face_weights(n_tri, excluded_faces, invert=False, soft_faces=None):
    """buildFaceWeights: per-corner weights, 1 = excluded."""
    w = np.full(n_tri * 3, 1.0 if invert else 0.0, F32)
    idx = np.fromiter((int(t) for t in excluded_faces), np.int64)
    if len(idx):
        w.reshape(-1, 3)[idx] = 0.0 if invert else 1.0
    if invert and soft_faces is not None:
        w.reshape(-1, 3)[np.asarray(soft_faces).astype(bool)] = 0.0
    return w


# ── Masks ──────────────────────────────────────────────────────────────────

class Mask:
    """A layer's mask: mode 'exclude' (painted faces stay plain) or 'include'
    (only painted faces are textured), paint per triangle corner (1 = painted)."""

    def __init__(self, paint, mode="exclude"):
        if mode not in ("exclude", "include"):
            raise ValueError("A mask's mode is 'exclude' or 'include'.")
        self.paint = np.asarray(paint, F32).reshape(-1)
        self.mode = mode

    @property
    def triangles(self):
        """Faces painted on all three corners."""
        return np.nonzero((self.paint.reshape(-1, 3) >= 1).all(axis=1))[0]

    def union(self, other):
        return Mask(np.maximum(self.paint, other.paint), self.mode)

    def subtract(self, other):
        return Mask(np.minimum(self.paint, 1 - other.paint), self.mode)

    def as_dict(self):
        return {"mode": self.mode, "paint": self.paint}


def from_triangles(n_tri, triangles, mode="exclude"):
    """A mask painting whole triangles."""
    p = np.zeros((n_tri, 3), F32)
    idx = np.asarray(sorted(int(t) for t in triangles), np.int64)
    if len(idx) and (idx.min() < 0 or idx.max() >= n_tri):
        raise ValueError(f"A triangle index is outside 0..{n_tri - 1}.")
    p[idx] = 1
    return Mask(p, mode)


def flood(positions, seeds, max_angle=20.0, mode="exclude", adjacency=None):
    """BumpMesh's Fill: from each seed face across edges up to max_angle degrees."""
    adj = (adjacency or build_adjacency(positions))["adjacency"]
    got = set()
    for s in ([seeds] if isinstance(seeds, (int, np.integer)) else seeds):
        got |= bucket_fill(int(s), adj, max_angle)
    return from_triangles(len(adj), got, mode)


def by_normal(positions, direction, max_angle=20.0, mode="exclude"):
    """Faces whose normal is within max_angle degrees of direction."""
    d = np.asarray(direction, np.float64)
    d = d / (np.linalg.norm(d) or 1)
    fn = face_normals(positions).astype(np.float64)
    cos = np.clip(fn @ d, -1, 1)
    return from_triangles(len(fn), np.nonzero(cos >= math.cos(math.radians(max_angle)))[0], mode)


def by_box(positions, lo, hi, mode="exclude", whole=False):
    """Faces in an axis-aligned box: centroid inside, or with whole=True all three corners."""
    t = _tri(positions)
    lo = np.asarray(lo, np.float64)
    hi = np.asarray(hi, np.float64)
    if whole:
        inside = ((t >= lo) & (t <= hi)).all(axis=2).all(axis=1)
    else:
        c = t.mean(axis=1)
        inside = ((c >= lo) & (c <= hi)).all(axis=1)
    return from_triangles(len(t), np.nonzero(inside)[0], mode)
