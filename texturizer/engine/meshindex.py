# SPDX-License-Identifier: AGPL-3.0-only
# Port of js/meshIndex.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# Vertex welding on a grid: every position quantised (Math.round(c * quant))
# and given the sequential id of its cell's first occurrence, as
# QuantizedPointMap / weldVertices number them. The JS hash table only finds
# cells; the ids are first-occurrence order, which is what is reproduced here.
import numpy as np

from ._js import jround


def quantize(points, quant):
    """(n, 3) int64 grid cells of points (Math.round(c * quant))."""
    p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    return jround(p * quant).astype(np.int64)


def first_ids(keys):
    """For (n, k) integer keys: (ids, firsts) — each row's id in order of the
    first occurrence of its key, and the row index of each id's first."""
    keys = np.asarray(keys)
    n = len(keys)
    if n == 0:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    if keys.ndim == 1:
        keys = keys[:, None]
    order = np.lexsort(keys.T[::-1])
    sk = keys[order]
    new = np.empty(n, bool)
    new[0] = True
    new[1:] = (sk[1:] != sk[:-1]).any(axis=1)
    starts = np.nonzero(new)[0]
    grp = np.cumsum(new) - 1
    first = np.minimum.reduceat(order, starts)
    rank = np.empty(len(first), np.int64)
    rank[np.argsort(first, kind="stable")] = np.arange(len(first))
    ids = np.empty(n, np.int64)
    ids[order] = rank[grp]
    return ids, np.sort(first)


def weld(points, quant):
    """weldVertices: (vertexId per point, uniqueCount, first point index per id)."""
    ids, firsts = first_ids(quantize(points, quant))
    return ids, len(firsts), firsts


class PointMap:
    """QuantizedPointMap as a growing table of (cell, value) in insertion
    order, looked up in batches: get_or_set(points, values) answers what a
    run of getOrSet calls in that order would."""

    def __init__(self, quant):
        self.quant = quant
        self.keys = np.zeros((0, 3), np.int64)
        self.vals = np.zeros(0, np.int64)

    def get_or_set(self, points, values):
        k = quantize(points, self.quant)
        values = np.asarray(values, np.int64)
        allk = np.concatenate([self.keys, k])
        ids, firsts = first_ids(allk)
        m = len(self.keys)
        allv = np.concatenate([self.vals, values])
        out = allv[firsts[ids[m:]]]
        newmask = firsts[ids[m:]] >= m
        isfirst = np.zeros(len(k), bool)
        isfirst[firsts[firsts >= m] - m] = True
        self.keys = np.concatenate([self.keys, k[isfirst]])
        self.vals = np.concatenate([self.vals, values[isfirst]])
        return out, newmask
