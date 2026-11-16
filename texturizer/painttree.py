# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 CNCKitchen (Stefan Hermann) and contributors; the
# Texturizer for FreeCAD contributors.
# Port of js/paintTree.js of BumpMesh (stlTexturizer) by Stefan Hermann (CNC
# Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer, itself after
# PrusaSlicer's TriangleSelector (AGPL-3.0).
"""BumpMesh's PaintTree: adaptive surface painting on a per-triangle split
tree, the Precision brush (design 4.2, P4). Pure: no FreeCAD, Qt or Coin.

The base mesh is never modified. Every base triangle is the root of a tree;
the Precision circle brush splits the triangles it only partly covers along
their long edges (1, 2 or 3 edges -> 2, 3 or 4 children) until the edges are
shorter than the brush's edge limit (radius / 5 for a hard brush), so the
mask follows the brush outline on a 12-triangle cube as on a scan. Fully
covered triangles are painted whole; after every dab, children that came out
uniform are merged back, so the tree only keeps detail along mask edges.
The Standard brush (paint_stroke whole=True) paints every base triangle the
stroke touches in full.

Paint is per layer: a hard state per node and, for the soft brush, a
coverage value per vertex. Node, vertex and midpoint order, the split
patterns, flatten() and serialize() follow paintTree.js line by line, so a
tree serialized here replays in BumpMesh and the other way round.

Texturizer keeps one tree per layer in the draft (paint.py); combine()
builds the shared tree BumpMesh's paint.json and the engine's flattened
input need from several.
"""
import base64
import math
import struct

import numpy as np

from .engine.softmask import brush_coverage

NO_CHILD = -1
_F = struct.Struct("<f")


def f32(x):
    """A value as a Float32Array element holds it."""
    return _F.unpack(_F.pack(x))[0]


def dist_sq_point_to_tri(px, py, pz, ax, ay, az, bx, by, bz, cx, cy, cz):
    """distSqPointToTri: squared distance from P to triangle ABC (Voronoi regions)."""
    abx, aby, abz = bx - ax, by - ay, bz - az
    acx, acy, acz = cx - ax, cy - ay, cz - az
    apx, apy, apz = px - ax, py - ay, pz - az
    d1 = abx * apx + aby * apy + abz * apz
    d2 = acx * apx + acy * apy + acz * apz
    if d1 <= 0 and d2 <= 0:
        return apx * apx + apy * apy + apz * apz
    bpx, bpy, bpz = px - bx, py - by, pz - bz
    d3 = abx * bpx + aby * bpy + abz * bpz
    d4 = acx * bpx + acy * bpy + acz * bpz
    if d3 >= 0 and d4 <= d3:
        return bpx * bpx + bpy * bpy + bpz * bpz
    cpx, cpy, cpz = px - cx, py - cy, pz - cz
    d5 = abx * cpx + aby * cpy + abz * cpz
    d6 = acx * cpx + acy * cpy + acz * cpz
    if d6 >= 0 and d5 <= d6:
        return cpx * cpx + cpy * cpy + cpz * cpz
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3)
        qx, qy, qz = ax + v * abx - px, ay + v * aby - py, az + v * abz - pz
        return qx * qx + qy * qy + qz * qz
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6)
        qx, qy, qz = ax + w * acx - px, ay + w * acy - py, az + w * acz - pz
        return qx * qx + qy * qy + qz * qz
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        qx, qy, qz = bx + w * (cx - bx) - px, by + w * (cy - by) - py, bz + w * (cz - bz) - pz
        return qx * qx + qy * qy + qz * qz
    den = 1 / (va + vb + vc)
    v, w = vb * den, vc * den
    qx, qy, qz = ax + abx * v + acx * w - px, ay + aby * v + acy * w - py, az + abz * v + acz * w - pz
    return qx * qx + qy * qy + qz * qz


def edge_limit(radius, hardness):
    """_brushEdgeLimit (main.js): r/5 for a hard brush, finer across a soft brush's fade band."""
    edge = radius / 5
    if hardness < 1:
        band = (1 - hardness) * radius
        edge = min(edge, max(band / 3, radius / 10))
    return max(0.02, edge)


def _rot(mask):
    bits = (mask & 1) + ((mask >> 1) & 1) + ((mask >> 2) & 1)
    if bits == 1:
        return bits, 0 if mask == 1 else 1 if mask == 2 else 2
    if bits == 2:
        return bits, 0 if mask == 3 else 1 if mask == 6 else 2
    return bits, 0


def child_count(code):
    mask = code & 7
    return 0 if mask == 0 else 1 + (mask & 1) + ((mask >> 1) & 1) + ((mask >> 2) & 1)


class Layer:
    __slots__ = ("id", "state", "cov")

    def __init__(self, lid, n):
        self.id = lid
        self.state = [0] * n
        self.cov = None


