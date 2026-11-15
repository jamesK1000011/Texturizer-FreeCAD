# SPDX-License-Identifier: AGPL-3.0-only
# Port of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# JavaScript's number rules where they differ from Python's, so the port's
# arithmetic is the engine's: Math.round (half up), Math.hypot as V8 computes
# it, the remainder operator (sign of the dividend), Number#toFixed, and
# BufferGeometry, the three.js container the JS pipeline passes between stages.
import math
from decimal import ROUND_HALF_UP, Decimal

import numpy as np

F32 = np.float32


def jround(x):
    """Math.round, element-wise: the nearest integer, halves up (toward +inf)."""
    x = np.asarray(x, dtype=np.float64)
    r = np.floor(x)
    return r + ((x - r) >= 0.5)


def jround1(x):
    r = math.floor(x)
    return r + 1 if x - r >= 0.5 else r


def jmod(a, b):
    """a % b as JavaScript computes it (C fmod: the sign of a)."""
    return np.fmod(a, b)


def hypot(*args):
    """Math.hypot as V8 computes it (scaled by the largest, Kahan-summed),
    element-wise over arrays or on plain numbers."""
    if all(isinstance(a, (int, float)) for a in args):
        return hypot1(*args)
    vals = [np.abs(np.asarray(a, dtype=np.float64)) for a in args]
    m = vals[0]
    for v in vals[1:]:
        m = np.maximum(m, v)
    safe = np.where(m > 0, m, 1.0)
    s = np.zeros_like(m)
    comp = np.zeros_like(m)
    for v in vals:
        n = v / safe
        summand = n * n - comp
        pre = s + summand
        comp = (pre - s) - summand
        s = pre
    out = np.sqrt(s) * m
    return np.where(m > 0, out, 0.0)


def hypot1(*args):
    vals = [abs(float(a)) for a in args]
    if any(math.isnan(v) for v in vals):
        return math.inf if any(math.isinf(v) for v in vals) else math.nan
    m = max(vals) if vals else 0.0
    if m == math.inf:
        return math.inf
    if m == 0:
        return 0.0
    s = 0.0
    comp = 0.0
    for v in vals:
        n = v / m
        summand = n * n - comp
        pre = s + summand
        comp = (pre - s) - summand
        s = pre
    return math.sqrt(s) * m


def to_fixed(x, digits):
    """Number#toFixed: the exact binary value rounded half away from zero; -0 reads 0."""
    if x == 0:
        return "0" if digits == 0 else "0." + "0" * digits
    q = Decimal(1).scaleb(-digits)
    return str(Decimal(float(x)).quantize(q, rounding=ROUND_HALF_UP))


def fixed_array(vals, digits):
    """to_fixed for every value of a float array, fast: Python's formatting,
    redone exactly where it can differ (a half-way value, or -0)."""
    vals = np.asarray(vals, dtype=np.float64)
    out = np.char.mod(f"%.{digits}f", vals).astype(object)
    scaled = vals * (2 * 10 ** digits)
    tie = np.nonzero((scaled == np.round(scaled)) | (vals == 0))[0]
    for i in tie:
        out[i] = to_fixed(vals[i], digits)
    return out


def fract(x):
    return x - np.floor(x)


class Attr:
    """A BufferAttribute: a flat typed array and its item size."""

    __slots__ = ("array", "item")

    def __init__(self, array, item):
        self.array = array
        self.item = item

    @property
    def count(self):
        return len(self.array) // self.item


class Geometry:
    """A non-indexed BufferGeometry: named attributes and userData."""

    def __init__(self, position=None):
        self.attributes = {}
        self.userData = {}
        if position is not None:
            self.set("position", position, 3)

    def set(self, name, array, item=3):
        self.attributes[name] = Attr(array, item)
        return self

    def get(self, name):
        a = self.attributes.get(name)
        return None if a is None else a.array

    def delete(self, name):
        self.attributes.pop(name, None)

    def compute_vertex_normals(self):
        """three.js r170 computeVertexNormals on a non-indexed mesh: the face's
        (C − B) × (A − B) on every corner, stored in float32, then each
        normalised (multiplied by 1 / length)."""
        p = self.get("position").reshape(-1, 9).astype(np.float64)
        a, b, c = p[:, 0:3], p[:, 3:6], p[:, 6:9]
        cb = c - b
        ab = a - b
        n = np.empty_like(cb)
        n[:, 0] = cb[:, 1] * ab[:, 2] - cb[:, 2] * ab[:, 1]
        n[:, 1] = cb[:, 2] * ab[:, 0] - cb[:, 0] * ab[:, 2]
        n[:, 2] = cb[:, 0] * ab[:, 1] - cb[:, 1] * ab[:, 0]
        n = np.repeat(n.astype(F32), 3, axis=0).astype(np.float64)
        self.set("normal", normalize_mul(n).astype(F32).reshape(-1), 3)


def normalize_mul(v):
    """Vector3#normalize on rows: v * (1 / (|v| || 1))."""
    ln = np.sqrt(v[:, 0] * v[:, 0] + v[:, 1] * v[:, 1] + v[:, 2] * v[:, 2])
    inv = 1.0 / np.where(ln == 0, 1.0, ln)
    return v * inv[:, None]


def cross_rows(e1, e2):
    out = np.empty_like(e1)
    out[:, 0] = e1[:, 1] * e2[:, 2] - e1[:, 2] * e2[:, 1]
    out[:, 1] = e1[:, 2] * e2[:, 0] - e1[:, 0] * e2[:, 2]
    out[:, 2] = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
    return out


def norm_rows(v):
    return np.sqrt(v[:, 0] * v[:, 0] + v[:, 1] * v[:, 1] + v[:, 2] * v[:, 2])
