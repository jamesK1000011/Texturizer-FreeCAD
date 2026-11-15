# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# The labels, ranges, defaults and tooltips this script reads are from
# BumpMesh (stlTexturizer) by Stefan Hermann (CNC Kitchen), AGPL-3.0,
# github.com/CNCKitchen/stlTexturizer.
"""Generate texturizer/controls.py from BumpMesh's own sources.

    python3 tests/tools/gen_controls.py [BUMPMESH_DIR] [--check]

BUMPMESH_DIR (default $BUMPMESH_SRC, else a stltexturizer checkout beside this
repo or one folder further) is a BumpMesh checkout. SPEC below names each sidebar control by its HTML id
and its en.js keys; the text, ranges and defaults are read from index.html and
js/i18n/en.js, and every default that is also a key of main.js's `settings`
must agree with it. --check compares instead of writing (exit 1 on a diff).
Rows marked "added" are Texturizer's own (design section 2) and carry their
own labels.
"""
import ast
import html.parser
import json
import os
import pprint
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(ROOT, "texturizer", "controls.py")
CATALOG = os.path.join(ROOT, "texturizer", "textures", "catalog.json")


def shipped():
    """The texture names Texturizer ships (texturizer/textures/catalog.json):
    BumpMesh's presets less the Filter Forge ones, which are not ours to ship."""
    with open(CATALOG, encoding="utf-8") as fh:
        return {c["name"] for c in json.load(fh)}


def not_shipped(names, have):
    """Explicit filter: the names in BumpMesh's defaults that we do not ship."""
    return list(dict.fromkeys(n for n in names if n not in have))


def _find_src():
    import glob

    up = os.path.dirname(ROOT)
    for pat in ("stltexturizer", "stlTexturizer", "*/stltexturizer", "*/stlTexturizer"):
        for d in sorted(glob.glob(os.path.join(up, pat))):
            if os.path.exists(os.path.join(d, "js", "i18n", "en.js")):
                return d
    return os.path.join(up, "stltexturizer")


DEFAULT_SRC = _find_src()

# Sections in BumpMesh's order: (id, heading key or None, tooltip key, collapsed).
SECTIONS = [
    ("header", None, None, False),
    ("load", None, None, False),
    ("displacementMap", "sections.displacementMap", None, False),
    ("projection", "sections.projection", None, False),
    ("textureDepth", "sections.displacement", None, False),
    ("transform", "sections.transform", None, False),
    ("masking", "sections.masking", None, False),
    ("advanced", "sections.advancedFeatures", "tooltips.advancedFeatures", True),
    ("export", "sections.export", "tooltips.export", False),
]

