# SPDX-License-Identifier: AGPL-3.0-only
# Port of js/outlineWrap.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# Outline wrap (mapping mode 8): the texture runs along the part's outline
# around +Z, with a whole number of repeats at every height. buildOutlineWrap
# cuts the mesh across Z and keeps each cut's largest loop as a table; a
# point's place (share of the outline from the seam, rise) is looked up in
# them, vectorised here with the JS's own binary searches.
import math

import numpy as np

from ._js import hypot1, to_fixed
from .meshindex import weld

WELD_QUANT = 1e5
OUTLINE_CUT_STEP_MM = 0.5
OUTLINE_FOLD_MM = 2
OUTLINE_POCKET_MM = 2
OUTLINE_CAP_RUN_MM = 2
OUTLINE_CAP_FLAT_DEG = 20
OUTLINE_CAP_WRAP_DEG = 30
TWO_PI = math.pi * 2
SEAM = -math.pi / 2


class OutlineError(ValueError):
    pass


def _turn(a):
    r = math.fmod(a, TWO_PI)
    return r + TWO_PI if r < 0 else r


def _half(a):
    r = _turn(a)
    return r - TWO_PI if r > math.pi else r


def _mm(v):
    s = to_fixed(v, 2)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def cap_weight(nz):
    """outlineWrapCapWeight, element-wise."""
    nz = np.asarray(nz, np.float64)
    off = np.arccos(np.minimum(1, np.abs(nz))) * 180 / math.pi
    t = (OUTLINE_CAP_WRAP_DEG - off) / (OUTLINE_CAP_WRAP_DEG - OUTLINE_CAP_FLAT_DEG)
    s = t * t * (3 - 2 * t)
    return np.where(off <= OUTLINE_CAP_FLAT_DEG, 1.0, np.where(off >= OUTLINE_CAP_WRAP_DEG, 0.0, s))


def build_outline_wrap(positions):
    """buildOutlineWrap: {cx, cy, minZ, maxZ, meanLength, cuts: [{z, theta, s, x, y, rise}]}.
    Raises OutlineError (one sentence) when the wrap cannot follow the part."""
    P = np.asarray(positions, np.float64).reshape(-1, 3)
    ids, nverts, firsts = weld(P, WELD_QUANT)
    V = P[firsts]
    vx, vy, vz = V[:, 0].tolist(), V[:, 1].tolist(), V[:, 2].tolist()
    if not len(V):
        raise OutlineError("No cut of the part across Z gives an outline of its sides, so the outline wrap has nothing to follow.")
    minX, maxX = min(vx), max(vx)
    minY, maxY = min(vy), max(vy)
    minZ, maxZ = min(vz), max(vz)
    cx, cy = (minX + maxX) / 2, (minY + maxY) / 2
    rng = maxZ - minZ
    count = max(1, math.ceil(rng / OUTLINE_CUT_STEP_MM))
    step = rng / count
    heights = [minZ + (k + 0.5) * step for k in range(count)]
    segs = [{"from": [], "to": [], "x": [], "y": [], "toX": [], "toY": [], "cap": []} for _ in heights]
    cap_cos = math.cos(OUTLINE_CAP_FLAT_DEG * math.pi / 180)
    tri = ids.reshape(-1, 3).tolist()
    for v in tri:
        zs = [vz[i] for i in v]
        lo, hi = min(zs), max(zs)
        if not (hi > lo) or step == 0:
            continue
        ax, ay, az = vx[v[1]] - vx[v[0]], vy[v[1]] - vy[v[0]], vz[v[1]] - vz[v[0]]
        bx, by, bz = vx[v[2]] - vx[v[0]], vy[v[2]] - vy[v[0]], vz[v[2]] - vz[v[0]]
        nx = ay * bz - az * by
        ny = az * bx - ax * bz
        nz = ax * by - ay * bx
        area = hypot1(nx, ny, nz)
        cap = area > 0 and abs(nz) >= cap_cos * area
        first = max(0, math.floor((lo - heights[0]) / step))
        last = min(count - 1, math.ceil((hi - heights[0]) / step))
        for k in range(first, last + 1):
            h = heights[k]
            ends = []
            for e in range(3):
                a, b = v[e], v[(e + 1) % 3]
                if (vz[a] >= h) == (vz[b] >= h):
                    continue
                below, above = (a, b) if vz[a] < h else (b, a)
                f = (h - vz[below]) / (vz[above] - vz[below])
                ends.append((min(a, b) * nverts + max(a, b), vx[below] + f * (vx[above] - vx[below]), vy[below] + f * (vy[above] - vy[below])))
            if len(ends) != 2:
                continue
            start, end = ends
            if (end[1] - start[1]) * -ny + (end[2] - start[2]) * nx < 0:
                start, end = end, start
            c = segs[k]
            c["from"].append(start[0])
            c["to"].append(end[0])
            c["x"].append(start[1])
            c["y"].append(start[2])
            c["toX"].append(end[1])
            c["toY"].append(end[2])
            c["cap"].append(cap)
    outlines = []
    for k, h in enumerate(heights):
        loop = _largest_loop(segs[k])
        if loop is None or loop["capRun"] > OUTLINE_CAP_RUN_MM:
            continue
        table = _cut_table(h, loop["x"], loop["y"], cx, cy)
        if not isinstance(table, str):
            outlines.append(table)
        elif loop["capRun"] == 0:
            raise OutlineError(table)
    if not outlines:
        raise OutlineError("No cut of the part across Z gives an outline of its sides, so the outline wrap has nothing to follow.")
    lengths = [o["s"][-1] for o in outlines]
    cuts = []
    rise = (outlines[0]["z"] - minZ) / lengths[0]
    for k, o in enumerate(outlines):
        if k > 0:
            rise += _rise_across(lengths[k - 1], lengths[k], o["z"] - outlines[k - 1]["z"], 1)
        cuts.append({**o, "rise": rise})
    mean = 0.0
    for L in lengths:
        mean += L
    mean /= len(lengths)
    return {"cx": cx, "cy": cy, "minZ": minZ, "maxZ": maxZ, "meanLength": mean, "cuts": cuts}


