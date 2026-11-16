# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Port of the projection gizmos of BumpMesh (stlTexturizer) by Stefan
# Hermann (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer:
# js/viewer.js (the Rotate rings: _buildRotGizmo, _updateGizmoScale,
# _pickGizmoRing, _gizmoPlaneAngle, the drag) and js/main.js (the stamp:
# toggleStampPlace, _placeStampAt, _grabStamp, _moveStampTo,
# _moveNormalStamp, _stampCursorSurface, _scaleStampBy, the wheel while
# armed, _endStampDrag).
"""Dragging the projection in the 3D view (design 4.3), pure: no FreeCAD,
Coin or Qt.

Rotate rings: while Rotate is on, three rings (X red, Y green, Z blue) sit on
the part's bounding-sphere centre at 0.65 of its radius. Dragging one turns
the texturing frame about that axis by the angle the cursor sweeps on the
ring's plane, live, one undo step a drag. BumpMesh turns the model under a
fixed projection; the part does not move in FreeCAD, so here the projection
turns with the cursor instead and Rotate X / Y / Z show the frame's angles.

Stamp: with a stamp layer, a press on the stamp's patch (no tool on) grabs
it and the drag slides it over the surface, keeping the grabbed point under
the cursor (Surface normal follows the surface's own frame and stops at an
edge until the cursor is clearly past it). Position on model arms a click
that centres the stamp there, the face choosing the projection's axis and
side, and drags on. After a press on the stamp the wheel resizes it (Size U
and V by the same factor, exp(-dy * 0.0015) a wheel step) until the next
press elsewhere; then the wheel zooms again. A drag is one undo step, a run
of wheel steps another.

Everything is in the document's frame on input (the part's tessellation,
the camera ray) and turned into the texturing frame here, where BumpMesh's
stamp fields live.

GizmoTool maps viewer events: button / move / wheel return True when they
took the event (FreeCAD does not navigate), as paint.PaintTool.
"""
import math

import numpy as np

from . import projection_extras as px
from .engine import frame_matrix
from .engine.mapping import is_stamp_active, stamp_axes, stamp_frame, stamp_uv

LEFT = 1
COLOURS = {"x": (1.0, 0.2, 0.2), "y": (0.2, 0.867, 0.333), "z": (0.267, 0.533, 1.0)}        # 0xff3333 ...
HOVER_COLOURS = {"x": (1.0, 0.533, 0.533), "y": (0.533, 1.0, 0.6), "z": (0.533, 0.733, 1.0)}
AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
RING_SCALE = 0.65       # _updateGizmoScale: 0.65 of the bounding sphere
RING_HIT = 0.08         # the invisible fat hit torus (tube 0.08 of the radius)
RING_SEGMENTS = 64
WHEEL_RATE = 0.0015     # _scaleStampBy(Math.exp(-dy * 0.0015))
WHEEL_PX_PER_STEP = 100  # a browser's deltaY for one wheel notch (Qt: 120 eighths of a degree)


# -- the rings --------------------------------------------------------------------------------
def bounding_sphere(positions):
    """three.js computeBoundingSphere: the box centre and the farthest point from it."""
    p = np.asarray(positions, np.float64).reshape(-1, 3)
    c = (p.min(axis=0) + p.max(axis=0)) * 0.5
    return c, float(np.sqrt(((p - c) ** 2).sum(axis=1).max())) if len(p) else 0.0


def ring_points(centre, radius, axis, n=RING_SEGMENTS):
    """The ring of axis as n + 1 points (closed). X lies in YZ, Y in ZX, Z in XY."""
    t = np.linspace(0, 2 * math.pi, n + 1)
    c, s = np.cos(t) * radius, np.sin(t) * radius
    z = np.zeros_like(t)
    loc = {"x": (z, c, s), "y": (s, z, c), "z": (c, s, z)}[axis]
    return np.stack(loc, axis=1) + np.asarray(centre, float)


