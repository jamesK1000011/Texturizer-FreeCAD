# SPDX-License-Identifier: AGPL-3.0-only
# Port of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0: js/presetTextures.js
# (IMAGE_PRESETS, PRESET_CATEGORIES, DEFAULT_FAVOURITES) and engine/node.js loadPreset.
# The texture library: BumpMesh's presets (less the Filter Forge ones) from
# texturizer/textures/catalog.json, plus your own heightmaps from a folder.
# An entry's id is its BumpMesh name, the name a .bumpmesh project stores.
import json
import math
import os

from .imageio import decode_png, load_image
from .maps import PRESET_SIZE, fit_image

HERE = os.path.dirname(os.path.abspath(__file__))
TEXTURES = os.path.join(os.path.dirname(HERE), "textures")
BUMPMESH_URL = "https://github.com/CNCKitchen/stlTexturizer"

CATEGORIES = ["geometric", "patterns", "organic", "fabric", "natural", "grip", "mold", "tires"]
CUSTOM = "custom"
DEFAULT_FAVOURITES = ["Crystal", "Knurling", "Carbon Fiber", "Hexagons", "Voronoi", "Leather 2", "Weave 1",
                      "Grip Surface", "Labyrinth", "Square Setts", "Brick", "Wood 1"]
DEFAULT_TILE_FRACTION = 0.5
ANCHOR_MM = 50  # main.js _scaleAnchorMm with no model loaded

_LICENCE = {"hero": "CC BY 4.0 (Hero Patterns by Steve Schoger)", "cc0": "CC0 1.0",
            "cnckitchen": "AGPL-3.0-only (CNC Kitchen, BumpMesh)"}
_SOURCE = {"hero": "https://heropatterns.com/", "cnckitchen": BUMPMESH_URL}
_cache = {}
_custom_dirs = []


def to_precision3(x):
    """parseFloat(x.toPrecision(3))."""
    if x == 0 or not math.isfinite(x):
        return x
    return float(f"{x:.3g}")


def default_scale_mm(fraction, bounds=None):
    """main.js _defaultTileMm: a preset's tile fraction of the part's largest size, in mm."""
    anchor = max(bounds["size"]) if bounds else ANCHOR_MM
    return to_precision3((fraction if fraction is not None else DEFAULT_TILE_FRACTION) * anchor)


def _entry(c):
    cr = c["credit"]
    return {"id": c["name"], "name": c["name"], "category": c["category"],
            "thumb": os.path.join(TEXTURES, "thumbs", c["file"]), "image": os.path.join(TEXTURES, c["file"]),
            # The gallery reads "cnckitchen" as free text: BumpMesh's own textures.
            "credit": cr if cr in ("hero", "cc0") else "CNC Kitchen (BumpMesh)",
            "licence": _LICENCE[cr], "source_url": c.get("source") or _SOURCE.get(cr, BUMPMESH_URL),
            "defaults": {"scaleU": default_scale_mm(c["defaultScale"]), "scaleV": default_scale_mm(c["defaultScale"]),
                         "textureSmoothing": 0, "tileFraction": c["defaultScale"]}}


def add_custom_dir(path):
    """A folder of your own heightmaps (PNG; other formats with Pillow) to list as category custom."""
    if path not in _custom_dirs:
        _custom_dirs.append(path)


def _custom_entries():
    out = []
    for d in _custom_dirs:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if os.path.splitext(fn)[1].lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                continue
            p = os.path.join(d, fn)
            out.append({"id": "custom:" + fn, "name": os.path.splitext(fn)[0], "category": CUSTOM, "thumb": p,
                        "image": p, "credit": "custom", "licence": "Your own", "source_url": None,
                        "defaults": {"scaleU": default_scale_mm(None), "scaleV": default_scale_mm(None),
                                     "textureSmoothing": 0, "tileFraction": DEFAULT_TILE_FRACTION}})
    return out


def list():  # noqa: A001 - the interface's name
    """Every texture: {id, name, category, thumb, image, credit, licence, source_url, defaults}."""
    with open(os.path.join(TEXTURES, "catalog.json"), encoding="utf-8") as fh:
        cat = json.load(fh)
    return [_entry(c) for c in cat] + _custom_entries()


def get(texture):
    """An entry by id, name (any case) or a file path."""
    if isinstance(texture, dict):
        return texture
    t = str(texture)
    low = t.lower()
    for e in list():
        if e["id"] == t or e["name"].lower() == low:
            return e
    if os.path.isfile(t):
        return {"id": "file:" + t, "name": os.path.splitext(os.path.basename(t))[0], "category": CUSTOM,
                "thumb": t, "image": t, "credit": "custom", "licence": "Your own", "source_url": None,
                "defaults": {"scaleU": default_scale_mm(None), "textureSmoothing": 0, "tileFraction": DEFAULT_TILE_FRACTION}}
    raise KeyError(f'No library texture named "{texture}".')


def load(texture):
    """The heightmap as the engine takes it: presets fit to 512 px, your own to 2048 px."""
    e = get(texture)
    key = e["image"]
    img = _cache.get(key)
    if img is None:
        if e["category"] == CUSTOM:
            img = load_image(key)
        else:
            with open(key, "rb") as fh:
                img = fit_image(decode_png(fh.read()), PRESET_SIZE)
        if len(_cache) > 16:
            _cache.clear()
        _cache[key] = img
    return img
