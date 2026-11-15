# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# The settings, defaults, layer model (LAYER_KEYS, MAX_LAYERS, Cover/Add) and
# undo limit follow BumpMesh (stlTexturizer) by Stefan Hermann (CNC Kitchen),
# AGPL-3.0, github.com/CNCKitchen/stlTexturizer (js/main.js).
"""The texture draft: plain data the sidebar, the gallery and the agent's
texture_* tools all edit.

    part      the body or Mesh object being textured (its Name), or None
    frame     the texturing frame: rotation [x, y, z] in degrees, face (the
              Place on Face pick) or None
    layers    up to MAX_LAYERS (4) layers, bottom first; each has id, name,
              texture (library id), visible, solo, blendAdd (Cover = False),
              includeOnly (By Surface mode), settings (BumpMesh's LAYER_KEYS)
              and mask {faces: [face names], triangles: [indices]}, plus
              soft {corners, values} where the brush painted soft coverage
              and split [[triangle, codes, states, cov]] where the
              Precision brush split triangles (paint.split_of)
    active    the index of the layer the sidebar edits
    settings  global settings (resolution, triangle limit, angle masks,
              Advanced, preview)
    ui        panel state that is not saved with a project (brush, tool)

Defaults come from controls.py (BumpMesh's own). JSON round-trips; one
undo/redo stack (BumpMesh's UNDO_LIMIT, 50); change signals are plain
callbacks: subscribe(fn) -> unsubscribe, fn(event) with event a dict
{kind, key, layer, value}. kinds: set, frame, part, layer, layers, active,
mask, texture, reset, load, undo, redo.

No Qt, FreeCAD or Agent Link import here.
"""
import copy
import json

from . import controls

FORMAT = "texturizer-draft"
VERSION = 1
MAX_LAYERS = controls.MAX_LAYERS
UNDO_LIMIT = controls.UNDO_LIMIT
BLEND_COVER, BLEND_ADD = False, True


def layer_defaults():
    d = controls.defaults("layer")
    d.pop("includeOnly", None)  # a layer property, as in BumpMesh's _newLayer
    d.pop("maskFaces", None)
    return d


def global_defaults():
    return controls.defaults("global")


def ui_defaults():
    return controls.defaults("ui")


def _clamp(c, v):
    """A value for control c, clamped to its typed range (the box), as
    BumpMesh's clampToInputBounds does."""
    t = c["type"]
    if t in ("checkbox", "toggle"):
        return bool(v)
    if t in ("combo", "buttons"):
        allowed = [o["value"] for o in c.get("options") or []]
        if v is None and c.get("default") is None and c["scope"] == "ui":
            return None  # a tool pressed again: no tool (BumpMesh's Brush / Fill)
        if allowed and v not in allowed:
            raise ValueError(f"{c['key']}: {v!r} is not one of {allowed}")
        return v
    if t == "hidden":  # a stored number with no widget (the stamp's place): None or as given
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{c['key']}: a number is needed")
        return v if isinstance(v, int) else float(v)
    if t in ("slider", "log slider", "number"):
        if v is None:
            if c["default"] is None:
                return None
            raise ValueError(f"{c['key']}: a number is needed")
        v = float(v)
        rng = c.get("box") or c.get("slider") or {}
        lo, hi = rng.get("min"), rng.get("max")
        if lo is not None:
            v = max(float(lo), v)
        if hi is not None:
            v = min(float(hi), v)
        if isinstance(c["default"], int) and not isinstance(c["default"], bool) and float(v).is_integer() \
                and (rng.get("step") in (None, 1) or c["key"] == "maxTriangles"):
            return int(v)
        return v
    return v


def _split_copy(split):
    """The Precision brush's split triangles (paint.split_of), as plain lists."""
    return [[int(r[0]), [int(c) for c in r[1]], [int(x) for x in r[2]], [float(v) for v in r[3]]] for r in split]


