# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer. The layer
# slots and uniforms follow _previewLayers in js/main.js and updateMaterial in
# js/previewMaterial.js.
"""The shader Quick look (design 3.4): uniforms from the draft, the Coin
scene, and detection.

    uniforms_from_layer(layer, frame, index)    pure: one layer's uniforms
    uniforms_from_layers(layers, frame, active) pure: all of them, the slots
                                                 and the globals
    layer_slots(layers, active)                 which draft layers get a slot
    pack_weights(W)                             (N, 4) weights -> RGBA uint32
    QuickLook                                   the Coin nodes (pivy, lazily)
    detect(view, params, fc_version)            can this GL run the shader?

frame: {"bounds": {min, size, center} in the texturing frame, "rotation":
[x, y, z] degrees (the draft's frame) or "matrix": 16 floats (object ->
frame, row-major, column vectors)}. Only the GUI thread touches Coin.
"""
import json
import math
import re

import numpy as np

from . import quicklook_glsl, uvmath
from .engine import frame_matrix as engine_frame_matrix

MAX_LAYERS = quicklook_glsl.MAX_LAYERS
MAP_PX = 512          # BumpMesh's height-map size
MAP_PX_CUSTOM = 2048  # custom maps are capped here
PARAM_PREFIX = "QuickLook"


# -- uniforms ------------------------------------------------------------------
def frame_matrix(rotation):
    """Object -> frame rotation as 16 floats, row-major with column vectors:
    exactly the engine's frame_matrix, BumpMesh's Rotate X / Y / Z (three.js
    Euler 'XYZ', R = Rx·Ry·Rz)."""
    m = np.eye(4)
    m[:3, :3] = engine_frame_matrix((list(rotation or []) + [0, 0, 0])[:3])
    return [float(x) for x in m.ravel()]


def _frame_m(frame):
    m = frame.get("matrix")
    return [float(x) for x in m] if m is not None else frame_matrix(frame.get("rotation"))


def frame_bounds(points, frame):
    """The bounds of points (N, 3) in the texturing frame."""
    m = np.array(_frame_m(frame)).reshape(4, 4)
    p = np.asarray(points, float).reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
    return uvmath.bounds_of(p)


def _shown(layers):
    solo = any(L.get("solo") for L in layers)
    return [bool(L.get("solo")) if solo else bool(L.get("visible", True)) for L in layers]


def layer_slots(layers, active=0):
    """[(draft index, shown)]: the shown layers and the active one (a hidden
    active layer keeps its slot with no relief), at most MAX_LAYERS, bottom
    first (BumpMesh's _layerSlots)."""
    shown = _shown(layers)
    out = []
    for i, L in enumerate(layers):
        if len(out) >= MAX_LAYERS:
            break
        if shown[i] or i == active:
            out.append((i, shown[i]))
    return out