# One row per control, in panel order. Fields:
#   s section, k BumpMesh setting name (or the control's own name for buttons),
#   t type (hidden = a stored setting with no widget), id HTML id (slider for sliders), box HTML id of the typed box,
#   l label key, tip tooltip key, sc scope (layer | global | frame | action | ui),
#   opts [(value, label key, html id, tooltip key)] for buttons, d default override
#   with its BumpMesh source, show {key: [values]} shown only then (as BumpMesh
#   hides the row; "never": BumpMesh keeps it hidden), later = not in v1.
G, L = "global", "layer"
SPEC = [
    # 0: header row (BumpMesh keeps it above the sidebar)
    # Undo and Redo are icons in BumpMesh: their label is the icon's word, the tooltip is BumpMesh's.
    dict(s="header", k="undo", t="button", id="undo-btn", text="Undo", tip="header.undo", sc="action"),
    dict(s="header", k="redo", t="button", id="redo-btn", text="Redo", tip="header.redo", sc="action"),
    dict(s="header", k="resetSettings", t="button", id="reset-settings-btn", l="header.resetSettings", tip="header.resetSettings", sc="action"),
    dict(s="header", k="saveProject", t="button", id="export-project-btn", l="header.save", tip="header.exportProject", sc="action"),
    dict(s="header", k="loadProject", t="button", id="import-project-input", l="header.load", tip="header.importProject", sc="action"),
    # 1: untitled Load
    dict(s="load", k="loadModel", t="button", id="stl-file-input", l="ui.loadStl", sc="action"),
    dict(s="load", k="placeOnFace", t="button", id="place-on-face-btn", l="ui.placeOnFace", tip="ui.placeOnFaceTitle", sc="action"),
    dict(s="load", k="rotate", t="toggle", id="rotate-btn", l="ui.rotate", tip="ui.rotateTitle", sc="ui", d=False),
    dict(s="load", k="rotateX", show={"rotate": [True]}, t="number", box="rotate-x", sc="frame", group="rotate"),
    dict(s="load", k="rotateY", show={"rotate": [True]}, t="number", box="rotate-y", sc="frame", group="rotate"),
    dict(s="load", k="rotateZ", show={"rotate": [True]}, t="number", box="rotate-z", sc="frame", group="rotate"),
    dict(s="load", k="rotateApply", show={"rotate": [True]}, t="button", id="rotate-apply-btn", l="ui.rotateApply", sc="action", group="rotate"),
    dict(s="load", k="rotateReset", show={"rotate": [True]}, t="button", id="rotate-reset-btn", l="ui.rotateReset", sc="action", group="rotate"),
    # 2: Displacement Map
    dict(s="displacementMap", k="addLayer", t="button", id="layer-add-btn", l="layers.add", tip="layers.addTitle", sc="action"),
    dict(s="displacementMap", k="textureGallery", t="button", id="texture-gallery-btn", l="ui.textureGallery", sc="action"),
    dict(s="displacementMap", k="uploadCustomMap", t="button", id="texture-file-input", l="ui.uploadCustomMap", sc="action"),
    dict(s="displacementMap", k="invertTexture", t="checkbox", id="invert-texture", l="labels.invertTexture", tip="tooltips.invertTexture", sc=L),
    dict(s="displacementMap", k="textureSmoothing", t="slider", id="texture-smoothing", box="texture-smoothing-val", l="labels.textureSmoothing", tip="tooltips.textureSmoothing", sc=L),
    # 3: Projection
    dict(s="projection", k="mappingMode", t="combo", id="mapping-mode", l="labels.mode", sc=L),
    dict(s="projection", k="mappingBlend", show={"never": [True]}, t="slider", id="seam-blend", box="seam-blend-val", l="labels.seamBlend", tip="tooltips.seamBlend", sc=L),
    dict(s="projection", k="seamBandWidth", t="slider", id="seam-band-width", box="seam-band-width-val", l="labels.transitionSmoothing", tip="tooltips.transitionSmoothing", sc=L),
    dict(s="projection", k="capAngle", show={"mappingMode": [3]}, t="slider", id="cap-angle", box="cap-angle-val", l="labels.capAngle", tip="tooltips.capAngle", sc=L),
    dict(s="projection", k="snapSeamlessWrap", show={"mappingMode": [3, 4], "stampOnce": [False]}, t="checkbox", id="cylinder-snap-toggle", l="labels.snapSeamless", tip="tooltips.snapSeamless", sc=L),
    dict(s="projection", k="cylinderAxis", show={"mappingMode": [3]}, t="heading", id="cylinder-axis-row", l="labels.cylinderAxis", sc="ui", note="ui.cylinderPanelLabel"),
    dict(s="projection", k="cylinderCenterX", show={"mappingMode": [3]}, t="number", sc=L, group="cylinder", added="Centre X (mm)", d=None),
    dict(s="projection", k="cylinderCenterY", show={"mappingMode": [3]}, t="number", sc=L, group="cylinder", added="Centre Y (mm)", d=None),
    dict(s="projection", k="cylinderRadius", show={"mappingMode": [3]}, t="number", sc=L, group="cylinder", added="Radius (mm)", d=None),
    dict(s="projection", k="cylinderAutofit", show={"mappingMode": [3]}, t="button", id="cylinder-autofit-btn", l="ui.cylinderAutofit", tip="tooltips.cylinderAutofit", sc="action", group="cylinder"),
    dict(s="projection", k="cylinderReset", show={"mappingMode": [3]}, t="button", id="cylinder-reset-btn", l="ui.cylinderReset", tip="tooltips.cylinderReset", sc="action", group="cylinder"),
    dict(s="projection", k="cylinderFromFace", show={"mappingMode": [3]}, t="button", sc="action", group="cylinder", added="From face",
         added_tip="Read the centre, axis and radius from a picked cylindrical face."),
    dict(s="projection", k="stampOnce", t="toggle", id="stamp-mode-btn", l="ui.stampMode", tip="tooltips.stamp", sc=L),
    dict(s="projection", k="stampPlace", show={"stampOnce": [True]}, t="button", id="stamp-place-btn", l="ui.stampPlace", tip="tooltips.stampPlace", sc="action"),
    # Where the stamp sits (Position on model writes them): per layer in
    # BumpMesh's LAYER_KEYS, no widget of their own.
    dict(s="projection", k="stampCenterX", t="hidden", sc=L),
    dict(s="projection", k="stampCenterY", t="hidden", sc=L),
    dict(s="projection", k="stampCenterZ", t="hidden", sc=L),
    dict(s="projection", k="stampSide", t="hidden", sc=L),
    dict(s="projection", k="stampNormalX", t="hidden", sc=L),
    dict(s="projection", k="stampNormalY", t="hidden", sc=L),
    dict(s="projection", k="stampNormalZ", t="hidden", sc=L),
    # 4: Texture Depth
    dict(s="textureDepth", k="amplitude", t="slider", id="amplitude", box="amplitude-val", l="labels.textureHeight", sc=L),
    dict(s="textureDepth", k="invertDisplacement", t="checkbox", id="invert-displacement", l="labels.invertDisplacement", sc=L),
    dict(s="textureDepth", k="symmetricDisplacement", t="checkbox", id="symmetric-displacement", l="labels.symmetricDisplacement", tip="tooltips.symmetricDisplacement", sc=L),
    dict(s="textureDepth", k="useDisplacement", t="checkbox", id="displacement-preview", l="labels.displacementPreview", tip="tooltips.displacementPreview", sc=G),
    dict(s="textureDepth", k="previewQuality", t="buttons", sc=G, added="Quality", d="draft",
         added_opts=[("draft", "Draft"), ("fine", "Fine")],
         added_tip="Preview triangle budget: Draft 500 k, Fine 1.5 M (BumpMesh's value)."),
    # 5: Transform
    dict(s="transform", k="scaleManualHint", t="note", l="labels.scaleManualHint", sc="ui"),
    dict(s="transform", k="scaleU", t="log slider", id="scale-u", box="scale-u-val", l="labels.textureSizeU", tip="tooltips.textureSize", sc=L),
    dict(s="transform", k="lockScale", t="toggle", id="lock-scale", l=None, tip="tooltips.proportionalScaling", sc=L),
    dict(s="transform", k="scaleV", t="log slider", id="scale-v", box="scale-v-val", l="labels.textureSizeV", tip="tooltips.textureSize", sc=L),
    dict(s="transform", k="offsetU", t="slider", id="offset-u", box="offset-u-val", l="labels.offsetU", sc=L),
    dict(s="transform", k="offsetV", t="slider", id="offset-v", box="offset-v-val", l="labels.offsetV", sc=L),
    dict(s="transform", k="rotation", t="slider", id="rotation", box="rotation-val", l="labels.rotation", sc=L),
    dict(s="transform", k="rotateAroundZ", show={"mappingMode": [5, 6]}, t="checkbox", id="rotate-around-z", l="labels.rotateAroundZ", tip="tooltips.rotateAroundZ", sc=L),
    # 6: Masking
    dict(s="masking", k="maskAngles", t="heading", l="sections.maskAngles", tip="tooltips.maskAngles", sc="ui"),
    dict(s="masking", k="bottomAngleLimit", t="slider", id="bottom-angle-limit", box="bottom-angle-limit-val", l="labels.bottomFaces", tip="tooltips.bottomFaces", sc=G),
    dict(s="masking", k="topAngleLimit", t="slider", id="top-angle-limit", box="top-angle-limit-val", l="labels.topFaces", tip="tooltips.topFaces", sc=G),
    dict(s="masking", k="surfaceMasking", t="heading", id="excl-section-heading", l="sections.surfaceMasking", tip="tooltips.surfaceMasking", sc="ui"),
    dict(s="masking", k="includeOnly", t="buttons", sc=L, d=False, d_src="main.js _newLayer includeOnly",
         opts=[(False, "excl.modeExclude", "excl-mode-exclude", "excl.modeExcludeTitle"),
               (True, "excl.modeIncludeOnly", "excl-mode-include", "excl.modeIncludeOnlyTitle")]),
    dict(s="masking", k="exclusionTool", t="buttons", sc="ui", d=None, d_src="main.js exclusionTool",
         opts=[("faces", None, None, None), ("brush", "excl.toolBrush", "excl-brush-btn", "excl.toolBrushTitle"),
               ("bucket", "excl.toolFill", "excl-bucket-btn", "excl.toolFillTitle")],
         added_opts=[("faces", "Faces")]),
    dict(s="masking", k="maskFaces", show={"exclusionTool": ["faces"]}, t="faces", sc=L, group="faces", added="Faces",
         added_tip="Picked faces of the body; Add by… selects by facing direction, box or same surface."),
    dict(s="masking", k="brushIsRadius", show={"exclusionTool": ["brush"]}, t="buttons", sc="ui", group="brush", l="labels.type",
         opts=[(False, "brushType.single", "excl-brush-single", None), (True, "brushType.circle", "excl-brush-radius-btn", None)]),
    # Standard marks whole triangles; Precision (paintTree, design 4.2 P4) refines under the stroke and
    # is the only mode with Hardness, as main.js's setBrushPrecision shows and hides the row.
    dict(s="masking", k="brushPrecision", show={"exclusionTool": ["brush"], "brushIsRadius": [True]}, t="buttons", sc="ui", group="brush", l="brushMode.label",
         d=True, d_src="main.js brushPrecision",
         opts=[(False, "brushMode.standard", "excl-brush-standard", "brushMode.standardTitle"),
               (True, "brushMode.precision", "excl-brush-precision", "brushMode.precisionTitle")]),
    dict(s="masking", k="brushSize", show={"exclusionTool": ["brush"], "brushIsRadius": [True]}, t="slider", id="excl-brush-radius-slider", box="excl-brush-radius-val", l="labels.size", sc="ui", group="brush"),
    dict(s="masking", k="brushHardness", show={"exclusionTool": ["brush"], "brushIsRadius": [True], "brushPrecision": [True]}, t="slider", id="excl-brush-hardness-slider", box="excl-brush-hardness-val", l="labels.hardness", tip="tooltips.hardness", sc="ui", group="brush"),
    dict(s="masking", k="bucketThreshold", show={"exclusionTool": ["bucket"]}, t="slider", id="excl-threshold-slider", box="excl-threshold-val", l="labels.maxAngle", tip="tooltips.maxAngle", sc="ui", group="bucket"),
    dict(s="masking", k="shiftHint", t="note", id="excl-shift-hint", l="excl.shiftHint", sc="ui"),
    dict(s="masking", k="clearAll", t="button", id="excl-clear-btn", l="ui.clearAll", sc="action"),
    dict(s="masking", k="smoothMask", t="heading", l="sections.smoothMask", tip="tooltips.boundaryFalloff", sc="ui"),
    dict(s="masking", k="boundaryFalloff", t="slider", id="boundary-falloff", box="boundary-falloff-val", l="labels.falloffDistance", tip="tooltips.falloffDistance", sc=L),
    dict(s="masking", k="boundaryFalloffCurve", t="buttons", l="labels.falloffCurve", tip="tooltips.falloffCurve", sc=L,
         opts=[("linear", "falloffCurve.linear", "falloff-curve-linear", "falloffCurve.linearTitle"),
               ("scurve", "falloffCurve.scurve", "falloff-curve-scurve", "falloffCurve.scurveTitle"),
               ("ease", "falloffCurve.ease", "falloff-curve-ease", "falloffCurve.easeTitle")]),
    # 7: Advanced / Beta Features
    dict(s="advanced", k="advancedBlurb", t="note", l="ui.advancedBlurb", sc="ui"),
    dict(s="advanced", k="noDownwardZHeading", t="heading", l="ui.noDownwardZHeading", note="ui.noDownwardZDesc", sc="ui"),
    dict(s="advanced", k="noDownwardZ", t="checkbox", id="no-downward-z-chk", l="ui.noDownwardZ", sc=G),
    dict(s="advanced", k="smoothBottomHeading", t="heading", l="ui.smoothBottomHeading", note="ui.smoothBottomDesc", sc="ui"),
    dict(s="advanced", k="smoothBottom", t="checkbox", id="smooth-bottom-chk", l="ui.smoothBottom", sc=G),
    dict(s="advanced", k="harvestFlatHeading", t="heading", l="ui.harvestFlatHeading", note="ui.harvestFlatDesc", sc="ui"),
    dict(s="advanced", k="harvestFlatFaces", t="checkbox", id="harvest-flat-chk", l="ui.harvestFlat", sc=G),
    dict(s="advanced", k="harvestTol", t="number", box="harvest-tol", l="ui.harvestTol", tip="tooltips.harvestTol", sc=G),
    dict(s="advanced", k="preserveUntexturedHeading", t="heading", l="ui.preserveUntexturedHeading", note="ui.preserveUntexturedDesc", sc="ui"),
    dict(s="advanced", k="preserveUntextured", t="checkbox", id="preserve-untextured-chk", l="ui.preserveUntextured", sc=G),
    dict(s="advanced", k="bakeTexturesHeading", t="heading", l="ui.bakeTexturesHeading", note="ui.bakeTexturesDesc", sc="ui"),
    dict(s="advanced", k="bakeMask", t="checkbox", id="bake-mask-chk", l="ui.bakeMaskNewFaces", sc=G),
    dict(s="advanced", k="bakeTextures", t="button", id="bake-btn", l="ui.bakeTextures", tip="tooltips.bakeTextures", sc="action"),
    # 8: Export
    dict(s="export", k="smartRes", t="button", id="smart-res-btn", l="labels.smartRes", tip="tooltips.smartRes", sc="action"),
    dict(s="export", k="refineLength", t="slider", id="refine-length", box="refine-length-val", l="labels.resolution", tip="tooltips.resolution", sc=G,
         captions=("labels.resCoarse", "labels.resFine")),
    dict(s="export", k="maxTriangles", t="slider", id="max-triangles", box=None, l="labels.outputTriangles", tip="tooltips.outputTriangles", sc=G, readout="M"),
    dict(s="export", k="estimate", t="note", sc="ui", added="Estimate: —"),
    dict(s="export", k="addToDocument", t="button", sc="action", added="Add to document",
         added_tip="Keep the textured mesh in the document as a Textured object (Update re-runs it)."),
    dict(s="export", k="exportStl", t="button", id="export-btn", l="ui.exportStl", tip="tooltips.exportStl", sc="action"),
    dict(s="export", k="export3mf", t="button", id="export-3mf-btn", l="ui.export3mf", tip="tooltips.export3mf", sc="action"),
]

