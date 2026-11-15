# SPDX-License-Identifier: AGPL-3.0-only
# Port of js/subdivisionEstimate.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# How many triangles subdivision will make at an edge length, without
# subdividing: each triangle's three edge lengths simulated through the same
# 2-, 3- and 4-way splits (memoised on the shape quantised to 1/256 of the
# edge), and the edge that fits a triangle budget.
import math

import numpy as np

from ._js import hypot


def _sim(a, b, c, T, memo, depth):
    if a < b:
        a, b = b, a
    if b < c:
        b, c = c, b
    if a < b:
        a, b = b, a
    key = (math.floor(a / T * 256 + 0.5), math.floor(b / T * 256 + 0.5), math.floor(c / T * 256 + 0.5))
    got = memo.get(key)
    if got is not None:
        return got
    if depth > 12:
        memo[key] = 1
        return 1
    n = (a > T) + (b > T) + (c > T)
    if n == 0:
        memo[key] = 1
        return 1
    if n == 3:
        total = 4 * _sim(a / 2, b / 2, c / 2, T, memo, depth + 1)
    elif n == 1:
        m = 0.5 * math.sqrt(max(0, 2 * b * b + 2 * c * c - a * a))
        total = _sim(a / 2, b, m, T, memo, depth + 1) + _sim(a / 2, c, m, T, memo, depth + 1)
    else:
        m = 0.5 * math.sqrt(max(0, 2 * b * b + 2 * c * c - a * a))
        total = (_sim(c, a / 2, m, T, memo, depth + 1) + _sim(m, c / 2, b / 2, T, memo, depth + 1)
                 + _sim(b / 2, c / 2, a / 2, T, memo, depth + 1))
    memo[key] = total
    return total


def compute_tri_edges(positions):
    """computeTriEdges: (n, 3) edge lengths AB, BC, CA (Math.hypot)."""
    p = np.asarray(positions, np.float32).reshape(-1, 9).astype(np.float64)
    ab = hypot(p[:, 3] - p[:, 0], p[:, 4] - p[:, 1], p[:, 5] - p[:, 2])
    bc = hypot(p[:, 6] - p[:, 3], p[:, 7] - p[:, 4], p[:, 8] - p[:, 5])
    ca = hypot(p[:, 0] - p[:, 6], p[:, 1] - p[:, 7], p[:, 2] - p[:, 8])
    return np.stack([ab, bc, ca], 1)


def simulate_from_edges(tri_edges, edge):
    """simulateFromEdges: the predicted triangle count at this edge length."""
    e = np.asarray(tri_edges, np.float64).reshape(-1, 3)
    small = (e <= edge).all(axis=1)
    total = int(small.sum())
    memo = {}
    for a, b, c in e[~small].tolist():
        total += _sim(a, b, c, edge, memo, 0)
    return total


def estimate_subdivision_tri_count(positions, edge):
    return simulate_from_edges(compute_tri_edges(positions), edge)


def solve_budget_edge(tri_edges, budget, start_edge):
    """solveBudgetEdge: the smallest edge (from start_edge, coarsening) whose count fits budget."""
    edge = start_edge
    for _ in range(3):
        n = simulate_from_edges(tri_edges, edge)
        if n <= budget:
            break
        corr = math.sqrt(n / budget)
        if corr < 1.005:
            break
        edge = edge * corr
    for _ in range(24):
        if simulate_from_edges(tri_edges, edge) <= budget:
            break
        edge *= 1.05
    return edge


def choose_preview_edge(positions, floor_edge, max_edge, tri_budget, tri_edges=None):
    """previewPipeline.js choosePreviewEdge: as fine as floor_edge, coarsened to the budget, at most max_edge."""
    if not (floor_edge < max_edge):
        return max_edge
    te = compute_tri_edges(positions) if tri_edges is None else tri_edges
    return min(solve_budget_edge(te, tri_budget, floor_edge), max_edge)