def _rise_across(frm, to, span, t):
    x = (to - frm) * t / frm
    return span * t * (1 if x == 0 else math.log1p(x) / x) / frm


def _largest_loop(cut):
    def index(keys):
        m = {}
        for i, k in enumerate(keys):
            m.setdefault(k, []).append(i)
        return m
    by_from, by_to = index(cut["from"]), index(cut["to"])
    n = len(cut["from"])
    used = [False] * n
    best, best_area = None, 0.0
    for i in range(n):
        if used[i]:
            continue
        x, y = [], []
        cap_run = 0.0
        at, fwd, closed = i, True, False
        while True:
            used[at] = True
            x.append(cut["x"][at] if fwd else cut["toX"][at])
            y.append(cut["y"][at] if fwd else cut["toY"][at])
            if cut["cap"][at]:
                cap_run += hypot1(cut["toX"][at] - cut["x"][at], cut["toY"][at] - cut["y"][at])
            ex = cut["to"][at] if fwd else cut["from"][at]
            if ex == cut["from"][i]:
                closed = True
                break
            ahead = next((j for j in by_from.get(ex, ()) if not used[j]), None)
            back = next((j for j in by_to.get(ex, ()) if not used[j]), None) if ahead is None else None
            if ahead is None and back is None:
                break
            fwd = ahead is not None
            at = ahead if ahead is not None else back
        if not closed:
            continue
        area = 0.0
        for j in range(len(x)):
            k = (j + 1) % len(x)
            area += x[j] * y[k] - x[k] * y[j]
        if abs(area) <= abs(best_area):
            continue
        best_area = area
        best = {"x": x, "y": y, "capRun": cap_run}
    if best and best_area < 0:
        best["x"].reverse()
        best["y"].reverse()
    return best