def uniforms_from_layer(layer, frame, index=0, shown=True, map_size=None):
    """One draft layer's uniforms for slot index (the uMode<index> ...
    names). map_size: the height map's (width, height) in texels."""
    s = layer.get("settings", layer)
    b = frame["bounds"]
    mn, size, center = uvmath._b(b)
    mode = int(s.get("mappingMode", 5) if s.get("mappingMode") is not None else 5)
    if mode == uvmath.MODE_OUTLINE_WRAP:
        mode = uvmath.MODE_CYLINDRICAL  # no outline tables in the Quick look
    if mode not in range(7):
        mode = uvmath.MODE_TRIPLANAR  # surface-normal stamps: not in the Quick look
    tw, th = map_size or (1, 1)
    tmax = max(tw, th, 1)
    a_u, a_v = tmax / max(tw, 1), tmax / max(th, 1)
    rel_u, rel_v = uvmath.scale_mm_to_relative(mode, s, b)
    amp = abs(float(s.get("amplitude") or 0))
    if s.get("invertDisplacement"):
        amp = -amp
    if not shown:
        amp = 0.0
    k = index
    return {
        f"uMode{k}": float(mode),
        f"uScale{k}": (rel_u / a_u, rel_v / a_v),
        f"uOffset{k}": (float(s.get("offsetU") or 0), float(s.get("offsetV") or 0)),
        f"uRot{k}": float(s.get("rotation") or 0) * math.pi / 180,
        f"uRotZ{k}": 1.0 if uvmath.is_rotate_around_z(s) else 0.0,
        f"uAmp{k}": amp,
        f"uSym{k}": 1.0 if s.get("symmetricDisplacement") else 0.0,
        f"uAdd{k}": 1.0 if (index > 0 and layer.get("blendAdd")) else 0.0,
        f"uCyl{k}": (float(uvmath._g(s, "cylinderCenterX", center[0])),
                     float(uvmath._g(s, "cylinderCenterY", center[1])),
                     float(uvmath._g(s, "cylinderRadius", max(size[0], size[1]) * 0.5))),
        f"uSeam{k}": (float(uvmath._g(s, "mappingBlend", 0)), float(uvmath._g(s, "seamBandWidth", 0.35)),
                      float(uvmath._g(s, "capAngle", 20))),
    }


def _inert(k):
    return {f"uMode{k}": 5.0, f"uScale{k}": (1.0, 1.0), f"uOffset{k}": (0.0, 0.0), f"uRot{k}": 0.0,
            f"uRotZ{k}": 0.0, f"uAmp{k}": 0.0, f"uSym{k}": 0.0, f"uAdd{k}": 0.0,
            f"uCyl{k}": (0.0, 0.0, 1.0), f"uSeam{k}": (0.0, 0.35, 20.0)}


def uniforms_from_layers(layers, frame, active=0, map_sizes=None):
    """Every uniform but the samplers: the slots of layer_slots, the unused
    slots inert, and uLayerCount, uActive, uFrame, uBounds*."""
    slots = layer_slots(layers, active)
    u = {}
    active_slot = 0
    for k in range(MAX_LAYERS):
        if k < len(slots):
            i, shown = slots[k]
            size = (map_sizes or {}).get(i) if isinstance(map_sizes, dict) else None
            u.update(uniforms_from_layer(layers[i], frame, k, shown, size))
            if i == active:
                active_slot = k
        else:
            u.update(_inert(k))
    mn, size, center = uvmath._b(frame["bounds"])
    u.update({"uLayerCount": float(len(slots)), "uActive": float(active_slot),
              "uFrame": tuple(_frame_m(frame)), "uBoundsMin": tuple(map(float, mn)),
              "uBoundsSize": tuple(map(float, size)), "uBoundsCenter": tuple(map(float, center))})
    return u


def pack_weights(W):
    """(N, 4) layer weights in 0..1 -> SoPackedColor's 0xRRGGBBAA per vertex."""
    q = np.clip(np.floor(np.asarray(W, float).reshape(-1, 4) * 255 + 0.5), 0, 255).astype(np.uint32)
    return (q[:, 0] << 24) | (q[:, 1] << 16) | (q[:, 2] << 8) | q[:, 3]


# -- Coin ------------------------------------------------------------------------
def _coin():
    from pivy import coin  # noqa: PLC0415  (lazy: FreeCAD's GUI only)

    return coin