# Strings the layer strip, the gallery and the status lines use, from en.js.
STRING_KEYS = [
    "layers.add", "layers.addTitle", "layers.max", "layers.name", "layers.rowTitle", "layers.hide",
    "layers.show", "layers.remove", "layers.blendCover", "layers.blendAdd", "layers.blendCoverTitle",
    "layers.blendAddTitle", "ui.noFavourites", "ui.meshInfo", "ui.loadingTextures",
    "gallery.title", "gallery.search", "gallery.filterLabel", "gallery.catAll", "gallery.catFavourites",
    "gallery.catGeometric", "gallery.catPatterns", "gallery.catOrganic", "gallery.catFabric",
    "gallery.catNatural", "gallery.catGrip", "gallery.catMold", "gallery.catTires", "gallery.catCustom",
    "gallery.hint", "gallery.done", "gallery.turntable", "gallery.turntableTitle", "gallery.favCount",
    "gallery.addFavourite", "gallery.removeFavourite", "gallery.resetFavourites", "gallery.noResults",
    "gallery.customTitle", "gallery.customEmpty", "gallery.downloadCustom", "gallery.deleteCustom",
    "gallery.deleteAllCustom", "gallery.credits", "excl.initExcluded", "excl.faceExcluded",
    "excl.facesExcluded", "excl.faceSelected", "excl.facesSelected", "excl.hintExclude",
    "excl.hintInclude", "warnings.textureHeightOverlap", "warnings.resolutionTooCoarse",
    "warnings.safetyCapHit", "ui.smartResInfo", "progress.processing", "progress.done",
    "ui.outlineWrapFallback", "ui.stampHint", "alerts.resetConfirm", "header.exportSettingsLabel",
    "header.exportModelLabel", "header.exportTextureLabel", "header.exportGo", "header.loadDialogTitle",
    "header.loadModelLabel", "header.loadSettingsLabel", "header.loadGo",
]


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def parse_en(text):
    """{key: text} from en.js (one `"key": "value",` per line)."""
    out = {}
    for m in re.finditer(r'^\s*"([^"]+)":\s*("(?:[^"\\]|\\.)*")\s*,?\s*$', text, re.M):
        out[m.group(1)] = json.loads(m.group(2))
    return out


