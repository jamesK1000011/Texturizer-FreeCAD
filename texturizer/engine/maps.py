# SPDX-License-Identifier: AGPL-3.0-only
# Port of engine/maps.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# Heightmaps as the engine takes them: RGBA (h, w, 4) uint8, sized as the app
# sizes them (library textures to fit 512 px, your own to fit 2048 px), with
# the layer's texture smoothing and invert applied (main.js _effectiveMapFor).
import math

import numpy as np

from ._js import jround, jround1

PRESET_SIZE = 512
CUSTOM_SIZE = 2048
REF_TEXTURE_SIZE = 512  # textureAnalysis.js: smoothing is in pixels of a 512 px map


class Image:
    """ImageData-like: data (h, w, 4) uint8 RGBA, width, height."""

    __slots__ = ("data", "width", "height")

    def __init__(self, data):
        self.data = np.ascontiguousarray(data, dtype=np.uint8)
        self.height, self.width = self.data.shape[:2]


def fit_dimensions(w, h, max_size):
    s = min(max_size / w, max_size / h, 1)
    return int(jround1(w * s)), int(jround1(h * s))


def fit_image(img, max_size):
    """fitImage: box-averaged down to fit max_size (Uint8ClampedArray stores
    the mean rounded half to even, as a clamped array does)."""
    w, h = fit_dimensions(img.width, img.height, max_size)
    if w == img.width and h == img.height:
        return img
    W, H = img.width, img.height
    sx, sy = W / w, H / h
    src = img.data.astype(np.float64)
    # Per output column: the source columns it averages (floor(x0) .. ceil(x1)).
    def spans(n, s, N):
        lo = [math.floor(k * s) for k in range(n)]
        hi = [math.ceil(min(N, (k + 1) * s)) for k in range(n)]
        return lo, hi
    xl, xh = spans(w, sx, W)
    yl, yh = spans(h, sy, H)
    out = np.empty((h, w, 4), np.float64)
    # Sum rows first, then columns: the same totals as the JS double loop
    # (integer sums of uint8 are exact in float64).
    cs = np.concatenate([np.zeros((H, 1, 4)), np.cumsum(src, axis=1)], axis=1)
    colsum = np.stack([cs[:, xh[k]] - cs[:, xl[k]] for k in range(w)], axis=1)  # (H, w, 4)
    rs = np.concatenate([np.zeros((1, w, 4)), np.cumsum(colsum, axis=0)], axis=0)
    for y in range(h):
        tot = rs[yh[y]] - rs[yl[y]]
        cnt = np.array([(yh[y] - yl[y]) * (xh[k] - xl[k]) for k in range(w)], np.float64)
        out[y] = tot / cnt[:, None]
    return Image(_clamped(out))


def _clamped(v):
    """Uint8ClampedArray assignment: clamp to 0..255, round half to even."""
    return np.clip(np.rint(v), 0, 255).astype(np.uint8)


def _box_blur_h(src, dst, w, h, r):
    """maps.js boxBlurH on (h, w, 4) float arrays, the running sum kept exactly as JS keeps it."""
    iarr = 1 / (r + r + 1)
    for c in range(4):
        S = src[:, :, c]
        D = dst[:, :, c]
        fv = S[:, 0].copy()
        lv = S[:, w - 1].copy()
        val = (r + 1) * fv
        for j in range(r):
            val = val + S[:, min(j, w - 1)]
        ri, li, ti = r, 0, 0
        for j in range(r + 1):
            val = val + ((S[:, ri] if ri < w else lv) - fv)
            D[:, ti] = _clamped(jround(val * iarr))
            ri += 1
            ti += 1
        for j in range(r + 1, w - r):
            val = val + (S[:, ri] - S[:, li])
            D[:, ti] = _clamped(jround(val * iarr))
            ri += 1
            li += 1
            ti += 1
        for j in range(max(w - r, r + 1), w):
            val = val + (lv - S[:, li])
            D[:, ti] = _clamped(jround(val * iarr))
            li += 1
            ti += 1


def _blur_tile(img, sigma):
    w, h = img.width, img.height
    pad_x = min(w, math.ceil(4 * sigma) + 2)
    pad_y = min(h, math.ceil(4 * sigma) + 2)
    ys = (np.arange(h + 2 * pad_y) - pad_y) % h
    xs = (np.arange(w + 2 * pad_x) - pad_x) % w
    a = img.data[ys][:, xs].astype(np.float64)
    b = np.zeros_like(a)
    W, H = a.shape[1], a.shape[0]
    r = max(1, int(jround1((math.sqrt(4 * sigma * sigma + 1) - 1) / 2)))
    for _ in range(3):
        _box_blur_h(a, b, W, H, r)
        # boxBlurV is boxBlurH on the transposed image.
        bt = np.ascontiguousarray(b.transpose(1, 0, 2))
        at = np.zeros_like(bt)
        _box_blur_h(bt, at, H, W, r)
        a = np.ascontiguousarray(at.transpose(1, 0, 2))
    return Image(a[pad_y:pad_y + h, pad_x:pad_x + w].astype(np.uint8))


def prepare_map(img, texture_smoothing=0, invert_texture=False):
    """prepareMap: the map a layer samples, smoothed and inverted as its settings say."""
    out = img
    if texture_smoothing and texture_smoothing > 0:
        sigma = texture_smoothing * max(1, max(img.width, img.height) / REF_TEXTURE_SIZE)
        out = _blur_tile(img, sigma)
    if invert_texture:
        d = out.data.copy()
        d[:, :, :3] = 255 - d[:, :, :3]
        d[:, :, 3] = 255
        out = Image(d)
    return out


def grey_to_rgba(grey, width, height):
    g = np.asarray(grey, np.uint8).reshape(height, width)
    return Image(np.dstack([g, g, g, np.full_like(g, 255)]))