def pick_ring(origin, direction, centre, radius):
    """_pickGizmoRing: the axis of the ring under the cursor ray (within the
    fat hit tube), the nearest along the ray, or None."""
    o = np.asarray(origin, float)
    d = np.asarray(direction, float)
    d = d / (np.linalg.norm(d) or 1)
    best, best_t = None, math.inf
    for axis in ("x", "y", "z"):
        P = ring_points(centre, radius, axis)
        v = P - o
        t = v @ d
        dist = np.linalg.norm(v - np.outer(t, d), axis=1)
        k = int(np.argmin(dist))
        if dist[k] <= RING_HIT * radius and 0 < t[k] < best_t:
            best, best_t = axis, float(t[k])
    return best


def plane_angle(origin, direction, centre, axis):
    """_gizmoPlaneAngle: the cursor ray on the ring's plane, as an angle in
    the ring's own 2D system; None when the ray misses the plane."""
    n = np.asarray(AXES[axis])
    o, d = np.asarray(origin, float), np.asarray(direction, float)
    den = float(n @ d)
    if abs(den) < 1e-12:
        return None
    t = float(n @ (np.asarray(centre, float) - o)) / den
    if t < 0:
        return None
    lx, ly, lz = o + t * d - np.asarray(centre, float)
    if axis == "x":
        return math.atan2(lz, ly)
    if axis == "y":
        return math.atan2(lx, lz)
    return math.atan2(ly, lx)


def wrap(delta):
    if delta > math.pi:
        delta -= 2 * math.pi
    if delta < -math.pi:
        delta += 2 * math.pi
    return delta


def axis_matrix(axis, degrees):
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def matrix_to_euler(R):
    """Rotate X / Y / Z (degrees) of R = Rx · Ry · Rz (engine.frame_matrix, three.js 'XYZ')."""
    R = np.asarray(R, float)
    y = math.asin(max(-1.0, min(1.0, R[0, 2])))
    if abs(R[0, 2]) < 0.9999999:
        x = math.atan2(-R[1, 2], R[2, 2])
        z = math.atan2(-R[0, 1], R[0, 0])
    else:
        x = math.atan2(R[2, 1], R[1, 1])
        z = 0.0
    return [round(math.degrees(v), 6) + 0.0 for v in (x, y, z)]


def turn_frame(rotation, axis, degrees):
    """The frame's Rotate X / Y / Z after the projection turned by degrees
    about the document's axis (the part fixed, as FreeCAD shows it)."""
    R = frame_matrix(rotation) @ axis_matrix(axis, -degrees)
    return matrix_to_euler(R)


# -- the stamp --------------------------------------------------------------------------------
def frame_bounds(pos_f):
    p = np.asarray(pos_f, np.float64).reshape(-1, 3)
    lo, hi = p.min(axis=0), p.max(axis=0)
    return {"min": tuple(lo), "max": tuple(hi), "size": tuple(hi - lo), "center": tuple((lo + hi) * 0.5)}


def stamp_frame_of(settings, pos_f):
    """BumpMesh's stampFrame for a layer's settings on the part in the texturing frame (square texture)."""
    if not is_stamp_active(settings):
        return None
    return stamp_frame({**settings, "textureAspectU": 1.0, "textureAspectV": 1.0}, frame_bounds(pos_f))


def on_patch(frame, point, normal):
    """_stampPatchHit: the point lies on the stamp's patch (stampUV cover > 0)."""
    if frame is None:
        return False
    return bool(stamp_uv([point], [normal], frame)[2][0] > 0)