def _fill_shallow_pockets(X, Y):
    n = len(X)
    if n < 4:
        return X, Y

    def cross(o, a, b):
        return (X[a] - X[o]) * (Y[b] - Y[o]) - (Y[a] - Y[o]) * (X[b] - X[o])
    order = sorted(range(n), key=lambda i: (X[i], Y[i]))
    chain = []
    for pas in (order, order[::-1]):
        start = len(chain)
        for i in pas:
            while len(chain) >= start + 2 and cross(chain[-2], chain[-1], i) <= 0:
                chain.pop()
            chain.append(i)
        chain.pop()
    hull = sorted(set(chain))
    if len(hull) < 3:
        return X, Y
    ox, oy = [], []
    for k, a in enumerate(hull):
        b = hull[(k + 1) % len(hull)]
        ox.append(X[a])
        oy.append(Y[a])
        edge = hypot1(X[b] - X[a], Y[b] - Y[a])
        pocket, depth = [], 0.0
        i = (a + 1) % n
        while i != b:
            pocket.append(i)
            if edge > 0:
                depth = max(depth, abs(cross(a, b, i)) / edge)
            i = (i + 1) % n
        if depth > OUTLINE_POCKET_MM:
            for i in pocket:
                ox.append(X[i])
                oy.append(Y[i])
    return ox, oy


def _fraction(ax, ay, bx, by, a, cx, cy):
    rx, ry = math.cos(a), math.sin(a)
    ex, ey = bx - ax, by - ay
    across = ex * ry - ey * rx
    if abs(across) < 1e-15:
        return 0.0
    f = -((ax - cx) * ry - (ay - cy) * rx) / across
    return min(1.0, max(0.0, f))


def _cut_table(z, x, y, cx, cy):
    X, Y = [], []
    for i in range(len(x)):
        j = (i + 1) % len(x)
        if hypot1(x[j] - x[i], y[j] - y[i]) > 1e-12:
            X.append(x[i])
            Y.append(y[i])
    X, Y = _fill_shallow_pockets(X, Y)
    n = len(X)
    angle = [math.atan2(Y[i] - cy, X[i] - cx) for i in range(n)]
    steps = [_half(angle[(i + 1) % n] - a) for i, a in enumerate(angle)]
    tot = 0.0
    for d in steps:
        tot += d
    if n < 3 or tot < math.pi:
        return f"At z = {_mm(z)} mm, the centre of the bounding box lies outside the part's outline, so the outline wrap cannot go around it."
    laps = 3 * n
    A = [0.0] * (laps + 1)
    S = [0.0] * (laps + 1)
    A[0] = angle[0]
    for i in range(laps):
        a, b = i % n, (i + 1) % n
        A[i + 1] = A[i] + steps[a]
        S[i + 1] = S[i] + hypot1(X[b] - X[a], Y[b] - Y[a])
    pieces = []
    most, seen_s, hidden = A[0], 0.0, 0.0
    for i in range(laps):
        if A[i + 1] <= most:
            continue
        a, b = i % n, (i + 1) % n
        f = 0.0 if A[i] >= most else _fraction(X[a], Y[a], X[b], Y[b], most, cx, cy)
        start_s = S[i] + f * (S[i + 1] - S[i])
        if i >= n:
            hidden = max(hidden, start_s - seen_s)
            pieces.append([max(A[i], most), start_s, X[a] + f * (X[b] - X[a]), Y[a] + f * (Y[b] - Y[a]), A[i + 1], S[i + 1], X[b], Y[b]])
        seen_s = S[i + 1]
        most = A[i + 1]
    if hidden > OUTLINE_FOLD_MM:
        return (f"At z = {_mm(z)} mm, a ray from the centre of the bounding box crosses the part's outline more than once, "
                f"and {_mm(hidden)} mm of the outline lies hidden from the centre, so the outline wrap cannot follow it.")
    frm = pieces[0][0] + _turn(SEAM - pieces[0][0])
    to = frm + TWO_PI
    th, s, tx, ty = [], [], [], []

    def cut_at(p, a):
        f = _fraction(p[2], p[3], p[6], p[7], a, cx, cy)
        return [a, p[1] + f * (p[5] - p[1]), p[2] + f * (p[6] - p[2]), p[3] + f * (p[7] - p[3])]
    for p in pieces:
        if p[4] <= frm or p[0] >= to:
            continue
        a0, s0, x0, y0 = cut_at(p, frm) if p[0] < frm else p[0:4]
        a1, s1, x1, y1 = cut_at(p, to) if p[4] > to else p[4:8]
        th += [a0, a1]
        s += [s0, s1]
        tx += [x0, x1]
        ty += [y0, y1]
    zero = s[0]
    return {"z": z, "theta": np.array(th), "s": np.array([v - zero for v in s]), "x": np.array(tx), "y": np.array(ty)}