class QuickLook:
    """The Quick look's nodes: an SoSwitch (root) over a separator with the
    pick style (UNPICKABLE), one SoTextureUnit + SoTexture2 per layer, the
    SoShaderProgram with its parameters, and the part's own tessellation
    (indexed) with the weights in an SoPackedColor per vertex."""

    def __init__(self, coin=None):
        self.coin = coin or _coin()
        self.params = {}
        self.textures = []
        self._last = {}
        c = self.coin
        self.root = c.SoSwitch()
        self.root.setName("TexturizerQuickLook")
        self.root.whichChild = -1
        sep = c.SoSeparator()
        self.root.addChild(sep)
        pick = c.SoPickStyle()
        pick.style = c.SoPickStyle.UNPICKABLE
        sep.addChild(pick)
        for k in reversed(range(MAX_LAYERS)):  # unit 0 last, so it stays current
            unit = c.SoTextureUnit()
            unit.unit = k
            tex = c.SoTexture2()
            tex.wrapS = c.SoTexture2.REPEAT
            tex.wrapT = c.SoTexture2.REPEAT
            sep.addChild(unit)
            sep.addChild(tex)
            self.textures.insert(0, tex)
        self.program = c.SoShaderProgram()
        vs, fs = c.SoVertexShader(), c.SoFragmentShader()
        for obj, src in ((vs, quicklook_glsl.VERTEX), (fs, quicklook_glsl.FRAGMENT)):
            obj.sourceType = c.SoShaderObject.GLSL_PROGRAM
            obj.sourceProgram = src
        self.program.shaderObject.set1Value(0, vs)
        self.program.shaderObject.set1Value(1, fs)
        self._vs, self._fs = vs, fs
        sep.addChild(self.program)
        for k in range(MAX_LAYERS):
            p = c.SoShaderParameter1i()
            p.name = f"uMap{k}"
            p.value = k
            fs.parameter.set1Value(fs.parameter.getNum(), p)
        self.coords, self.normals = c.SoCoordinate3(), c.SoNormal()
        nb = c.SoNormalBinding()
        nb.value = c.SoNormalBinding.PER_VERTEX_INDEXED
        mb = c.SoMaterialBinding()
        mb.value = c.SoMaterialBinding.PER_VERTEX_INDEXED
        self.colors = c.SoPackedColor()
        self.faces = c.SoIndexedFaceSet()
        for n in (self.coords, self.normals, nb, mb, self.colors, self.faces):
            sep.addChild(n)

    # parameters ------------------------------------------------------------------
    def _param(self, name, value):
        c = self.coin
        node = self.params.get(name)
        if node is None:
            if name == "uFrame":
                node = c.SoShaderParameterMatrix()
            else:
                n = 1 if isinstance(value, float) else len(value)
                node = getattr(c, f"SoShaderParameter{n}f")()
            node.name = name
            self.params[name] = node
            self._fs.parameter.set1Value(self._fs.parameter.getNum(), node)
            if name == "uFrame":
                vnode = c.SoShaderParameterMatrix()
                vnode.name = name
                self.params["uFrame@vs"] = vnode
                self._vs.parameter.set1Value(self._vs.parameter.getNum(), vnode)
        return node

    def set_uniforms(self, uniforms):
        """Sets the uniforms that changed (a slider drag: a few fields)."""
        c = self.coin
        for name, value in uniforms.items():
            if self._last.get(name) == value:
                continue
            self._last[name] = value
            node = self._param(name, value)
            if name == "uFrame":
                m = np.array(value, float).reshape(4, 4).T  # SbMatrix: row vectors
                mat = c.SbMatrix(*[tuple(r) for r in m.tolist()])
                node.value = mat
                self.params["uFrame@vs"].value = mat
            elif isinstance(value, float):
                node.value = value
            else:
                node.value = getattr(c, f"SbVec{len(value)}f")(*value)

    def set_image(self, k, grey, width, height):
        """Layer k's height map: grey bytes, one per texel, rows bottom first."""
        self.textures[k].image.setValue(self.coin.SbVec2s(int(width), int(height)), 1, bytes(grey))

    # geometry --------------------------------------------------------------------
    def set_mesh(self, points, triangles, normals):
        p = np.asarray(points, float).reshape(-1, 3)
        t = np.asarray(triangles, np.int64).reshape(-1, 3)
        self.coords.point.setValues(0, len(p), p.tolist())
        self.coords.point.setNum(len(p))   # a mesh may shrink (the Precision brush's leaves merge back)
        self.normals.vector.setValues(0, len(p), np.asarray(normals, float).reshape(-1, 3).tolist())
        self.normals.vector.setNum(len(p))
        idx = np.hstack([t, -np.ones((len(t), 1), np.int64)]).ravel()
        self.faces.coordIndex.setValues(0, len(idx), idx.tolist())
        self.faces.coordIndex.setNum(len(idx))

    def set_weights(self, W):
        packed = pack_weights(W)
        self.colors.orderedRGBA.setValues(0, len(packed), [int(x) for x in packed])
        self.colors.orderedRGBA.setNum(len(packed))

    def show(self, on):
        self.root.whichChild = -3 if on else -1  # SO_SWITCH_ALL / NONE