class Draft:
    def __init__(self, part=None):
        self._subs = []
        self._undo, self._redo = [], []
        self._batch = 0
        self._batch_before = None
        self._coalesce = None
        self._seq = 0
        self.part = part
        self.frame = {"rotation": [0, 0, 0], "face": None}
        self.layers = []
        self.active = 0
        self.settings = global_defaults()
        self.ui = ui_defaults()
        self.layers.append(self._new_layer())

    # -- layers --------------------------------------------------------------
    def _new_layer(self, include_only=False, texture=None, settings=None):
        self._seq += 1
        return {"id": self._seq, "name": controls.string("layers.name", n=len(self.layers) + 1),
                "texture": texture if texture is not None else controls.DEFAULT_TEXTURE,
                "visible": True, "solo": False, "blendAdd": BLEND_COVER, "includeOnly": include_only,
                "settings": dict(settings) if settings else layer_defaults(),
                "mask": {"faces": [], "triangles": []}}

    def layer(self, i=None):
        return self.layers[self.active if i is None else i]

    # -- reading ---------------------------------------------------------------
    def get(self, key, layer=None):
        """A setting by BumpMesh's name: the layer's (active unless given) for
        per-layer keys, else the global or panel value."""
        c = controls.BY_KEY.get(key)
        scope = c["scope"] if c else None
        if key == "includeOnly":
            return self.layer(layer)["includeOnly"]
        if scope == "layer":
            return self.layer(layer)["settings"].get(key)
        if scope == "frame":
            return self.frame["rotation"]["XYZ".index(key[-1])]
        if key in self.settings:
            return self.settings[key]
        if key in self.ui:
            return self.ui[key]
        raise KeyError(key)

    # -- signals ---------------------------------------------------------------
    def subscribe(self, fn):
        self._subs.append(fn)

        def unsubscribe():
            if fn in self._subs:
                self._subs.remove(fn)

        return unsubscribe

    def _emit(self, kind, key=None, layer=None, value=None):
        ev = {"kind": kind, "key": key, "layer": layer, "value": value}
        for fn in list(self._subs):
            fn(ev)

    # -- undo ------------------------------------------------------------------
    def _state(self):
        return copy.deepcopy({"part": self.part, "frame": self.frame, "layers": self.layers,
                              "active": self.active, "settings": self.settings, "seq": self._seq})

    def _restore(self, s):
        s = copy.deepcopy(s)
        self.part, self.frame, self.layers = s["part"], s["frame"], s["layers"]
        self.active, self.settings, self._seq = s["active"], s["settings"], s["seq"]

    def _push(self, coalesce=None):
        """Record the state before a change. Changes with the same coalesce
        tag in a row (one slider drag) make one undo step."""
        if self._batch:
            return
        if coalesce is not None and coalesce == self._coalesce:
            return
        self._coalesce = coalesce
        self._undo.append(self._state())
        if len(self._undo) > UNDO_LIMIT:
            self._undo.pop(0)
        self._redo.clear()

    def end_coalesce(self):
        """Close a coalesced run (slider released): the next change is a new step."""
        self._coalesce = None

    class _Batch:
        def __init__(self, d):
            self.d = d

        def __enter__(self):
            d = self.d
            if not d._batch:
                self.before, self.redo = d._state(), list(d._redo)
                d._push()
                d._coalesce = None
            d._batch += 1
            return d

        def __exit__(self, kind, *exc):
            d = self.d
            d._batch -= 1
            if d._batch:
                return
            # The outer batch: a batch that changed nothing leaves no undo step,
            # and one that failed is undone whole (a refused tool changes nothing).
            if kind is not None and d._state() != self.before:
                d._restore(self.before)
                d._emit("load")
            if d._state() == self.before:
                if d._undo and d._undo[-1] == self.before:
                    d._undo.pop()
                d._redo[:] = self.redo

    def batch(self):
        """with draft.batch(): ... several changes, one undo step."""
        return Draft._Batch(self)

    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def undo(self):
        if not self._undo:
            return False
        self._redo.append(self._state())
        self._restore(self._undo.pop())
        self._coalesce = None
        self._emit("undo")
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append(self._state())
        self._restore(self._redo.pop())
        self._coalesce = None
        self._emit("redo")
        return True

    # -- writing ---------------------------------------------------------------
    def set(self, key, value, layer=None, coalesce=False):
        """Set one control by BumpMesh's name; clamped to its typed range.
        coalesce=True folds a run of moves of this key into one undo step.
        With lockScale on, Size U and Size V move together (BumpMesh)."""
        c = controls.BY_KEY.get(key)
        if c is None:
            raise KeyError(key)
        value = _clamp(c, value)
        if c["scope"] == "ui":
            if self.ui.get(key) != value:
                self.ui[key] = value
                self._emit("set", key, None, value)
            return value
        i = self.active if layer is None else layer
        if self.get(key, i) == value:
            return value
        self._push(coalesce=(key, i) if coalesce else None)
        if key == "includeOnly":
            # BumpMesh: switching Exclude / Include Only clears the layer's mask.
            L = self.layers[i]
            L["includeOnly"] = value
            L["mask"] = {"faces": [], "triangles": []}
            self._emit("mask", key, i, value)
        elif c["scope"] == "layer":
            s = self.layers[i]["settings"]
            s[key] = value
            if key in ("scaleU", "scaleV") and s.get("lockScale"):
                other = "scaleV" if key == "scaleU" else "scaleU"
                s[other] = value
                self._emit("set", other, i, value)
        elif c["scope"] == "frame":
            self.frame["rotation"]["XYZ".index(key[-1])] = value
            self._emit("frame", key, None, value)
            return value
        else:
            self.settings[key] = value
            i = None
        self._emit("set", key, i, value)
        return value

    def update(self, values, layer=None):
        """Several settings at once (texture_set's partial update): one undo step."""
        with self.batch():
            for k, v in values.items():
                self.set(k, v, layer=layer)

    def set_part(self, name):
        if name != self.part:
            self._push()
            self.part = name
            self._emit("part", value=name)

    def set_frame(self, rotation=None, face=None):
        self._push()
        if rotation is not None:
            self.frame["rotation"] = [float(a) for a in rotation]
        if face is not None:
            self.frame["face"] = face
        self._emit("frame", value=copy.deepcopy(self.frame))

    def reset_frame(self):
        self.set_frame(rotation=[0, 0, 0])
        self.frame["face"] = None

    def set_texture(self, texture, layer=None, defaults=None):
        """Put a library texture on a layer; defaults (the entry's, per-layer
        keys only) are applied with it, as BumpMesh does on a preset pick."""
        i = self.active if layer is None else layer
        with self.batch():
            self.layers[i]["texture"] = texture
            for k, v in (defaults or {}).items():
                c = controls.BY_KEY.get(k)
                if c and c["scope"] == "layer":
                    self.layers[i]["settings"][k] = _clamp(c, v)
        self._emit("texture", "texture", i, texture)

    def set_active(self, i):
        if not 0 <= i < len(self.layers):
            raise IndexError(i)
        if i != self.active:
            self.active = i
            self._emit("active", layer=i)

    def add_layer(self):
        """BumpMesh's Add layer: a copy of the active layer's settings and
        texture, in Include Only mode, made active. None at MAX_LAYERS."""
        if len(self.layers) >= MAX_LAYERS:
            return None
        self._push()
        src = self.layer()
        L = self._new_layer(include_only=True, texture=src["texture"], settings=src["settings"])
        self.layers.append(L)
        self.active = len(self.layers) - 1
        self._emit("layers", layer=self.active)
        return self.active

    def remove_layer(self, i):
        if len(self.layers) <= 1:
            return False
        self._push()
        del self.layers[i]
        if self.active >= len(self.layers) or self.active > i:
            self.active = max(0, self.active - 1)
        self._emit("layers", layer=self.active)
        return True

    def move_layer(self, i, delta):
        j = i + delta
        if not (0 <= i < len(self.layers) and 0 <= j < len(self.layers)):
            return False
        self._push()
        self.layers[i], self.layers[j] = self.layers[j], self.layers[i]
        if self.active == i:
            self.active = j
        elif self.active == j:
            self.active = i
        self._emit("layers", layer=self.active)
        return True

    def set_layer(self, i, **props):
        """visible, solo, blendAdd, name of layer i."""
        bad = set(props) - {"visible", "solo", "blendAdd", "name"}
        if bad:
            raise KeyError(sorted(bad))
        L = self.layers[i]
        if all(L[k] == v for k, v in props.items()):
            return
        self._push()
        L.update(props)
        self._emit("layer", layer=i, value=dict(props))

    # -- masks -----------------------------------------------------------------
    def mask(self, layer=None):
        return self.layer(layer)["mask"]

    def mask_add(self, faces=(), triangles=(), layer=None):
        i = self.active if layer is None else layer
        m = self.layers[i]["mask"]
        nf = [f for f in faces if f not in m["faces"]]
        nt = sorted(set(int(t) for t in triangles) - set(m["triangles"]))
        if not nf and not nt:
            return
        self._push()
        m["faces"].extend(nf)
        m["triangles"] = sorted(set(m["triangles"]) | set(nt))
        self._emit("mask", "add", i, {"faces": nf, "triangles": nt})

    def mask_remove(self, faces=(), triangles=(), layer=None):
        i = self.active if layer is None else layer
        m = self.layers[i]["mask"]
        rf = [f for f in faces if f in m["faces"]]
        rt = set(int(t) for t in triangles) & set(m["triangles"])
        if not rf and not rt:
            return
        self._push()
        m["faces"] = [f for f in m["faces"] if f not in rf]
        m["triangles"] = sorted(set(m["triangles"]) - rt)
        self._emit("mask", "remove", i, {"faces": rf, "triangles": sorted(rt)})

    def mask_set(self, mask, layer=None, coalesce=None):
        """Replace the layer's mask (a brush stroke, paint.py): one undo step;
        calls with the same coalesce tag in a row (one drag) make one step."""
        i = self.active if layer is None else layer
        new = {"faces": list(mask.get("faces", [])), "triangles": sorted(int(t) for t in mask.get("triangles", []))}
        if mask.get("soft"):
            new["soft"] = {"corners": [int(c) for c in mask["soft"]["corners"]],
                           "values": [float(v) for v in mask["soft"]["values"]]}
        if mask.get("split"):
            new["split"] = _split_copy(mask["split"])
        if new == self.layers[i]["mask"]:
            return
        self._push(coalesce=coalesce)
        self.layers[i]["mask"] = new
        self._emit("mask", "set", i, None)

    def mask_clear(self, layer=None):
        i = self.active if layer is None else layer
        m = self.layers[i]["mask"]
        if not m["faces"] and not m["triangles"] and not m.get("soft") and not m.get("split"):
            return
        self._push()
        self.layers[i]["mask"] = {"faces": [], "triangles": []}
        self._emit("mask", "clear", i)

    def mask_count(self, layer=None):
        m = self.mask(layer)
        return len(m["faces"]) + len(m["triangles"]) + len(m.get("split") or ())

    # -- whole draft -------------------------------------------------------------
    def reset(self):
        """Reset to defaults (BumpMesh's Reset to defaults): settings, frame and
        layers; the part stays. One undo step."""
        self._push()
        self._coalesce = None
        part = self.part
        self.frame = {"rotation": [0, 0, 0], "face": None}
        self.layers, self.active, self._seq = [], 0, 0
        self.settings = global_defaults()
        self.layers.append(self._new_layer())
        self.part = part
        self._emit("reset")

    def to_dict(self):
        return {"format": FORMAT, "version": VERSION, "part": self.part, "frame": copy.deepcopy(self.frame),
                "active": self.active, "layers": copy.deepcopy(self.layers), "settings": dict(self.settings)}

    def to_json(self, **kw):
        return json.dumps(self.to_dict(), **kw)

    def load(self, data, undoable=True):
        """Replace the draft with a dict from to_dict (missing keys take
        defaults, unknown keys are dropped)."""
        if data.get("format", FORMAT) != FORMAT:
            raise ValueError("not a Texturizer draft")
        if undoable:
            self._push()
        self.part = data.get("part")
        fr = data.get("frame") or {}
        self.frame = {"rotation": [float(a) for a in fr.get("rotation", [0, 0, 0])], "face": fr.get("face")}
        g = global_defaults()
        for k, v in (data.get("settings") or {}).items():
            if k in g:
                g[k] = _clamp(controls.BY_KEY[k], v)
        self.settings = g
        self.layers, self._seq = [], 0
        for src in (data.get("layers") or [{}])[:MAX_LAYERS]:
            L = self._new_layer()
            s = layer_defaults()
            for k, v in (src.get("settings") or {}).items():
                if k in s:
                    s[k] = _clamp(controls.BY_KEY[k], v)
            L.update({k: src[k] for k in ("name", "texture", "visible", "solo", "blendAdd", "includeOnly") if k in src})
            L["id"] = int(src.get("id", L["id"]))
            L["settings"] = s
            m = src.get("mask") or {}
            L["mask"] = {"faces": list(m.get("faces", [])), "triangles": sorted(int(t) for t in m.get("triangles", []))}
            if m.get("soft"):
                L["mask"]["soft"] = copy.deepcopy(m["soft"])
            if m.get("split"):
                L["mask"]["split"] = _split_copy(m["split"])
            self.layers.append(L)
            self._seq = max(self._seq, L["id"])
        self.active = min(max(0, int(data.get("active", 0))), len(self.layers) - 1)
        self._emit("load")

    @classmethod
    def from_dict(cls, data):
        d = cls()
        d.load(data, undoable=False)
        return d

    @classmethod
    def from_json(cls, text):
        return cls.from_dict(json.loads(text))