class _Html(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids, self.selects, self._sel, self.i18n = {}, {}, None, set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        a["_tag"] = tag
        for k in ("data-i18n", "data-i18n-title", "data-i18n-opt"):
            if a.get(k):
                self.i18n.add(a[k])
        if a.get("id"):
            self.ids[a["id"]] = a
        if tag == "select":
            self._sel = a["id"]
            self.selects[self._sel] = []
        if tag == "option" and self._sel:
            self.selects[self._sel].append(a)

    def handle_endtag(self, tag):
        if tag == "select":
            self._sel = None


def parse_html(text):
    p = _Html()
    p.feed(text)
    return p


def _num(v):
    if v is None or v == "any":
        return v
    f = float(v)
    return int(f) if f == int(f) and "." not in v else f


def _js(v):
    v = v.strip().rstrip(",").strip()
    if v in ("true", "false"):
        return v == "true"
    if v == "null":
        return None
    if v[:1] in "'\"":
        return v[1:-1]
    return _num(v.replace("_", ""))


def parse_settings(main):
    """main.js's `const settings = {...}` as {key: value} (plain literals only)."""
    body = main.split("const settings = {", 1)[1].split("\n};", 1)[0]
    out = {}
    for m in re.finditer(r"^\s*(\w+):\s*([^,/\n]+),", body, re.M):
        out[m.group(1)] = _js(m.group(2))
    return out


def parse_const(text, name):
    m = re.search(r"const %s\s*=\s*([^;]+);" % re.escape(name), text)
    return _js(m.group(1))


def parse_categories(presets):
    body = presets.split("const PRESET_CATEGORIES = [", 1)[1].split("];", 1)[0]
    return [{"id": i, "label_key": k} for i, k in re.findall(r"id:\s*'(\w+)',\s*label:\s*'([\w.]+)'", body)]


def parse_favourites(presets):
    body = presets.split("const DEFAULT_FAVOURITES = [", 1)[1].split("];", 1)[0]
    return re.findall(r"'([^']+)'", body)


def parse_credits(gallery):
    body = gallery.split("const CREDITS = {", 1)[1].split("};", 1)[0]
    return {k: {"badge": b, "title": t} for k, b, t in re.findall(r"(\w+):\s*\{\s*badge:\s*'([^']+)',\s*title:\s*'([^']+)'", body)}


class SpecError(Exception):
    pass


def build(src):
    en = parse_en(read(os.path.join(src, "js", "i18n", "en.js")))
    page = parse_html(read(os.path.join(src, "index.html")))
    main = read(os.path.join(src, "js", "main.js"))
    presets = read(os.path.join(src, "js", "presetTextures.js"))
    gallery = read(os.path.join(src, "js", "textureGallery.js"))
    material = read(os.path.join(src, "js", "previewMaterial.js"))
    settings = parse_settings(main)

    def text(key, where):
        if key is None:
            return None
        if key not in en:
            raise SpecError(f"{where}: {key} not in en.js")
        return en[key]

    def el(i, where):
        if i not in page.ids:
            raise SpecError(f"{where}: #{i} not in index.html")
        return page.ids[i]

    rows = []
    layer_keys = re.findall(r"'(\w+)'", main.split("const LAYER_KEYS = [", 1)[1].split("];", 1)[0])
    # textureHeight is |amplitude| in BumpMesh; project.py writes and reads it from amplitude.
    missing = [k for k in layer_keys if k != "textureHeight" and not any(sp["k"] == k and sp["sc"] == L for sp in SPEC)]
    if missing:
        raise SpecError(f"LAYER_KEYS without a layer row: {missing}")
    for sp in SPEC:
        k, t = sp["k"], sp["t"]
        row = {"section": sp["s"], "key": k, "type": t, "scope": sp["sc"], "origin": "added" if sp.get("added") else "bumpmesh",
               "label": sp.get("added") or sp.get("text") or text(sp.get("l"), k),
               "label_key": None if sp.get("added") else sp.get("l"),
               "tooltip": sp.get("added_tip") if sp.get("added") else text(sp.get("tip"), k),
               "group": sp.get("group"), "show": sp.get("show"), "later": sp.get("later"), "slider": None, "box": None, "default": None, "options": None, "note": text(sp.get("note"), k)}
        for lk in [sp.get("l"), sp.get("tip")] + [o[1] for o in sp.get("opts", [])] + [o[3] for o in sp.get("opts", [])]:
            if lk and not sp.get("added") and lk not in page.i18n:
                raise SpecError(f"{k}: {lk} is not used in index.html")
        if t == "number" and sp.get("box") and not sp.get("added"):
            b = el(sp["box"], k)
            row["box"] = {"min": _num(b.get("min")), "max": _num(b.get("max")), "step": _num(b.get("step"))}
            row["default"] = _num(b.get("value"))
            if row["label"] is None:  # rotate X/Y/Z: the HTML label text, "X°"
                row["label"] = sp["box"][-1].upper() + "°"
        elif t in ("slider", "log slider"):
            s = el(sp["id"], k)
            row["slider"] = {"min": _num(s["min"]), "max": _num(s["max"]), "step": _num(s["step"])}
            if sp.get("box"):
                b = el(sp["box"], k)
                row["box"] = {"min": _num(b.get("min")), "max": _num(b.get("max")), "step": _num(b.get("step"))}
                row["default"] = _num(b.get("value"))
            else:
                row["default"] = _num(s["value"])
            if t == "log slider":
                row["slider"]["log"] = {"rel_min": parse_const(main, "SCALE_REL_SLIDER_MIN"),
                                        "rel_max": parse_const(main, "SCALE_REL_SLIDER_MAX"), "anchor_mm": 50,
                                        "tile_fraction": parse_const(main, "DEFAULT_TILE_FRACTION")}
            if sp.get("captions"):
                row["captions"] = [text(c, k) for c in sp["captions"]]
            if sp.get("readout"):
                row["readout"] = sp["readout"]
        elif t == "checkbox":
            row["default"] = "checked" in el(sp["id"], k)
        elif t == "toggle" and sp.get("id"):
            e = el(sp["id"], k)
            row["default"] = sp["d"] if "d" in sp else ("active" in e.get("class", "").split() or e.get("aria-pressed") == "true")
            if "d" not in sp and e.get("aria-pressed") is not None and e["aria-pressed"] not in ("true", "false"):
                raise SpecError(k)
        elif t == "combo":
            row["options"] = [{"value": _num(o["value"]), "label": text(o["data-i18n-opt"], k), "label_key": o["data-i18n-opt"],
                               "hidden": "hidden" in o} for o in page.selects[sp["id"]]]
            sel = [o for o in page.selects[sp["id"]] if "selected" in o]
            row["default"] = _num(sel[0]["value"])
        elif t == "buttons":
            if sp.get("opts"):
                added = dict(sp.get("added_opts", []))
                row["options"] = []
                active = None
                for v, lk, i, tk in sp["opts"]:
                    if lk is None:
                        row["options"].append({"value": v, "label": added[v], "label_key": None, "tooltip": None, "origin": "added"})
                        continue
                    e = el(i, k)
                    if "active" in e.get("class", "").split():
                        active = v
                    row["options"].append({"value": v, "label": text(lk, k), "label_key": lk, "tooltip": text(tk, k), "origin": "bumpmesh"})
                    if v in sp.get("later_opts", {}):
                        row["options"][-1]["later"] = sp["later_opts"][v]
                row["default"] = sp["d"] if "d" in sp else active
                row["default_source"] = sp.get("d_src", "index.html")
            else:
                row["options"] = [{"value": v, "label": lab, "label_key": None, "tooltip": None, "origin": "added"} for v, lab in sp["added_opts"]]
                row["default"] = sp["d"]
        elif t == "hidden":
            if k not in settings:
                raise SpecError(f"{k}: not in main.js settings")
            row["default"] = settings[k]
        elif "d" in sp:
            row["default"] = sp["d"]
        if sp.get("added") and t == "heading":
            row["label"] = sp["added"]
        if not sp.get("added") and sp.get("id") and t in ("button", "toggle", "heading", "note"):
            el(sp["id"], k)
        if t == "heading" and sp.get("l") and row["label"] is None:
            raise SpecError(k)
        # The page's defaults must be main.js's.
        if k in settings and row["default"] != settings[k]:
            raise SpecError(f"{k}: index.html default {row['default']!r} != main.js {settings[k]!r}")
        rows.append({x: v for x, v in row.items() if v is not None or x in ("default",)})

    sections = []
    for sid, hk, tk, collapsed in SECTIONS:
        sections.append({"id": sid, "title": text(hk, sid), "title_key": hk, "tooltip": text(tk, sid), "collapsed": collapsed})
    have = shipped()
    data = {
        "SECTIONS": sections,
        "CONTROLS": rows,
        "STRINGS": {k: text(k, "STRINGS") for k in STRING_KEYS},
        "CATEGORIES": [dict(c, label=text(c["label_key"], "categories")) for c in parse_categories(presets)],
        # BumpMesh's favourites less the textures we do not ship (Filter Forge).
        "NOT_SHIPPED": not_shipped([parse_const(main, "DEFAULT_PRESET_NAME")] + parse_favourites(presets), have),
        "DEFAULT_FAVOURITES": [n for n in parse_favourites(presets) if n in have],
        "CREDITS": parse_credits(gallery),
        "MAX_LAYERS": parse_const(material, "MAX_LAYERS"),
        "UNDO_LIMIT": parse_const(main, "UNDO_LIMIT"),
        # BumpMesh's default, or its next default (the first favourite) we ship.
        "DEFAULT_TEXTURE": next(n for n in [parse_const(main, "DEFAULT_PRESET_NAME")] + parse_favourites(presets) if n in have),
    }
    return data


HEADER = '''# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 CNCKitchen (Stefan Hermann) and contributors; the
# Texturizer for FreeCAD contributors.
# GENERATED by tests/tools/gen_controls.py from BumpMesh (stlTexturizer) by
# Stefan Hermann (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer:
# js/i18n/en.js, index.html, js/main.js, js/presetTextures.js,
# js/textureGallery.js, js/previewMaterial.js. Do not edit by hand; rerun it.
"""Every control of the Texturizer sidebar, as data: BumpMesh's sections in
BumpMesh's order, with its labels, slider and typed ranges, defaults and
English tooltips (credited to Stefan Hermann / CNC Kitchen). Rows with
origin "added" are Texturizer's own (design section 2).

Row fields: section, key (BumpMesh's setting name, or the control's own name
for buttons), type (slider | log slider | checkbox | combo | buttons | toggle |
number | button | heading | note | faces | hidden = stored, no widget), scope (layer = per layer, BumpMesh's
LAYER_KEYS; global; frame = the texturing frame; action = runs a tool; ui =
panel state), label, label_key (en.js), tooltip, slider {min, max, step[, log]},
box {min, max, step} (the typed box), default, options, group, note.
"""

'''

FOOTER = '''

BY_KEY = {c["key"]: c for c in CONTROLS}


def defaults(scope):
    """{key: default} for one scope (layer, global, frame, ui)."""
    return {c["key"]: c["default"] for c in CONTROLS if c["scope"] == scope and c["type"] not in ("heading", "note", "button")}


def section(sid):
    return [c for c in CONTROLS if c["section"] == sid]


def string(key, **kw):
    """One of BumpMesh's strings, with {n}-style fields filled."""
    s = STRINGS[key]
    for k, v in kw.items():
        s = s.replace("{%s}" % k, str(v))
    return s
'''


def render(data):
    out = [HEADER]
    for name, value in data.items():
        out.append(f"{name} = {pprint.pformat(value, width=110, sort_dicts=False)}\n\n")
    return "".join(out).rstrip("\n") + "\n" + FOOTER


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    src = args[0] if args else os.environ.get("BUMPMESH_SRC", DEFAULT_SRC)
    text = render(build(src))
    if "--check" in argv:
        same = os.path.exists(OUT) and read(OUT) == text
        print("controls.py is current" if same else "controls.py differs from a fresh parse")
        return 0 if same else 1
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    ast.parse(text)
    print(f"wrote {os.path.relpath(OUT, ROOT)}: {len(data_rows(text))} controls")
    return 0


def data_rows(text):
    ns = {}
    exec(compile(text, "controls.py", "exec"), ns)  # our own output
    return ns["CONTROLS"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