# -- detection -------------------------------------------------------------------
_SESSION = {}


def parse_version(text):
    m = re.search(r"(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _pget(params, key, default=None):
    if params is None:
        return default
    if isinstance(params, dict):
        return params.get(key, default)
    try:  # a FreeCAD parameter group
        if isinstance(default, bool):
            return params.GetBool(key, default)
        return params.GetString(key, default or "") or default
    except Exception:
        return default


def _pset(params, key, value):
    if params is None:
        return
    if isinstance(params, dict):
        params[key] = value
        return
    try:
        if isinstance(value, bool):
            params.SetBool(key, value)
        else:
            params.SetString(key, value)
    except Exception:
        pass


def judge(info):
    """(ok, reason) for a probe's findings."""
    gl, glsl = parse_version(info.get("gl")), parse_version(info.get("glsl"))
    if gl < (2, 0):
        return False, f"OpenGL {info.get('gl') or 'unknown'} (2.0 needed)"
    if glsl < (1, 20):
        return False, f"GLSL {info.get('glsl') or 'unknown'} (1.20 needed)"
    if info.get("errors"):
        return False, "shader compile or link: " + "; ".join(map(str, info["errors"]))[:300]
    if not info.get("pixels_ok"):
        return False, "the offscreen probe did not draw the expected pixels"
    return True, ""


def detect(view, params=None, fc_version="", probe=None, log=None):
    """{"ok", "reason", "gpu", "fc"}: whether the Quick look can run. Once per
    session; the verdict is kept in params (Texturizer's parameters) with the
    FreeCAD version and GPU string and reused until either changes. The
    setting UseQuickLook = False turns it off. Never raises, never a dialog;
    the reason is logged once (log(text))."""
    try:
        if not _pget(params, "UseQuickLook", True):
            return {"ok": False, "reason": "turned off in Texturizer's settings", "gpu": "", "fc": fc_version}
        if view is None:
            return {"ok": False, "reason": "no 3D view", "gpu": "", "fc": fc_version}
        key = (id(params), fc_version)
        if key in _SESSION and probe is None:
            return dict(_SESSION[key])
        stored = _pget(params, PARAM_PREFIX + "Result")
        probe = probe or default_probe
        if stored:
            try:
                res = json.loads(stored)
            except ValueError:
                res = None
            gpu_now = _gpu_cheap(view) if probe is default_probe else None
            if res and res.get("fc") == fc_version and (gpu_now is None or gpu_now == res.get("gpu")):
                _SESSION[key] = res
                return dict(res)
        try:
            info = probe(view) or {}
        except Exception as e:  # the probe itself failed: no shader this time
            info = {"gl": "2.0", "glsl": "1.20", "errors": [f"probe failed: {e}"]}
        ok, reason = judge(info)
        res = {"ok": ok, "reason": reason, "gpu": str(info.get("gpu") or ""), "fc": fc_version}
        _pset(params, PARAM_PREFIX + "Result", json.dumps(res))
        _SESSION[key] = res
        if not ok and log:
            log(f"Texturizer: shader Quick look off ({reason}); the CPU preview is used.\n")
        return dict(res)
    except Exception as e:  # never raise into the GUI
        return {"ok": False, "reason": f"detection failed: {e}", "gpu": "", "fc": fc_version}


def _gl_strings(view):
    """GL_VERSION, GL_SHADING_LANGUAGE_VERSION and GL_RENDERER in the view's
    context (FreeCAD 1.x: a QOpenGLWidget viewport)."""
    w = view.graphicsView().viewport()
    w.makeCurrent()
    try:
        f = w.context().functions()
        return tuple(str(f.glGetString(e) or "") for e in (0x1F02, 0x8B8C, 0x1F01))
    finally:
        w.doneCurrent()


def _gpu_cheap(view):
    try:
        return _gl_strings(view)[2]
    except Exception:
        return None


PROBE_SIZE = 16


def default_probe(view):
    """Reads the GL strings, then renders a quad offscreen with the shader:
    left half weight 1 (teal), right half weight 0 (BumpMesh orange). Without
    a working shader Coin draws the packed weights as colours instead (red /
    black), so the halves tell. Coin's SoDebugError messages during the
    render count as compile or link errors."""
    coin = _coin()
    gl, glsl, gpu = _gl_strings(view)
    info = {"gl": gl, "glsl": glsl, "gpu": gpu, "errors": [], "pixels_ok": False}
    if parse_version(gl) < (2, 0) or parse_version(glsl) < (1, 20):
        return info
    errors = info["errors"]

    def hook(err, data=None):
        try:
            errors.append(err.getDebugString())
        except Exception:
            errors.append(str(err))

    try:
        coin.SoDebugError.setHandlerCallback(hook, None)
    except Exception:
        pass
    try:
        ql = QuickLook(coin)
        pts = [(-1, -1, 0), (0, -1, 0), (0, 1, 0), (-1, 1, 0), (0, -1, 0), (1, -1, 0), (1, 1, 0), (0, 1, 0)]
        ql.set_mesh(pts, [(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7)], [(0, 0, 1)] * 8)
        ql.set_weights([(1, 0, 0, 0)] * 4 + [(0, 0, 0, 0)] * 4)
        for k in range(MAX_LAYERS):
            ql.set_image(k, b"\x80" * 4, 2, 2)
        bounds = {"min": (-1, -1, 0), "size": (2, 2, 0), "center": (0, 0, 0)}
        layer = {"settings": {"mappingMode": 0, "scaleU": 1, "scaleV": 1, "amplitude": 0}}
        ql.set_uniforms(uniforms_from_layers([layer], {"bounds": bounds}))
        ql.show(True)
        scene = coin.SoSeparator()
        cam = coin.SoOrthographicCamera()
        cam.position = coin.SbVec3f(0, 0, 5)
        cam.height = 2.0
        light = coin.SoDirectionalLight()
        light.direction = coin.SbVec3f(0, 0, -1)
        for n in (cam, light, ql.root):
            scene.addChild(n)
        r = coin.SoOffscreenRenderer(coin.SbViewportRegion(PROBE_SIZE, PROBE_SIZE))
        r.setBackgroundColor(coin.SbColor(0, 0, 0))
        if not r.render(scene):
            errors.append("offscreen render failed")
            return info
        buf = r.getBuffer()
        data = bytes(buf) if not isinstance(buf, (bytes, bytearray)) else buf
        nc = max(3, len(data) // (PROBE_SIZE * PROBE_SIZE))
        row = PROBE_SIZE // 2

        def px(x):
            i = (row * PROBE_SIZE + x) * nc
            return data[i], data[i + 1], data[i + 2]

        lr, lg, lb = px(PROBE_SIZE // 4)
        rr, rg, rb = px(3 * PROBE_SIZE // 4)
        info["pixels_ok"] = lg > lr + 20 and lb > lr + 20 and rr > rg + 20 and rr > rb + 20
        return info
    finally:
        try:
            coin.SoDebugError.setHandlerCallback(None, None)
        except Exception:
            pass