def _bsearch_last_le(arr, val, lo, hi):
    """The JS loop `while (lo < hi) { mid = (lo + hi + 1) >> 1; arr[mid] <= a ? lo = mid : hi = mid - 1 }`
    for arrays of values at once."""
    lo = np.broadcast_to(np.asarray(lo, np.int64), val.shape).copy()
    hi = np.broadcast_to(np.asarray(hi, np.int64), val.shape).copy()
    while True:
        act = lo < hi
        if not act.any():
            return lo
        mid = (lo + hi + 1) >> 1
        ok = arr[np.where(act, mid, 0)] <= val
        lo = np.where(act & ok, mid, lo)
        hi = np.where(act & ~ok, mid - 1, hi)


def _along(cut, phi, cx, cy):
    theta, s, x, y = cut["theta"], cut["s"], cut["x"], cut["y"]
    a = theta[0] + phi
    lo = _bsearch_last_le(theta, a, 0, len(theta) - 2)
    ax, ay, bx, by = x[lo], y[lo], x[lo + 1], y[lo + 1]
    rx, ry = np.cos(a), np.sin(a)
    ex, ey = bx - ax, by - ay
    across = ex * ry - ey * rx
    with np.errstate(all="ignore"):
        f = -((ax - cx) * ry - (ay - cy) * rx) / across
    f = np.where(np.abs(across) < 1e-15, 0.0, np.minimum(1, np.maximum(0, f)))
    return s[lo] + f * (s[lo + 1] - s[lo])


def places(wrap, x, y, z):
    """outlineWrapPlace for arrays: (share, rise)."""
    x, y, z = (np.asarray(a, np.float64) for a in (x, y, z))
    phi = np.fmod(np.arctan2(y - wrap["cy"], x - wrap["cx"]) - SEAM, TWO_PI)
    phi = np.where(phi < 0, phi + TWO_PI, phi)
    cuts = wrap["cuts"]
    zs = np.array([c["z"] for c in cuts])
    nc = len(cuts)
    k = np.zeros(len(z), np.int64)
    t = np.zeros(len(z))
    top = z >= zs[-1]
    k[top] = nc - 1
    mid = ~top & (z > zs[0])
    if mid.any():
        kk = np.zeros(int(mid.sum()), np.int64)
        hi = np.full(len(kk), nc - 1, np.int64)
        zm = z[mid]
        while True:
            act = kk + 1 < hi
            if not act.any():
                break
            m = (kk + hi) >> 1
            ok = zs[np.where(act, m, 0)] <= zm
            kk = np.where(act & ok, m, kk)
            hi = np.where(act & ~ok, m, hi)
        k[mid] = kk
        t[mid] = (zm - zs[kk]) / (zs[kk + 1] - zs[kk])
    share = np.zeros(len(z))
    rise = np.zeros(len(z))
    for ci in np.unique(k):
        sel = k == ci
        a = cuts[ci]
        l0 = a["s"][-1]
        u0 = _along(a, phi[sel], wrap["cx"], wrap["cy"])
        tt = t[sel]
        flat = tt == 0
        sh = u0 / l0
        rs = a["rise"] + (z[sel] - a["z"]) / l0
        if (~flat).any():
            b = cuts[ci + 1]
            l1 = b["s"][-1]
            u1 = _along(b, phi[sel], wrap["cx"], wrap["cy"])
            length = l0 + tt * (l1 - l0)
            xx = (l1 - l0) * tt / l0
            span = b["z"] - a["z"]
            with np.errstate(all="ignore"):
                ra = span * tt * np.where(xx == 0, 1.0, np.log1p(xx) / np.where(xx == 0, 1, xx)) / l0
            sh = np.where(flat, sh, (u0 + tt * (u1 - u0)) / length)
            rs = np.where(flat, rs, a["rise"] + ra)
        share[sel] = sh
        rise[sel] = rs
    return share, rise