def grab(frame, hit, positions=None, settings=None):
    """_grabStamp: where on the patch it was grabbed (and, Surface normal, the surface there)."""
    p = np.asarray(hit["point"], float)
    if frame["mode"] == px.MODE_CYLINDRICAL:
        return {"cyl": True, "ccx": frame["ccx"], "ccy": frame["ccy"],
                "rc": math.hypot(frame["cx"] - frame["ccx"], frame["cy"] - frame["ccy"]),
                "dth": math.atan2(frame["cy"] - frame["ccy"], frame["cx"] - frame["ccx"])
                - math.atan2(p[1] - frame["ccy"], p[0] - frame["ccx"]),
                "dz": frame["cz"] - p[2]}
    if frame["mode"] == px.MODE_SURFACE_NORMAL:
        s = surface_under(hit, positions, settings)
        ax = stamp_axes(s[3], s[4], s[5])
        dx, dy, dz = frame["cx"] - s[0], frame["cy"] - s[1], frame["cz"] - s[2]
        return {"normal": True, "last": s,
                "du": dx * ax["ux"] + dy * ax["uy"] + dz * ax["uz"],
                "dv": dx * ax["vx"] + dy * ax["vy"] + dz * ax["vz"],
                "dn": dx * s[3] + dy * s[4] + dz * s[5]}
    return {"dx": frame["cx"] - p[0], "dy": frame["cy"] - p[1], "dz": frame["cz"] - p[2]}


def move_to(hit, g):
    """_moveStampTo: the centre under the cursor, at the grab offset (around the axis for Cylindrical)."""
    x, y, z = (float(c) for c in hit["point"])
    if g and g.get("cyl"):
        th = math.atan2(y - g["ccy"], x - g["ccx"]) + g["dth"]
        x, y, z = g["ccx"] + g["rc"] * math.cos(th), g["ccy"] + g["rc"] * math.sin(th), z + g["dz"]
    elif g:
        x, y, z = x + g["dx"], y + g["dy"], z + g["dz"]
    return {"stampCenterX": x, "stampCenterY": y, "stampCenterZ": z}


def surface_under(hit, positions, settings):
    """_stampSurfaceUnder: a short snap to the surface and its fitted normal."""
    R = px.stamp_radius(settings)
    q, n = hit["point"], hit["normal"]
    return px.stamp_surface_at(positions, q, n, R, 0.1 * R + 0.5) or (*(float(c) for c in q), *(float(c) for c in n))


def cursor_surface(hit, ray, last, positions, settings):
    """_stampCursorSurface: the surface under the cursor; while dragging past an
    edge (the hit turns away, jumps in depth or misses) the ray on the last
    surface's tangent plane, dropped back onto it, so the stamp stops at the
    edge until the cursor is a quarter of its size past it."""
    s = surface_under(hit, positions, settings) if hit else None
    if last is not None:
        R = px.stamp_radius(settings)
        L = last
        left = s is None or s[3] * L[3] + s[4] * L[4] + s[5] * L[5] < 0.5 \
            or abs((s[0] - L[0]) * L[3] + (s[1] - L[1]) * L[4] + (s[2] - L[2]) * L[5]) > 0.2 * R
        o, d = ray
        den = d[0] * L[3] + d[1] * L[4] + d[2] * L[5]
        if left and den < -1e-3:
            t = ((L[0] - o[0]) * L[3] + (L[1] - o[1]) * L[4] + (L[2] - o[2]) * L[5]) / den
            q = [o[k] + t * d[k] for k in range(3)]
            on = px.stamp_surface_at(positions, q, L[3:6], R, R)
            if on and math.dist(on[:3], q) < 0.5 * R:
                s = on
    return s


def move_normal(hit, ray, g, positions, settings):
    """_moveNormalStamp: Surface normal follows the surface (at the grab offset in its frame)."""
    last = g.get("last") if g else None
    s = cursor_surface(hit, ray, last, positions, settings)
    if s is None:
        return None
    if g is not None:
        g["last"] = s
    if g and g.get("normal"):
        ax = stamp_axes(s[3], s[4], s[5])
        q = [s[k] + g["du"] * ax["u" + "xyz"[k]] + g["dv"] * ax["v" + "xyz"[k]] + g["dn"] * s[3 + k] for k in range(3)]
        R = max(px.stamp_radius(settings), math.hypot(g["du"], g["dv"]))
        s = px.stamp_surface_at(positions, q, s[3:6], px.stamp_radius(settings), R) or (*q, *s[3:6])
    return dict(zip(("stampCenterX", "stampCenterY", "stampCenterZ", "stampNormalX", "stampNormalY", "stampNormalZ"),
                    (float(v) for v in s)))


