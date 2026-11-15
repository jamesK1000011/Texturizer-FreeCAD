# SPDX-License-Identifier: AGPL-3.0-only
# Port of js/textureAnalysis.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# How detailed a heightmap is: the mean central-difference gradient of the red
# channel and the share of sharp pixels (|∇I| > 30), turned into how many
# texture pixels one mesh edge may span (1, 1.5, 2.5 or 4). Read at the
# 512 px reference size so the answer does not shift with a map's pixel count.
import math

import numpy as np

SHARP_THRESHOLD = 30
REF_TEXTURE_SIZE = 512


def analyze_texture(img):
    """analyzeTexture: {"meanGrad", "sharpFrac", "pixelsPerEdge"}."""
    if img is None:
        return {"meanGrad": 0, "sharpFrac": 0, "pixelsPerEdge": 4.0}
    r = np.asarray(img.data)[:, :, 0].astype(np.float64)
    h, w = r.shape
    if w < 3 or h < 3:
        return {"meanGrad": 0, "sharpFrac": 0, "pixelsPerEdge": 4.0}
    dx = (r[1:-1, 2:] - r[1:-1, :-2]) * 0.5
    dy = (r[2:, 1:-1] - r[:-2, 1:-1]) * 0.5
    mag = np.sqrt(dx * dx + dy * dy).reshape(-1)
    n = len(mag)
    mean = float(np.cumsum(mag)[-1]) / n  # a running sum, as the JS loop adds
    sharp = int((mag > SHARP_THRESHOLD).sum()) / n
    if sharp > 0.15 or mean > 50:
        ppe = 1.0
    elif sharp > 0.05 or mean > 20:
        ppe = 1.5
    elif mean > 8:
        ppe = 2.5
    else:
        ppe = 4.0
    return {"meanGrad": mean, "sharpFrac": sharp, "pixelsPerEdge": ppe}


def analyze_texture_at_ref(img):
    """analyzeTextureAtRef: the analysis at REF_TEXTURE_SIZE (bilinear), with width and height."""
    if img is None:
        return {**analyze_texture(None), "width": 0, "height": 0}
    width, height = img.width, img.height
    longest = max(width, height)
    if longest <= REF_TEXTURE_SIZE:
        return {**analyze_texture(img), "width": width, "height": height}
    s = REF_TEXTURE_SIZE / longest
    w = max(1, int(math.floor(width * s + 0.5)))
    h = max(1, int(math.floor(height * s + 0.5)))
    src = np.asarray(img.data)[:, :, 0].astype(np.float64)
    sx, sy = width / w, height / h
    fy = np.clip((np.arange(h) + 0.5) * sy - 0.5, 0, height - 1)
    y0 = np.floor(fy).astype(int)
    y1 = np.minimum(y0 + 1, height - 1)
    ty = (fy - y0)[:, None]
    fx = np.clip((np.arange(w) + 0.5) * sx - 0.5, 0, width - 1)
    x0 = np.floor(fx).astype(int)
    x1 = np.minimum(x0 + 1, width - 1)
    tx = (fx - x0)[None, :]
    top = src[y0][:, x0] * (1 - tx) + src[y0][:, x1] * tx
    bot = src[y1][:, x0] * (1 - tx) + src[y1][:, x1] * tx
    v = top * (1 - ty) + bot * ty
    out = np.clip(np.floor(v + 0.5), 0, 255)  # Uint8ClampedArray rounds (half to even on .5 exactly)
    half = (v - np.floor(v)) == 0.5
    out = np.where(half, np.clip(np.round(v), 0, 255), out).astype(np.uint8)
    from .maps import Image
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[:, :, 0] = out
    return {**analyze_texture(Image(rgba)), "width": w, "height": h}
