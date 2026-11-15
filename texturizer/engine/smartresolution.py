# SPDX-License-Identifier: AGPL-3.0-only
# Port of js/smartResolution.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# "Suggest values": a resolution (refineLength) from the texture's detail
# (pixels per edge x mm per texture pixel), coarsened to the 16 M x 0.75
# triangle budget and clamped to 0.05 .. min(5, diagonal / 50), rounded up to
# 0.01 mm; and the matching output-triangle limit from the surface area and
# the texture height.
import math

import numpy as np

from .subdivisionestimate import compute_tri_edges, simulate_from_edges, solve_budget_edge
from .textureanalysis import analyze_texture_at_ref

HARD_CAP_TRIANGLES = 16_000_000
HARD_CAP_HEADROOM = 0.75
TRIS_PER_AREA_GEOM = 4 / math.sqrt(3)
DECIM_COARSEN = 1.0
DECIM_REF_AMP = 0.5
DECIM_MIN_AMP = 0.1
DECIM_MIN_TRI = 10_000
DECIM_MAX_TRI = 2_000_000


def surface_area(positions):
    """stlLoader.js computeSurfaceArea."""
    p = np.asarray(positions, np.float32).reshape(-1, 9).astype(np.float64)
    u = p[:, 3:6] - p[:, 0:3]
    v = p[:, 6:9] - p[:, 0:3]
    c = np.cross(u, v)
    return float(np.cumsum(np.sqrt((c * c).sum(1)) * 0.5)[-1]) if len(p) else 0.0


def recommended_max_tri(pixels_per_edge, pix_mm, area, amplitude):
    if not (pixels_per_edge > 0) or not (pix_mm > 0) or not (area > 0):
        return DECIM_MIN_TRI
    amp = abs(amplitude or 0)
    amp_scale = math.sqrt(DECIM_REF_AMP / max(amp, DECIM_MIN_AMP))
    target = DECIM_COARSEN * pixels_per_edge * pix_mm * amp_scale
    raw = TRIS_PER_AREA_GEOM * area / (target * target)
    stepped = math.floor(raw / 10_000 + 0.5) * 10_000
    return max(DECIM_MIN_TRI, min(DECIM_MAX_TRI, stepped))


def smart_resolution(positions, settings, image, bounds=None):
    """computeSmartResolution: {"edge", "maxTriangles", "diagnostics"} or None."""
    if image is None or positions is None or not len(positions):
        return None
    from .index import bounds_of
    bounds = bounds or bounds_of(positions)
    a = analyze_texture_at_ref(image)
    pu = (settings.get("scaleU") or 1e-6) / (settings.get("textureAspectU") or 1)
    pv = (settings.get("scaleV") or 1e-6) / (settings.get("textureAspectV") or 1)
    pix_mm = min(pu / a["width"], pv / a["height"])
    detail = pix_mm * a["pixelsPerEdge"]
    area = surface_area(positions)
    budget = HARD_CAP_TRIANGLES * HARD_CAP_HEADROOM
    te = compute_tri_edges(positions)
    budget_edge = solve_budget_edge(te, budget, math.sqrt(TRIS_PER_AREA_GEOM * area / max(budget, 1)))
    edge = max(detail, budget_edge)
    sx, sy, sz = bounds["size"]
    diag = math.sqrt(sx ** 2 + sy ** 2 + sz ** 2)
    lo, hi = 0.05, min(5.0, diag / 50)
    pre = edge
    edge = min(max(edge, lo), max(hi, lo))
    clamped = edge != pre
    edge = max(lo, math.ceil(edge * 100) / 100)
    amp = settings.get("amplitude", settings.get("textureHeight", 0.5))
    rec = recommended_max_tri(a["pixelsPerEdge"], pix_mm, area, amp)
    return {"edge": edge, "maxTriangles": rec, "diagnostics": {
        "pixelsPerEdge": a["pixelsPerEdge"], "meanGrad": a["meanGrad"], "sharpFrac": a["sharpFrac"],
        "pixMm": pix_mm, "period_mm": min(pu, pv), "surfaceArea": area, "detailEdge": detail,
        "budgetEdge": budget_edge, "estTriangles": simulate_from_edges(te, edge), "triBudget": budget,
        "budgetClamped": budget_edge > detail, "edgeClamped": clamped, "recommendedMaxTri": rec}}