class PaintTree:
    """paintTree.js's PaintTree on a paint.Surface (positions, vert_id,
    vert_count, neighbours, face normals)."""

    def __init__(self, surface):
        s = surface
        self.s = s
        self.base_tri_count = s.n
        self.base_vert_count = s.vert_count
        self.vert_id = s.tri.reshape(-1).tolist()
        self.neighbours = s.neighbours
        self.face_normals = s._fn
        V = np.asarray(s.V, np.float64)   # the last corner of a vertex wins, as the JS loop (paint.Surface)
        self.vx, self.vy, self.vz = V[:, 0].tolist(), V[:, 1].tolist(), V[:, 2].tolist()
        self.mid_ref = [0] * s.vert_count
        self.vert_count = s.vert_count
        self.nv = list(self.vert_id)
        n = s.n
        self.child = [NO_CHILD] * n
        self.code = [0] * n
        self.root = list(range(n))
        self.node_count = n
        self.mid = {}
        self.layers = []
        self.structure_version = 0
        self.paint_version = 0

    # -- layers ---------------------------------------------------------------
    def add_layer(self, lid):
        s = self.layer_slot(lid)
        if s >= 0:
            return s
        self.layers.append(Layer(lid, self.node_count))
        return len(self.layers) - 1

    def layer_slot(self, lid):
        for i, L in enumerate(self.layers):
            if L.id == lid:
                return i
        return -1

    def clear_layer(self, slot):
        L = self.layers[slot]
        L.state = [0] * self.node_count
        L.cov = None
        self.paint_version += 1
        self._merge_all()

    def _ensure_cov(self, L):
        if L.cov is None:
            L.cov = [0.0] * self.vert_count
        return L.cov

    # -- splitting --------------------------------------------------------------
    def _midpoint(self, a, b):
        key = (a, b) if a < b else (b, a)
        v = self.mid.get(key)
        if v is not None:
            if self.mid_ref[v] == 0:
                for L in self.layers:
                    if L.cov is not None:
                        L.cov[v] = f32((L.cov[a] + L.cov[b]) * 0.5)
            return v
        v = self.vert_count
        self.vert_count += 1
        self.vx.append((self.vx[a] + self.vx[b]) * 0.5)
        self.vy.append((self.vy[a] + self.vy[b]) * 0.5)
        self.vz.append((self.vz[a] + self.vz[b]) * 0.5)
        self.mid_ref.append(0)
        for L in self.layers:
            if L.cov is not None:
                L.cov.append(f32((L.cov[a] + L.cov[b]) * 0.5))
        self.mid[key] = v
        return v

    def _active_midpoint(self, a, b):
        v = self.mid.get((a, b) if a < b else (b, a))
        return v if v is not None and self.mid_ref[v] > 0 else -1

    def _edge_len2(self, a, b):
        dx, dy, dz = self.vx[a] - self.vx[b], self.vy[a] - self.vy[b], self.vz[a] - self.vz[b]
        return dx * dx + dy * dy + dz * dz

    def split(self, n, mask, diag=-1):
        """_split: n on the edges in mask (bit0 v0-v1, bit1 v1-v2, bit2 v2-v0);
        children inherit every layer's state. The first child's id."""
        if self.child[n] != NO_CHILD or mask == 0:
            return self.child[n]
        b = n * 3
        a0, a1, a2 = self.nv[b], self.nv[b + 1], self.nv[b + 2]
        bits, rot = _rot(mask)
        for _ in range(rot):
            a0, a1, a2 = a1, a2, a0
        mp = self._midpoint
        if bits == 1:
            m = mp(a0, a1)
            self.mid_ref[m] += 1
            kids = (a0, m, a2, m, a1, a2)
        elif bits == 2:
            m0 = mp(a0, a1)
            self.mid_ref[m0] += 1
            m1 = mp(a1, a2)
            self.mid_ref[m1] += 1
            if diag < 0:
                diag = 0 if self._edge_len2(a0, m1) <= self._edge_len2(m0, a2) else 1
            kids = (m0, a1, m1) + ((a0, m0, m1, a0, m1, a2) if diag == 0 else (a0, m0, a2, m0, m1, a2))
        else:
            m0 = mp(a0, a1)
            self.mid_ref[m0] += 1
            m1 = mp(a1, a2)
            self.mid_ref[m1] += 1
            m2 = mp(a2, a0)
            self.mid_ref[m2] += 1
            kids = (a0, m0, m2, m0, a1, m1, m2, m1, a2, m0, m1, m2)
        count = len(kids) // 3
        first = self.node_count
        self.node_count += count
        self.nv.extend(kids)
        self.child.extend([NO_CHILD] * count)
        self.code.extend([0] * count)
        self.root.extend([self.root[n]] * count)
        for L in self.layers:
            L.state.extend([L.state[n]] * count)
        self.child[n] = first
        self.code[n] = mask | ((1 if diag > 0 else 0) << 3)
        self.structure_version += 1
        return first

    def child_count(self, n):
        return child_count(self.code[n])

    def _try_merge(self, n):
        first = self.child[n]
        if first == NO_CHILD:
            return False
        count = self.child_count(n)
        for k in range(count):
            if self.child[first + k] != NO_CHILD:
                return False
        nv = self.nv
        for L in self.layers:
            s = L.state[first]
            for k in range(1, count):
                if L.state[first + k] != s:
                    return False
            if L.cov is not None:
                c = L.cov[nv[first * 3]]
                for k in range(count):
                    b = (first + k) * 3
                    if L.cov[nv[b]] != c or L.cov[nv[b + 1]] != c or L.cov[nv[b + 2]] != c:
                        return False
        for L in self.layers:
            L.state[n] = L.state[first]
        mask = self.code[n] & 7
        b = n * 3
        v0, v1, v2 = nv[b], nv[b + 1], nv[b + 2]
        if mask & 1:
            self.mid_ref[self._midpoint(v0, v1)] -= 1
        if mask & 2:
            self.mid_ref[self._midpoint(v1, v2)] -= 1
        if mask & 4:
            self.mid_ref[self._midpoint(v2, v0)] -= 1
        self.child[n] = NO_CHILD
        self.code[n] = 0
        self.structure_version += 1
        return True

    def _merge_all(self):
        for f in range(self.base_tri_count):
            if self.child[f] != NO_CHILD:
                self._merge_subtree(f)

    def _merge_subtree(self, n):
        first = self.child[n]
        if first == NO_CHILD:
            return
        for k in range(self.child_count(n)):
            self._merge_subtree(first + k)
        self._try_merge(n)

    def _set_subtree_state(self, n, L, value):
        L.state[n] = value
        first = self.child[n]
        if first == NO_CHILD:
            return
        for k in range(self.child_count(n)):
            self._set_subtree_state(first + k, L, value)

    def _set_subtree_cov(self, n, cov, value):
        b = n * 3
        cov[self.nv[b]] = cov[self.nv[b + 1]] = cov[self.nv[b + 2]] = value
        first = self.child[n]
        if first == NO_CHILD:
            return
        for k in range(self.child_count(n)):
            self._set_subtree_cov(first + k, cov, value)

    # -- painting --------------------------------------------------------------------
    def paint_faces(self, slot, faces, erase=False):
        """paintFaces: whole base faces (Fill, the Single brush)."""
        L = self.layers[slot]
        for f in faces:
            f = int(f)
            if 0 <= f < self.base_tri_count:
                self._paint_whole_face(f, L, erase)
        self.paint_version += 1

    def _paint_whole_face(self, f, L, erase):
        self._set_subtree_state(f, L, 0 if erase else 1)
        if erase and L.cov is not None:
            self._set_subtree_cov(f, L.cov, 0.0)
        self._merge_subtree(f)

    def paint_stroke(self, slot, seed, frm, to, radius, view, hardness, erase=False, edge=None, whole=False):
        """paintStroke: one dab, or the capsule swept from frm to to, measured in
        the plane perpendicular to view. whole: the Standard brush (no refinement)."""
        L = self.layers[slot]
        if seed < 0 or seed >= self.base_tri_count:
            return
        r = float(radius)
        r2 = r * r
        soft = not whole and hardness < 1
        if soft:
            self._ensure_cov(L)
        edge = edge_limit(r, hardness) if edge is None else edge
        vx, vy, vz = (float(c) for c in view)
        tx, ty, tz = (float(c) for c in to)
        frm = to if frm is None else frm
        sx, sy, sz = float(frm[0]) - tx, float(frm[1]) - ty, float(frm[2]) - tz
        a = sx * vx + sy * vy + sz * vz
        sx, sy, sz = sx - a * vx, sy - a * vy, sz - a * vz
        seg2 = sx * sx + sy * sy + sz * sz
        seg = seg2 ** 0.5

        def dist2(px, py, pz):
            px, py, pz = px - tx, py - ty, pz - tz
            al = px * vx + py * vy + pz * vz
            px, py, pz = px - al * vx, py - al * vy, pz - al * vz
            u = (px * sx + py * sy + pz * sz) / seg2 if seg2 > 0 else 0
            u = 0 if u < 0 else 1 if u > 1 else u
            dx, dy, dz = px - u * sx, py - u * sy, pz - u * sz
            return dx * dx + dy * dy + dz * dz

        n_dabs = max(1, math.ceil(seg / max(r * 0.5, 1e-6)) + 1)
        dabs = []
        for i in range(n_dabs):
            u = 0 if n_dabs == 1 else i / (n_dabs - 1)
            dabs.append((tx + sx * u, ty + sy * u, tz + sz * u))
        r_test = r + edge if soft else r
        reach = r_test + seg * 0.5
        ctx = _Ctx(L, soft, bool(erase), hardness, r, r2, r_test * r_test, edge * edge, dist2, dabs,
                   vx, vy, vz, (r * hardness) * (r * hardness))
        touched = self._faces_in_disk(int(seed), tx + sx * 0.5, ty + sy * 0.5, tz + sz * 0.5, reach * reach, vx, vy, vz)
        if whole:
            for f in touched:
                if self._node_stroke_dist2(f, ctx) <= r2:
                    self._paint_whole_face(f, L, ctx.erase)
            self.paint_version += 1
            return
        for f in touched:
            self._refine_node(f, ctx)
        for f in touched:
            self._paint_node(f, ctx)
        for f in touched:
            self._merge_subtree(f)
        self.paint_version += 1

    def faces_under_brush(self, seed, at, radius, view):
        """facesUnderBrush: the base faces a Standard dab at `at` marks (hover preview)."""
        if seed < 0 or seed >= self.base_tri_count:
            return []
        return self._faces_in_disk(int(seed), *(float(c) for c in at), float(radius) ** 2, *(float(c) for c in view))

    def _faces_in_disk(self, seed, cx, cy, cz, r2, vx, vy, vz):
        visited = {seed}
        queue = [seed]
        touched = []
        head = 0
        fn = self.face_normals
        nv = self.nv
        while head < len(queue):
            f = queue[head]
            head += 1
            b = f * 3
            if self._tri_dist2(nv[b], nv[b + 1], nv[b + 2], cx, cy, cz, vx, vy, vz) > r2:
                continue
            touched.append(f)
            for nb in self.neighbours[f]:
                if nb in visited:
                    continue
                visited.add(nb)
                n = fn[nb]
                if n[0] * vx + n[1] * vy + n[2] * vz >= 0:
                    continue   # back-facing: the silhouette stops the walk
                queue.append(nb)
        return touched

    def _tri_dist2(self, a, b, c, px, py, pz, vx, vy, vz):
        X, Y, Z = self.vx, self.vy, self.vz
        t = []
        for v in (a, b, c):
            dx, dy, dz = X[v] - px, Y[v] - py, Z[v] - pz
            al = dx * vx + dy * vy + dz * vz
            t += (dx - al * vx, dy - al * vy, dz - al * vz)
        return dist_sq_point_to_tri(0.0, 0.0, 0.0, *t)

    def _node_stroke_dist2(self, n, ctx):
        b = n * 3
        nv = self.nv
        best = float("inf")
        for x, y, z in ctx.dabs:
            d2 = self._tri_dist2(nv[b], nv[b + 1], nv[b + 2], x, y, z, ctx.vx, ctx.vy, ctx.vz)
            if d2 < best:
                best = d2
        return best

    def _classify(self, n, ctx):
        b = n * 3
        v0, v1, v2 = self.nv[b], self.nv[b + 1], self.nv[b + 2]
        X, Y, Z = self.vx, self.vy, self.vz
        d0 = ctx.dist2(X[v0], Y[v0], Z[v0])
        d1 = ctx.dist2(X[v1], Y[v1], Z[v1])
        d2 = ctx.dist2(X[v2], Y[v2], Z[v2])
        ctx.d = (d0, d1, d2)
        rt2 = ctx.r_test2
        inside = (d0 <= rt2) + (d1 <= rt2) + (d2 <= rt2)
        if inside == 3:
            if not ctx.soft or (d0 <= ctx.core2 and d1 <= ctx.core2 and d2 <= ctx.core2):
                return 2
            return 1
        if inside == 0 and self._node_stroke_dist2(n, ctx) > rt2:
            return 0
        return 1

    def _refine_node(self, n, ctx):
        if self._classify(n, ctx) != 1:
            return
        if self.child[n] == NO_CHILD:
            b = n * 3
            v0, v1, v2 = self.nv[b], self.nv[b + 1], self.nv[b + 2]
            mask = 0
            if self._edge_len2(v0, v1) > ctx.edge2:
                mask |= 1
            if self._edge_len2(v1, v2) > ctx.edge2:
                mask |= 2
            if self._edge_len2(v2, v0) > ctx.edge2:
                mask |= 4
            if mask == 0:
                return
            self.split(n, mask)
        first = self.child[n]
        for k in range(self.child_count(n)):
            self._refine_node(first + k, ctx)

    def _paint_node(self, n, ctx):
        c = self._classify(n, ctx)
        if c == 0:
            return
        if c == 2:
            self._paint_subtree(n, ctx)
            return
        if self.child[n] == NO_CHILD:
            self._paint_leaf(n, ctx, *ctx.d)
            return
        first = self.child[n]
        for k in range(self.child_count(n)):
            self._paint_node(first + k, ctx)

    def _paint_subtree(self, n, ctx):
        L = ctx.L
        if ctx.soft:
            self._set_subtree_cov(n, L.cov, 0.0 if ctx.erase else 1.0)
            if ctx.erase:
                self._set_subtree_state(n, L, 0)
        else:
            self._set_subtree_state(n, L, 0 if ctx.erase else 1)
            if ctx.erase and L.cov is not None:
                self._set_subtree_cov(n, L.cov, 0.0)

    def _paint_leaf(self, n, ctx, d0, d1, d2):
        L = ctx.L
        b = n * 3
        nv = self.nv
        if ctx.soft:
            cov = L.cov
            if ctx.erase and L.state[n]:
                L.state[n] = 0
                cov[nv[b]] = cov[nv[b + 1]] = cov[nv[b + 2]] = 1.0
            for k, d in enumerate((d0, d1, d2)):
                if d > ctx.r2:
                    continue
                v = nv[b + k]
                a = brush_coverage(d ** 0.5, ctx.r, ctx.hardness)
                if ctx.erase:
                    if 1 - a < cov[v]:
                        cov[v] = f32(1 - a)
                elif a > cov[v]:
                    cov[v] = f32(a)
            return
        X, Y, Z = self.vx, self.vy, self.vz
        cx = (X[nv[b]] + X[nv[b + 1]] + X[nv[b + 2]]) / 3
        cy = (Y[nv[b]] + Y[nv[b + 1]] + Y[nv[b + 2]]) / 3
        cz = (Z[nv[b]] + Z[nv[b + 1]] + Z[nv[b + 2]]) / 3
        if ctx.dist2(cx, cy, cz) <= ctx.r2:
            L.state[n] = 0 if ctx.erase else 1
            if ctx.erase and L.cov is not None:
                L.cov[nv[b]] = L.cov[nv[b + 1]] = L.cov[nv[b + 2]] = 0.0

    # -- queries ----------------------------------------------------------------------
    @property
    def is_flat(self):
        return all(self.child[f] == NO_CHILD for f in range(self.base_tri_count))

    def split_roots(self):
        """The base faces that are split."""
        return [f for f in range(self.base_tri_count) if self.child[f] != NO_CHILD]

    def leaves(self, f):
        """The leaves under node f, depth-first, children in order."""
        out, stack = [], [f]
        while stack:
            n = stack.pop()
            first = self.child[n]
            if first == NO_CHILD:
                out.append(n)
                continue
            for k in range(self.child_count(n) - 1, -1, -1):
                stack.append(first + k)
        return out

    def barycentric(self, n, px, py, pz):
        b = n * 3
        a, bb, c = self.nv[b], self.nv[b + 1], self.nv[b + 2]
        X, Y, Z = self.vx, self.vy, self.vz
        ax, ay, az = X[a], Y[a], Z[a]
        e0x, e0y, e0z = X[bb] - ax, Y[bb] - ay, Z[bb] - az
        e1x, e1y, e1z = X[c] - ax, Y[c] - ay, Z[c] - az
        d00 = e0x * e0x + e0y * e0y + e0z * e0z
        d01 = e0x * e1x + e0y * e1y + e0z * e1z
        d11 = e1x * e1x + e1y * e1y + e1z * e1z
        den = d00 * d11 - d01 * d01
        if not den > 1e-18 * d00 * d11:
            return (1 / 3, 1 / 3, 1 / 3)
        qx, qy, qz = px - ax, py - ay, pz - az
        d20 = qx * e0x + qy * e0y + qz * e0z
        d21 = qx * e1x + qy * e1y + qz * e1z
        v = (d11 * d20 - d01 * d21) / den
        w = (d00 * d21 - d01 * d20) / den
        return (1 - v - w, v, w)

    def leaf_at(self, f, x, y, z):
        """The leaf under base face f containing the point (samplePaint's descent)."""
        n = f
        for _ in range(64):
            first = self.child[n]
            if first == NO_CHILD:
                break
            best, best_min = -1, -float("inf")
            for k in range(self.child_count(n)):
                m = min(self.barycentric(first + k, x, y, z))
                if m > best_min:
                    best_min, best = m, first + k
            n = best
        return n

    def sample_paint(self, slot, f, x, y, z):
        """samplePaint: paint (0..1) of a layer at a point on base face f."""
        L = self.layers[slot] if 0 <= slot < len(self.layers) else None
        if L is None:
            return 0.0
        n = self.leaf_at(f, x, y, z)
        if L.state[n]:
            return 1.0
        if L.cov is None:
            return 0.0
        u, v, w = (c if c > 0 else 0.0 for c in self.barycentric(n, x, y, z))
        s = u + v + w or 1
        b = n * 3
        return (u * L.cov[self.nv[b]] + v * L.cov[self.nv[b + 1]] + w * L.cov[self.nv[b + 2]]) / s

    def _bary_many(self, nodes, P, NV, X):
        """barycentric() for many (node, point) pairs at once: the same
        float64 operations in the same order, so the same values."""
        A, B, C = NV[nodes * 3], NV[nodes * 3 + 1], NV[nodes * 3 + 2]
        a = X[A]
        e0 = X[B] - a
        e1 = X[C] - a
        d00 = e0[:, 0] * e0[:, 0] + e0[:, 1] * e0[:, 1] + e0[:, 2] * e0[:, 2]
        d01 = e0[:, 0] * e1[:, 0] + e0[:, 1] * e1[:, 1] + e0[:, 2] * e1[:, 2]
        d11 = e1[:, 0] * e1[:, 0] + e1[:, 1] * e1[:, 1] + e1[:, 2] * e1[:, 2]
        den = d00 * d11 - d01 * d01
        q = P - a
        d20 = q[:, 0] * e0[:, 0] + q[:, 1] * e0[:, 1] + q[:, 2] * e0[:, 2]
        d21 = q[:, 0] * e1[:, 0] + q[:, 1] * e1[:, 1] + q[:, 2] * e1[:, 2]
        ok = den > 1e-18 * d00 * d11
        with np.errstate(divide="ignore", invalid="ignore"):
            v = (d11 * d20 - d01 * d21) / den
            w = (d00 * d21 - d01 * d20) / den
        u = 1 - v - w
        third = 1 / 3
        return (np.where(ok, u, third), np.where(ok, v, third), np.where(ok, w, third))

    def leaf_at_many(self, faces, P):
        """leaf_at for many points at once (numpy): faces (k,) base faces,
        P (k, 3) float64 points; the leaves, the same ones leaf_at picks
        (the first child with the largest smallest barycentric)."""
        faces = np.asarray(faces, np.int64)
        P = np.asarray(P, np.float64).reshape(-1, 3)
        child = np.asarray(self.child, np.int64)
        code = np.asarray(self.code, np.int64)
        m7 = code & 7
        cc = np.where(m7 == 0, 0, 1 + (m7 & 1) + ((m7 >> 1) & 1) + ((m7 >> 2) & 1))
        NV = np.asarray(self.nv, np.int64)
        X = np.stack([np.asarray(self.vx), np.asarray(self.vy), np.asarray(self.vz)], 1)
        n = faces.copy()
        for _ in range(64):
            act = np.nonzero(child[n] != NO_CHILD)[0]
            if not len(act):
                break
            first = child[n[act]]
            cnt = cc[n[act]]
            best = np.full(len(act), -np.inf)
            pick = first.copy()
            for k in range(4):
                has = np.nonzero(cnt > k)[0]
                if not len(has):
                    break
                u, v, w = self._bary_many(first[has] + k, P[act[has]], NV, X)
                m = np.minimum(np.minimum(u, v), w)   # min(u, v, w), NaN never wins (as m > best_min)
                better = m > best[has]
                best[has[better]] = m[better]
                pick[has[better]] = first[has[better]] + k
            n[act] = pick
        return n

    def sample_paint_many(self, slot, faces, P):
        """sample_paint for many points at once: (k,) float64, value for
        value what sample_paint returns point by point."""
        P = np.asarray(P, np.float64).reshape(-1, 3)
        L = self.layers[slot] if 0 <= slot < len(self.layers) else None
        if L is None or not len(P):
            return np.zeros(len(P))
        n = self.leaf_at_many(faces, P)
        st = np.asarray(L.state, np.int64)[n] != 0
        out = st.astype(np.float64)
        if L.cov is None:
            return out
        soft = np.nonzero(~st)[0]
        if len(soft):
            NV = np.asarray(self.nv, np.int64)
            X = np.stack([np.asarray(self.vx), np.asarray(self.vy), np.asarray(self.vz)], 1)
            ns = n[soft]
            u, v, w = self._bary_many(ns, P[soft], NV, X)
            u, v, w = (np.where(c > 0, c, 0.0) for c in (u, v, w))
            s = u + v + w
            s = np.where(s == 0, 1, s)
            cov = np.asarray(L.cov, np.float64)
            out[soft] = (u * cov[NV[ns * 3]] + v * cov[NV[ns * 3 + 1]] + w * cov[NV[ns * 3 + 2]]) / s
        return out

    def base_paint(self, slot):
        """basePaint: per base corner paint and per base face hard flag from the roots."""
        L = self.layers[slot]
        n = self.base_tri_count
        hard = np.asarray(L.state[:n], np.uint8)
        paint = np.zeros((n, 3), np.float32)
        if L.cov is not None:
            paint[:] = np.asarray(L.cov, np.float32)[np.asarray(self.vert_id).reshape(-1, 3)]
        paint[hard.astype(bool)] = 1
        return paint.reshape(-1), hard

    def count_painted(self, slot):
        """countPainted: painted leaves and soft-painted vertices."""
        L = self.layers[slot]
        faces, seen, soft = 0, set(), 0
        for f in range(self.base_tri_count):
            for n in self.leaves(f):
                if L.state[n]:
                    faces += 1
                if L.cov is not None:
                    for k in range(3):
                        v = self.nv[n * 3 + k]
                        if v not in seen:
                            seen.add(v)
                            if L.cov[v] > 0:
                                soft += 1
        return {"faces": faces, "softVertices": soft}

    # -- flatten ----------------------------------------------------------------------
    def flatten(self, base_normals=None):
        """flatten: the leaves as a watertight non-indexed mesh; a neighbour's
        midpoint hanging on a leaf's edge splits that output triangle too.
        {positions, normals, faceParentId, leaf, cornerVert, triCount}."""
        out_v, out_leaf = [], []

        def emit(a, b, c, leaf):
            m = self._active_midpoint(a, b)
            if m >= 0:
                emit(a, m, c, leaf)
                emit(m, b, c, leaf)
                return
            m = self._active_midpoint(b, c)
            if m >= 0:
                emit(a, b, m, leaf)
                emit(a, m, c, leaf)
                return
            m = self._active_midpoint(c, a)
            if m >= 0:
                emit(a, b, m, leaf)
                emit(m, b, c, leaf)
                return
            out_v.extend((a, b, c))
            out_leaf.append(leaf)

        nv = self.nv
        for f in range(self.base_tri_count):
            if self.child[f] == NO_CHILD:
                b = f * 3
                if self._active_midpoint(nv[b], nv[b + 1]) < 0 and self._active_midpoint(nv[b + 1], nv[b + 2]) < 0 \
                        and self._active_midpoint(nv[b + 2], nv[b]) < 0:
                    out_v.extend((nv[b], nv[b + 1], nv[b + 2]))
                    out_leaf.append(f)
                    continue
            for n in self.leaves(f):
                b = n * 3
                emit(nv[b], nv[b + 1], nv[b + 2], n)
        tri = len(out_leaf)
        corner = np.asarray(out_v, np.int64)
        leaf = np.asarray(out_leaf, np.int64)
        V = np.stack([np.asarray(self.vx), np.asarray(self.vy), np.asarray(self.vz)], axis=1)
        positions = V[corner].astype(np.float32).reshape(-1)
        parent = np.asarray(self.root, np.int64)[leaf] if tri else np.zeros(0, np.int64)
        fn = np.asarray(self.face_normals, np.float64).reshape(-1, 3)
        if base_normals is None:
            normals = np.repeat(fn[parent], 3, axis=0).astype(np.float32).reshape(-1)
        else:
            bn = np.asarray(base_normals, np.float64).reshape(-1, 3, 3)
            normals = np.zeros((tri * 3, 3))
            for i in range(tri * 3):
                f = int(parent[i // 3])
                v = int(corner[i])
                u, vv, w = self.barycentric(f, self.vx[v], self.vy[v], self.vz[v])
                nn = u * bn[f, 0] + vv * bn[f, 1] + w * bn[f, 2]
                normals[i] = nn / (np.linalg.norm(nn) or 1)
            normals = normals.astype(np.float32).reshape(-1)
        return {"positions": positions, "normals": normals, "faceParentId": parent.astype(np.int32),
                "leaf": leaf, "cornerVert": corner, "triCount": tri}

    def flat_paint(self, slot, flat):
        """flatPaint: per-corner paint and per-triangle hard flag of a layer on a flatten() result."""
        L = self.layers[slot]
        t = flat["triCount"]
        if t == 0:
            return np.zeros(0, np.float32), np.zeros(0, np.uint8)
        st = np.asarray(L.state, np.uint8)[flat["leaf"]]
        paint = np.zeros((t, 3), np.float32)
        if L.cov is not None:
            paint[:] = np.asarray(L.cov, np.float32)[flat["cornerVert"]].reshape(-1, 3)
        paint[st.astype(bool)] = 1
        return paint.reshape(-1), st

    # -- serialization -----------------------------------------------------------------
    def _mids_of(self, n):
        """The midpoints _split creates for node n, in its order."""
        mask = self.code[n] & 7
        b = n * 3
        v = [self.nv[b], self.nv[b + 1], self.nv[b + 2]]
        bits, rot = _rot(mask)
        v = v[rot:] + v[:rot]
        pairs = [(v[0], v[1])] if bits == 1 else [(v[0], v[1]), (v[1], v[2])] if bits == 2 else \
            [(v[0], v[1]), (v[1], v[2]), (v[2], v[0])]
        return [self._midpoint(a, c) for a, c in pairs]

    def serialize(self, leaf_cov=False):
        """serialize(): per base face a depth-first stream of split codes, every
        layer's node states in that order, soft coverage per vertex in replay
        order, and with leaf_cov per leaf corner too."""
        codes, states = [], [[] for _ in self.layers]
        leaf_idx = [[] for _ in self.layers] if leaf_cov else None
        leaf_val = [[] for _ in self.layers] if leaf_cov else None
        new_id = {v: v for v in range(self.base_vert_count)}
        order = list(range(self.base_vert_count))
        for f in range(self.base_tri_count):
            stack = [f]
            while stack:
                n = stack.pop()
                pos = len(codes)
                codes.append(self.code[n])
                for li, L in enumerate(self.layers):
                    states[li].append(L.state[n])
                first = self.child[n]
                if leaf_cov and first == NO_CHILD:
                    b = n * 3
                    v0, v1, v2 = self.nv[b], self.nv[b + 1], self.nv[b + 2]
                    for li, L in enumerate(self.layers):
                        c = L.cov
                        if c is None:
                            continue
                        if c[v0] > 0 or c[v1] > 0 or c[v2] > 0:
                            leaf_idx[li].append(pos)
                            leaf_val[li].extend((c[v0], c[v1], c[v2]))
                if first == NO_CHILD:
                    continue
                for m in self._mids_of(n):
                    if m not in new_id:
                        new_id[m] = len(order)
                        order.append(m)
                for k in range(self.child_count(n) - 1, -1, -1):
                    stack.append(first + k)
        cov = []
        for L in self.layers:
            if L.cov is None:
                cov.append(None)
                continue
            out = [L.cov[v] for v in order]
            cov.append(out if any(x > 0 for x in out) else None)
        data = {"baseTriCount": self.base_tri_count, "baseVertCount": self.base_vert_count,
                "nodeCount": len(codes), "vertCount": len(order), "codes": codes,
                "layerIds": [L.id for L in self.layers], "states": states, "cov": cov, "leafCov": None}
        if leaf_cov:
            data["leafCov"] = [{"idx": leaf_idx[li], "val": leaf_val[li]} if leaf_idx[li] else None
                               for li in range(len(self.layers))]
        return data

    def deserialize(self, data):
        """deserialize(): rebuild from serialize() output (layers by id; layers
        the data lacks are kept, empty). False when it does not fit the base."""
        if not data or data["baseTriCount"] != self.base_tri_count:
            return False
        by_leaf = bool(data.get("leafCov"))
        if not by_leaf and data["baseVertCount"] != self.base_vert_count:
            return False
        keep = [L.id for L in self.layers]
        n = self.base_tri_count
        nb = self.base_vert_count
        self.vx, self.vy, self.vz = self.vx[:nb], self.vy[:nb], self.vz[:nb]
        self.mid_ref = [0] * nb
        self.vert_count = nb
        self.mid = {}
        self.nv = list(self.vert_id)
        self.child = [NO_CHILD] * n
        self.code = [0] * n
        self.root = list(range(n))
        self.node_count = n
        ids = list(dict.fromkeys(keep + list(data.get("layerIds") or [])))
        self.layers = [Layer(i, n) for i in ids]
        slot_of = {lid: i for i, lid in enumerate(data.get("layerIds") or [])}
        codes = data["codes"]
        dfs = [0] * data["nodeCount"] if by_leaf else None
        pos = 0
        for f in range(n):
            stack = [f]
            while stack:
                node = stack.pop()
                code = int(codes[pos])
                if dfs is not None:
                    dfs[pos] = node
                for L in self.layers:
                    ds = slot_of.get(L.id)
                    if ds is not None:
                        L.state[node] = int(data["states"][ds][pos])
                pos += 1
                mask = code & 7
                if mask == 0:
                    continue
                first = self.split(node, mask, (code >> 3) & 1)
                for k in range(self.child_count(node) - 1, -1, -1):
                    stack.append(first + k)
        for L in self.layers:
            ds = slot_of.get(L.id)
            if ds is None:
                continue
            if by_leaf:
                lc = data["leafCov"][ds]
                if not lc:
                    continue
                cov = self._ensure_cov(L)
                val = lc["val"]
                for i, p in enumerate(lc["idx"]):
                    b = dfs[int(p)] * 3
                    for k in range(3):
                        v, c = self.nv[b + k], float(val[i * 3 + k])
                        if c > cov[v]:
                            cov[v] = f32(c)
            else:
                c = data["cov"][ds]
                if c is not None:
                    L.cov = [f32(float(x)) for x in list(c)[:self.vert_count]]
                    L.cov += [0.0] * (self.vert_count - len(L.cov))
        self.structure_version += 1
        self.paint_version += 1
        return True


class _Ctx:
    __slots__ = ("L", "soft", "erase", "hardness", "r", "r2", "r_test2", "edge2", "dist2", "dabs",
                 "vx", "vy", "vz", "core2", "d")

    def __init__(self, L, soft, erase, hardness, r, r2, r_test2, edge2, dist2, dabs, vx, vy, vz, core2):
        self.L, self.soft, self.erase, self.hardness = L, soft, erase, hardness
        self.r, self.r2, self.r_test2, self.edge2 = r, r2, r_test2, edge2
        self.dist2, self.dabs = dist2, dabs
        self.vx, self.vy, self.vz = vx, vy, vz
        self.core2 = core2
        self.d = (0.0, 0.0, 0.0)


# -- paint.json (PaintTree.toJSON / fromJSON) ------------------------------------------------
def _b64(b):
    return base64.b64encode(bytes(b)).decode("ascii")


def to_json(data):
    """PaintTree.toJSON: the JSON-safe form a .bumpmesh's paint.json holds."""
    out = {"version": 1, "baseTriCount": data["baseTriCount"], "baseVertCount": data["baseVertCount"],
           "nodeCount": data["nodeCount"], "vertCount": data["vertCount"],
           "codes": _b64(np.asarray(data["codes"], np.uint8).tobytes()), "layerIds": list(data["layerIds"]),
           "states": [_b64(np.asarray(s, np.uint8).tobytes()) for s in data["states"]],
           "cov": [None if c is None else _b64(np.asarray(c, "<f4").tobytes()) for c in data["cov"]]}
    if data.get("leafCov"):
        out["leafCov"] = [None if lc is None else {"idx": _b64(np.asarray(lc["idx"], "<i4").tobytes()),
                                                   "val": _b64(np.asarray(lc["val"], "<f4").tobytes())}
                          for lc in data["leafCov"]]
    return out


def from_json(j):
    """PaintTree.fromJSON: None for an unknown version."""
    if not j or j.get("version") != 1:
        return None

    def u8(s):
        return np.frombuffer(base64.b64decode(s), np.uint8)

    out = {"baseTriCount": int(j["baseTriCount"]), "baseVertCount": int(j["baseVertCount"]),
           "nodeCount": int(j["nodeCount"]), "vertCount": int(j["vertCount"]),
           "codes": u8(j["codes"]).tolist(), "layerIds": list(j["layerIds"]),
           "states": [u8(s).tolist() for s in j["states"]],
           "cov": [None if c is None else np.frombuffer(base64.b64decode(c), "<f4").tolist() for c in j["cov"]],
           "leafCov": None}
    if isinstance(j.get("leafCov"), list):
        out["leafCov"] = [None if lc is None else {
            "idx": np.frombuffer(base64.b64decode(lc["idx"]), "<i4").tolist(),
            "val": np.frombuffer(base64.b64decode(lc["val"]), "<f4").tolist()} for lc in j["leafCov"]]
    return out


# -- several layers' trees as one ---------------------------------------------------------------
def combine(surface, trees, ids=None):
    """One PaintTree with a layer per (single-layer) tree in trees: the union
    of their splits (a node one tree splits differently from another keeps
    the first tree's split), each layer's state per leaf and coverage per
    vertex read from its own tree at the leaf (exact where the structures
    agree, sampled where one is finer). BumpMesh keeps all layers in one
    tree; Texturizer keeps one per layer and joins them for paint.json and
    the engine's flattened input."""
    out = PaintTree(surface)
    ids = list(ids) if ids is not None else list(range(1, len(trees) + 1))
    for t in trees:
        if t is None:
            continue
        for f in t.split_roots():
            stack = [(f, f)]
            while stack:
                a, b = stack.pop()
                if t.child[a] == NO_CHILD:
                    continue
                if out.child[b] == NO_CHILD:
                    out.split(b, t.code[a] & 7, (t.code[a] >> 3) & 1)
                elif out.code[b] != t.code[a]:
                    continue
                for k in range(t.child_count(a)):
                    stack.append((t.child[a] + k, out.child[b] + k))
    for lid in ids:
        out.add_layer(lid)
    for slot, t in enumerate(trees):
        if t is None:
            continue
        L, src = out.layers[slot], t.layers[0]
        if src.cov is not None:
            out._ensure_cov(L)
        for f in range(out.base_tri_count):
            if out.child[f] == NO_CHILD and t.child[f] == NO_CHILD:
                L.state[f] = src.state[f]
                if src.cov is not None:
                    for k in range(3):
                        v = out.nv[f * 3 + k]
                        L.cov[v] = max(L.cov[v], src.cov[t.nv[f * 3 + k]])
                continue
            for n in out.leaves(f):
                b = n * 3
                X, Y, Z = out.vx, out.vy, out.vz
                cx = sum(X[out.nv[b + k]] for k in range(3)) / 3
                cy = sum(Y[out.nv[b + k]] for k in range(3)) / 3
                cz = sum(Z[out.nv[b + k]] for k in range(3)) / 3
                m = t.leaf_at(f, cx, cy, cz)
                L.state[n] = src.state[m]
                if src.cov is None:
                    continue
                mb = m * 3
                for k in range(3):
                    v = out.nv[b + k]
                    u, vv, w = (c if c > 0 else 0.0 for c in t.barycentric(m, X[v], Y[v], Z[v]))
                    s = u + vv + w or 1
                    c = (u * src.cov[t.nv[mb]] + vv * src.cov[t.nv[mb + 1]] + w * src.cov[t.nv[mb + 2]]) / s
                    L.cov[v] = max(L.cov[v], f32(c))
    return out


def single(tree, slot):
    """One layer of a shared tree as a tree of its own, with the splits only
    that layer needs (the others merged back)."""
    data = tree.serialize()
    s = data["layerIds"].index(tree.layers[slot].id)
    one = {**data, "layerIds": [tree.layers[slot].id], "states": [data["states"][s]], "cov": [data["cov"][s]]}
    t = PaintTree(tree.s)
    t.deserialize(one)
    t._merge_all()
    return t