def scale_by(settings, factor, lo=0.0, hi=math.inf):
    """_scaleStampBy: Size U and V times factor (V follows U with Lock), clamped."""
    u = max(lo, min(hi, float(settings["scaleU"]) * factor))
    v = u if settings.get("lockScale") else max(lo, min(hi, float(settings["scaleV"]) * factor))
    return {"scaleU": u, "scaleV": v}


def reorient(settings, positions):
    """_reorientNormalStamp: Surface normal re-fits its normal at the centre after a resize."""
    if settings.get("mappingMode") != px.MODE_SURFACE_NORMAL or settings.get("stampCenterX") is None \
            or settings.get("stampNormalX") is None:
        return {}
    q = [settings[k] for k in ("stampCenterX", "stampCenterY", "stampCenterZ")]
    n = [settings[k] for k in ("stampNormalX", "stampNormalY", "stampNormalZ")]
    s = px.stamp_surface_at(positions, q, n, px.stamp_radius(settings))
    return {} if s is None else dict(zip(("stampNormalX", "stampNormalY", "stampNormalZ"), s[3:6]))


# -- events -----------------------------------------------------------------------------------
class GizmoTool:
    """Viewer events to the rings and the stamp on the draft's active layer.

    positions() -> the part's triangles (document frame) or None; ray(x, y)
    -> (origin, direction) in the document frame; on_rings(rings or None)
    draws them ({centre, radius, hover, drag}); on_armed(bool) lights the
    stamp hint while the wheel resizes."""

    def __init__(self, draft, positions, ray, on_rings=None, on_armed=None, scale_range=(0.0, math.inf)):
        self.d = draft
        self.positions = positions
        self.ray = ray
        self.on_rings = on_rings or (lambda *a: None)
        self.on_armed = on_armed or (lambda *a: None)
        self.scale_range = scale_range
        self.placing = False
        self.armed = False
        self.drag = None   # {"kind": "ring" | "stamp", ...}
        self.hover = None
        self._n = 0
        self._wheel = None

    # -- state --------------------------------------------------------------------------
    def _ui(self, k):
        try:
            return self.d.get(k)
        except KeyError:
            return None

    def rings_on(self):
        return bool(self._ui("rotate")) and self.positions() is not None

    def rings(self):
        P = self.positions()
        if P is None:
            return None
        c, r = bounding_sphere(P)
        return {"centre": c, "radius": r * RING_SCALE}

    def draw(self):
        g = self.rings() if self.rings_on() else None
        if g is not None:
            g.update(hover=self.hover, drag=self.drag["axis"] if self.drag and self.drag["kind"] == "ring" else None)
        self.on_rings(g)

    def _R(self):
        return frame_matrix(self.d.frame.get("rotation"))

    def _frame_pos(self, R):
        return (np.asarray(self.positions(), np.float64).reshape(-1, 3) @ R.T).reshape(-1)

    def _hit(self, x, y, R, pos_f):
        from .paint import ray_hit

        o, d = self.ray(x, y)
        o, d = R @ np.asarray(o, float), R @ np.asarray(d, float)
        h = ray_hit(pos_f, o, d)
        return h, (o.tolist(), d.tolist())

    def stamp_on(self):
        return is_stamp_active(self.d.layer()["settings"])

    def arm_place(self, on=True):
        """Position on model: the next click on the part places the stamp."""
        self.placing = bool(on) and self.positions() is not None
        if self.placing:
            if not self.stamp_on():
                self.d.update(px.stamp_mode(self.d.layer()["settings"], True))
        return self.placing

    def _arm(self, on):
        if on != self.armed:
            self.armed = on
            self.on_armed(on)
        if not on and self._wheel is not None:
            self._wheel = None
            self.d.end_coalesce()   # the wheel's resize: one step

    def cancel(self):
        """Escape, another tool, a new part: placing ends, the wheel zooms again."""
        self.placing = False
        self._arm(False)
        if self.drag is not None:
            self.drag = None
            self.d.end_coalesce()
        self.draw()

    # -- events -------------------------------------------------------------------------
    def button(self, button, down, x, y):
        if button != LEFT:
            if down:
                self._arm(False)   # orbit, pan: the wheel zooms again
            return False
        if not down:
            if self.drag is None:
                return False
            kind = self.drag["kind"]
            self.drag = None
            self.d.end_coalesce()
            if kind == "stamp":
                self.placing = False
                self._arm(True)
            self.draw()
            return True
        if self.positions() is None:
            return False
        if self.rings_on():
            g = self.rings()
            o, d = self.ray(x, y)
            axis = pick_ring(o, d, g["centre"], g["radius"])
            if axis is not None:
                self._n += 1
                self.drag = {"kind": "ring", "axis": axis, "tag": ("ring", self._n),
                             "last": plane_angle(o, d, g["centre"], axis), "g": g}
                self.draw()
                return True
        if self._ui("exclusionTool") or self._ui("rotate"):
            return False
        if not (self.placing or self.stamp_on()):
            self._arm(False)
            return False
        R = self._R()
        pos_f = self._frame_pos(R)
        hit, ray = self._hit(x, y, R, pos_f)
        S = self.d.layer()["settings"]
        if self.placing:
            if hit is None:
                self._arm(False)
                return False
            up = px.stamp_place(hit["point"], hit["normal"], S, frame_bounds(pos_f), pos_f)
            gr = None
            if S.get("mappingMode") == px.MODE_SURFACE_NORMAL:
                gr = {"last": tuple(up[k] for k in ("stampCenterX", "stampCenterY", "stampCenterZ",
                                                    "stampNormalX", "stampNormalY", "stampNormalZ"))}
        else:
            fr = stamp_frame_of(S, pos_f)
            if hit is None or not on_patch(fr, hit["point"], hit["normal"]):
                self._arm(False)
                return False
            gr = grab(fr, hit, pos_f, S)
            up = {}
        self._n += 1
        self.drag = {"kind": "stamp", "grab": gr, "tag": ("stamp", self._n)}
        if up:
            self.d.update(up, coalesce=self.drag["tag"])
        self._arm(True)
        return True

    def move(self, x, y):
        if self.drag is None:
            if self.rings_on():
                g = self.rings()
                o, d = self.ray(x, y)
                h = pick_ring(o, d, g["centre"], g["radius"])
                if h != self.hover:
                    self.hover = h
                    self.draw()
            return False
        if self.drag["kind"] == "ring":
            dr = self.drag
            o, d = self.ray(x, y)
            a = plane_angle(o, d, dr["g"]["centre"], dr["axis"])
            if a is not None and dr["last"] is not None:
                deg = math.degrees(wrap(a - dr["last"]))
                if abs(deg) > 0.01:
                    self.d.set_frame(rotation=turn_frame(self.d.frame["rotation"], dr["axis"], deg), coalesce=dr["tag"])
            if a is not None:
                dr["last"] = a
            return True
        R = self._R()
        pos_f = self._frame_pos(R)
        hit, ray = self._hit(x, y, R, pos_f)
        S = self.d.layer()["settings"]
        if S.get("mappingMode") == px.MODE_SURFACE_NORMAL:
            if self.drag["grab"] is None:
                self.drag["grab"] = {}
            up = move_normal(hit, ray, self.drag["grab"], pos_f, S)
        else:
            up = move_to(hit, self.drag["grab"]) if hit is not None else None
        if up:
            self.d.update(up, coalesce=self.drag["tag"])
        return True

    def wheel(self, delta_y):
        """A wheel step (delta_y as a browser's deltaY: + towards the person):
        resizes the stamp while armed; False (zoom) otherwise."""
        if not self.armed or not self.stamp_on():
            return False
        S = self.d.layer()["settings"]
        up = scale_by(S, math.exp(-delta_y * WHEEL_RATE), *self.scale_range)
        if self._wheel is None:
            self._n += 1
            self._wheel = ("wheel", self._n)
        self.d.update(up, coalesce=self._wheel)
        pos = self.positions()
        if pos is not None:
            ro = reorient(self.d.layer()["settings"], self._frame_pos(self._R()))
            if ro:
                self.d.update(ro, coalesce=self._wheel)
        return True
