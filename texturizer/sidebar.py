# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# The sidebar's sections, order, headings, labels, ranges, defaults and
# tooltips are BumpMesh's (stlTexturizer) by Stefan Hermann (CNC Kitchen),
# AGPL-3.0, github.com/CNCKitchen/stlTexturizer (index.html, js/i18n/en.js,
# js/main.js), so a BumpMesh user knows it at once.
"""The "Texturizer" settings sidebar: a right-hand QDockWidget built from
controls.py, BumpMesh's sections in BumpMesh's order:

    (top row) Undo, Redo, Reset to Defaults, Save Project, Load Project
    1 (untitled) Load Model…, Place on Face, Rotate
    2 Displacement Map   3 Projection   4 Texture Depth   5 Transform
    6 Masking            7 Advanced / Beta Features (collapsed)   8 Export
    footer: the BumpMesh credit, progress + Cancel

Each slider has its typed box beside it. The per-layer sections edit the
active layer of the layer strip. Widgets and the draft.Draft bind both ways
(the draft's signals move the widgets, so an agent's texture_set shows live).
Buttons that need the engine, the document or the selection call the injected
runner(tool, args) -> (ok, result): Agent Link's when hosted, Texturizer's
own alone; the sidebar doesn't care which (ACTIONS lists what it asks).
"""
import math

from . import controls, gallery, layout, title_case
from .qt import QtCore, QtGui, QtWidgets

S = controls.string
TITLE = "Texturizer"
NAME = "TexturizerSidebar"
CREDIT_HTML = (f'{gallery.BUMPMESH_CREDIT} · <a href="{gallery.BUMPMESH_URL}">github.com/CNCKitchen/stlTexturizer</a>')

# Button -> (tool, args) through the runner. Every call also carries
# "layer": the active layer's index. Buttons not here are the draft's own
# (Undo, Redo, Reset, Add layer, Clear All, Rotate Apply / Reset, Texture Gallery).
ACTIONS = {
    "loadModel": ("texture_draft", {"load": "selection"}),
    "placeOnFace": ("texture_set", {"frame": {"face": "selected"}}),
    "cylinderAutofit": ("texture_set", {"cylinder": "autofit"}),
    "cylinderReset": ("texture_set", {"cylinder": "reset"}),
    "cylinderFromFace": ("texture_set", {"cylinder": "from_face"}),
    "stampPlace": ("texture_set", {"stamp": "place"}),
    "uploadCustomMap": ("texture_set", {"texture": {"upload": True}}),
    "maskAddSelected": ("texture_mask", {"add": "selected"}),
    "maskAddBy": ("texture_mask", {"add_by": None}),
    "smartRes": ("texture_preview", {"suggest": True}),
    "bakeTextures": ("texture_apply", {"bake": True}),
    "addToDocument": ("texture_apply", {}),
    "exportStl": ("texture_export", {"format": "stl"}),
    "export3mf": ("texture_export", {"format": "3mf"}),
    "saveProject": ("texture_export", {"format": "bumpmesh"}),
    "loadProject": ("texture_draft", {"project": "ask"}),
    "cancel": ("texture_preview", {"cancel": True}),
}
ADD_BY = [("direction", "Facing Direction"), ("box", "In Box"), ("surface", "Same Surface")]
FLOW_SPACING = 6
LONG_CHECK = 28  # a check box's label longer than this wraps in a label of its own


# -- plain helpers (no Qt) ------------------------------------------------------
def format_m(n):
    """BumpMesh's formatM: 750000 -> '750 k', 1500000 -> '1.5 M'."""
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} M"
    if n >= 1_000:
        return f"{n / 1_000:.0f} k"
    return str(n)


def _prec3(x):
    return float(f"{x:.3g}")


def positions(c):
    """(first, last) integer slider position for control c."""
    s = c["slider"]
    if c["type"] == "log slider":
        return int(s["min"]), int(s["max"])
    step = s["step"]
    n = 1000 if step in (None, "any") else int(round((s["max"] - s["min"]) / step))
    return 0, n


def pos_to_value(c, pos, anchor=50.0):
    s = c["slider"]
    if c["type"] == "log slider":
        lg = s["log"]
        lo, hi = math.log(lg["rel_min"]), math.log(lg["rel_max"])
        return _prec3(anchor * math.exp(lo + (pos / float(s["max"])) * (hi - lo)))  # BumpMesh posToScale
    a, n = positions(c)
    v = s["min"] + (s["max"] - s["min"]) * pos / float(n)
    step = s["step"]
    if step not in (None, "any"):
        v = round(round(v / step) * step, 6)
    return v


def value_to_pos(c, v, anchor=50.0):
    s = c["slider"]
    if v is None:
        v = c["default"]
    if c["type"] == "log slider":
        lg = s["log"]
        rel = max(lg["rel_min"], min(lg["rel_max"], float(v) / anchor))
        lo, hi = math.log(lg["rel_min"]), math.log(lg["rel_max"])
        return int(round((math.log(rel) - lo) / (hi - lo) * s["max"]))  # BumpMesh scaleToPos
    a, n = positions(c)
    v = max(s["min"], min(s["max"], float(v)))
    return int(round((v - s["min"]) / float(s["max"] - s["min"]) * n))


def decimals(step):
    if step in (None, "any"):
        return 2
    d = 0
    while d < 6 and abs(step * 10 ** d - round(step * 10 ** d)) > 1e-9:
        d += 1
    return d


def shown(c, get):
    """Whether control c's row is shown, by its show rule (BumpMesh hides the row otherwise)."""
    rule = c.get("show")
    if not rule:
        return True
    if "never" in rule:
        return False
    for k, vals in rule.items():
        try:
            if get(k) not in vals:
                return False
        except KeyError:
            return False
    return True


def mask_count_text(n, include_only):
    if include_only:
        return S("excl.faceSelected" if n == 1 else "excl.facesSelected", n=n)
    return S("excl.faceExcluded" if n == 1 else "excl.facesExcluded", n=n)


def min_dock_width(content, scrollbar, frame=0):
    """The sidebar's minimum width for content of minimum width content."""
    return int(content) + int(scrollbar) + 2 * int(frame)


def content_width(dock, scrollbar, frame=0):
    """The width the content gets in a dock this wide: the viewport, that
    is the dock less the vertical scrollbar and the frame."""
    return max(0, int(dock) - int(scrollbar) - 2 * int(frame))


def flow_rows(widths, avail, spacing=FLOW_SPACING):
    """Item indices in rows no wider than avail, in order: a row of buttons
    wraps instead of running under the scrollbar. An item wider than avail
    gets a row of its own (and is shrunk to avail by the layout)."""
    rows, cur, x = [], [], 0
    for i, w in enumerate(widths):
        w = int(w)
        need = w if not cur else x + spacing + w
        if cur and need > avail:
            rows.append(cur)
            cur, x = [i], w
        else:
            cur.append(i)
            x = need
    if cur:
        rows.append(cur)
    return rows


if QtWidgets is not None:

    class FlowLayout(QtWidgets.QLayout):
        """Widgets left to right at their own width, wrapping to a new line
        when the next one would not fit (flow_rows): its minimum width is its
        widest item's, so a row of buttons never pushes the sidebar wider
        than its dock."""

        def __init__(self, parent=None, spacing=FLOW_SPACING):
            super().__init__(parent)
            self._items = []
            self._gap = spacing
            self.setContentsMargins(0, 0, 0, 0)

        def addItem(self, item):  # noqa: N802 (Qt)
            self._items.append(item)

        def count(self):
            return len(self._items)

        def itemAt(self, i):  # noqa: N802
            return self._items[i] if 0 <= i < len(self._items) else None

        def takeAt(self, i):  # noqa: N802
            return self._items.pop(i) if 0 <= i < len(self._items) else None

        def hasHeightForWidth(self):  # noqa: N802
            return True

        def heightForWidth(self, width):  # noqa: N802
            return self.arrange(0, 0, width, apply=False)

        def setGeometry(self, rect):  # noqa: N802
            super().setGeometry(rect)
            self.arrange(rect.x(), rect.y(), rect.width())

        def _shown(self):
            return [it for it in self._items if not it.isEmpty()]

        def minimumSize(self):  # noqa: N802
            w = h = 0
            for it in self._shown():
                m = it.minimumSize()
                w, h = max(w, m.width()), max(h, m.height())
            return QtCore.QSize(w, h)

        def sizeHint(self):  # noqa: N802
            items = self._shown()
            w = sum(it.sizeHint().width() for it in items) + self._gap * max(0, len(items) - 1)
            h = max([it.sizeHint().height() for it in items] or [0])
            return QtCore.QSize(w, h)

        def arrange(self, x0, y0, width, apply=True):
            """Lay the items out in width from (x0, y0); the height used."""
            items = self._shown()
            sizes = [it.sizeHint() for it in items]
            widths = [max(it.minimumSize().width(), min(sz.width(), width)) for it, sz in zip(items, sizes)]
            y = 0
            for row in flow_rows(widths, width, self._gap):
                hh = max(sizes[i].height() for i in row)
                x = 0
                for i in row:
                    if apply:
                        items[i].setGeometry(QtCore.QRect(x0 + x, y0 + y, widths[i], sizes[i].height()))
                    x += widths[i] + self._gap
                y += hh + self._gap
            return max(0, y - self._gap)

    class _Bind:
        """One control's widgets: row (shown or hidden), set(value) from the
        draft, label() the text the user reads."""

        def __init__(self, c, row):
            self.c, self.key, self.row = c, c["key"], row
            self.widgets = []

        def set(self, v):  # pragma: no cover — overridden
            pass

        def label(self):
            return self.c.get("label")

    class Section(QtWidgets.QWidget):
        """A sidebar section: BumpMesh's heading as a collapse toggle (none
        for the untitled ones) over its rows."""

        def __init__(self, s, parent=None):
            super().__init__(parent)
            self.sid, self.title = s["id"], s["title"]
            self.setObjectName("section_" + s["id"])
            v = QtWidgets.QVBoxLayout(self)
            v.setContentsMargins(0, 4, 0, 4)
            self.head = None
            if s["title"]:
                self.head = QtWidgets.QToolButton()
                self.head.setText(title_case(s["title"]))  # a group title: Title Case
                self.head.setCheckable(True)
                self.head.setChecked(not s["collapsed"])
                self.head.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
                self.head.setAutoRaise(True)
                if s.get("tooltip"):
                    self.head.setToolTip(s["tooltip"])
                self.head.toggled.connect(self.expand)
                v.addWidget(self.head)
            self.body = QtWidgets.QWidget()
            self.rows = QtWidgets.QVBoxLayout(self.body)
            self.rows.setContentsMargins(8 if s["title"] else 0, 0, 0, 0)
            v.addWidget(self.body)
            self.expand(not s["collapsed"])

        def expand(self, on):
            self.body.setVisible(bool(on))
            if self.head is not None:
                self.head.setArrowType(QtCore.Qt.DownArrow if on else QtCore.Qt.RightArrow)
                if self.head.isChecked() != bool(on):
                    self.head.setChecked(bool(on))

        def collapsed(self):
            return not self.body.isVisibleTo(self) if self.head is not None else False

        def add(self, w):
            self.rows.addWidget(w)

    class LayerStrip(QtWidgets.QWidget):
        """BumpMesh's layer strip: rows with eye, thumbnail, name, Cover/Add,
        delete (plus solo and up/down), and Add layer. Clicking a row makes it
        the layer the per-layer sections edit."""

        def __init__(self, sidebar):
            super().__init__()
            self.sb = sidebar
            self.setObjectName("TexturizerLayers")
            v = QtWidgets.QVBoxLayout(self)
            v.setContentsMargins(0, 0, 0, 0)
            self.list = QtWidgets.QVBoxLayout()
            v.addLayout(self.list)
            c = controls.BY_KEY["addLayer"]
            self.add = QtWidgets.QPushButton(title_case(c["label"]))
            self.add.setObjectName("btn_addLayer")
            b = sidebar._register(_Bind(c, self.add))
            b.widgets = [self.add]
            b.label = lambda: self.add.text()
            self.add.clicked.connect(lambda *_: self.sb.draft.add_layer())
            v.addWidget(self.add)
            self.rows = []
            self.refresh()

        def refresh(self):
            d = self.sb.draft
            while self.list.count():
                w = self.list.takeAt(0).widget()
                if w is not None:  # later: a row's own click may be what rebuilds the strip
                    w.hide()
                    w.deleteLater()
            self.rows = []
            by_id = {e["id"]: e for e in self.sb.textures()}
            for i, L in enumerate(d.layers):
                row = QtWidgets.QWidget()
                row.setObjectName(f"layer_{i}")
                h = FlowLayout(row)  # wraps in a narrow dock: Remove never under the scrollbar
                eye = QtWidgets.QToolButton()
                eye.setCheckable(True)
                eye.setChecked(L["visible"])
                eye.setText("👁" if L["visible"] else "–")
                eye.setToolTip(S("layers.hide") if L["visible"] else S("layers.show"))
                eye.clicked.connect(lambda on, i=i: d.set_layer(i, visible=bool(on)))
                h.addWidget(eye)
                thumb = QtWidgets.QLabel()
                e = by_id.get(L["texture"])
                if e is not None:
                    thumb.setPixmap(gallery.thumb_icon(e, 24).pixmap(24, 24))
                    thumb.setToolTip(gallery.credit_line(e))
                h.addWidget(thumb)
                name = QtWidgets.QPushButton(L["name"] + (" · " + e["name"] if e else ""))
                name.setFlat(True)
                name.setCheckable(True)
                name.setChecked(i == d.active)
                f = name.font()
                f.setBold(i == d.active)
                name.setFont(f)
                name.setToolTip(S("layers.rowTitle", n=i + 1))
                # "Layer n" always fits; the row wraps rather than clip the rest.
                name.setMinimumWidth(name.fontMetrics().horizontalAdvance(S("layers.name", n=len(d.layers))) + 16)
                name.clicked.connect(lambda *_, i=i: d.set_active(i))
                h.addWidget(name)
                solo = QtWidgets.QToolButton()
                solo.setText("S")
                solo.setCheckable(True)
                solo.setChecked(L.get("solo", False))
                solo.setToolTip("Solo: show only this layer in the preview")
                solo.clicked.connect(lambda on, i=i: d.set_layer(i, solo=bool(on)))
                h.addWidget(solo)
                blend = QtWidgets.QToolButton()
                blend.setText(S("layers.blendAdd") if L["blendAdd"] else S("layers.blendCover"))
                blend.setToolTip(S("layers.blendAddTitle") if L["blendAdd"] else S("layers.blendCoverTitle"))
                blend.clicked.connect(lambda *_, i=i, b=L["blendAdd"]: d.set_layer(i, blendAdd=not b))
                h.addWidget(blend)
                up, down = QtWidgets.QToolButton(), QtWidgets.QToolButton()
                up.setText("▲")
                down.setText("▼")
                up.setEnabled(i > 0)
                down.setEnabled(i < len(d.layers) - 1)
                up.clicked.connect(lambda *_, i=i: d.move_layer(i, -1))
                down.clicked.connect(lambda *_, i=i: d.move_layer(i, 1))
                h.addWidget(up)
                h.addWidget(down)
                rm = QtWidgets.QToolButton()
                rm.setText("✕")
                rm.setToolTip(S("layers.remove"))
                rm.setEnabled(len(d.layers) > 1)
                rm.clicked.connect(lambda *_, i=i: d.remove_layer(i))
                h.addWidget(rm)
                self.list.addWidget(row)
                self.rows.append({"row": row, "eye": eye, "name": name, "solo": solo, "blend": blend, "remove": rm})
            full = len(d.layers) >= controls.MAX_LAYERS
            self.add.setEnabled(not full)
            self.add.setToolTip(S("layers.max", n=controls.MAX_LAYERS) if full else S("layers.addTitle"))
            if getattr(self.sb, "scroll", None) is not None:
                self.sb.fit_width()

    class Sidebar(QtWidgets.QDockWidget):
        def __init__(self, draft, runner=None, list_textures=None, favourites=None, open_gallery=None,
                     selection=None, confirm=None, parent=None):
            super().__init__(TITLE, parent)
            self.setObjectName(NAME)
            self.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
            self.draft = draft
            self.runner = runner
            self.list_textures = list_textures or (lambda: [])
            self.favourites = favourites or gallery.Favourites(entries=self.textures())
            self.open_gallery = open_gallery
            self.selection = selection or _freecad_faces
            self.confirm = confirm or self._ask_reset
            self.anchor = 50.0  # BumpMesh's scale anchor: the part's largest box edge (50 mm with none)
            self.binds = {}
            self.sections = []
            self._updating = False
            self._gallery = None
            self.last = None

            body = QtWidgets.QWidget()
            self.body = body
            v = QtWidgets.QVBoxLayout(body)
            for s in controls.SECTIONS:
                sec = Section(s)
                self.sections.append(sec)
                v.addWidget(sec)
                self._fill(sec)
            v.addStretch(1)
            v.addWidget(self._footer())
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            # Content follows the dock's width (tiles reflow, labels wrap):
            # never a horizontal scrollbar, and the dock is never narrower
            # than the content's minimum, so nothing is clipped.
            scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            scroll.setWidget(body)
            self.scroll = scroll
            # The settings, and the Texture Gallery sliding out over them
            # inside this panel (show_gallery; its Done slides it back): no
            # dock of its own beside the sidebar, which widened the window.
            self.pages = QtWidgets.QStackedWidget()
            self.pages.addWidget(scroll)
            self.setWidget(self.pages)
            self._unsub = draft.subscribe(self._on_draft)
            self.refresh()
            self.fit_width()

        def fit_width(self):
            """The dock's minimum width: the content's minimum plus the
            vertical scrollbar (counted whether shown yet or not, as it
            appears once the sections outgrow the dock) and the frame.
            Button rows wrap (FlowLayout), so the content's minimum is its
            widest single control. Run again once shown: the theme's
            stylesheet makes buttons wider than they were when built."""
            # Called from _fill too, which runs in __init__ before the scroll
            # area is made: then there is nothing to measure yet (it crashed
            # every open on the Mac: self.scroll was QWidget.scroll).
            scroll = self.__dict__.get("scroll")
            if scroll is None or not hasattr(scroll, "verticalScrollBar"):
                return 0
            sb = scroll.verticalScrollBar()
            bar = sb.sizeHint().width()
            try:
                bar = max(bar, sb.style().pixelMetric(QtWidgets.QStyle.PM_ScrollBarExtent, None, sb))
            except (AttributeError, TypeError):
                pass
            w = min_dock_width(self.body.minimumSizeHint().width(), bar, scroll.frameWidth())
            # Never more than layout.CONTENT_MIN: the panel stands beside the
            # tree or slides out (300 px or less); rows wrap below that.
            w = min(w, layout.CONTENT_MIN)
            scroll.setMinimumWidth(w)
            return w

        def showEvent(self, ev):  # noqa: N802 (Qt)
            super().showEvent(ev)
            self.fit_width()

        # -- library -------------------------------------------------------------
        def textures(self):
            try:
                return list(self.list_textures())
            except Exception:
                return []

        # -- building ------------------------------------------------------------
        def _fill(self, sec):
            rows = controls.section(sec.sid)
            i = 0
            while i < len(rows):
                c = rows[i]
                k, t = c["key"], c["type"]
                if k == "addLayer":
                    self.layers = LayerStrip(self)
                    sec.add(self.layers)
                    self.favgrid = gallery.FavouritesGrid(self.textures, self.favourites, on_pick=self._pick)
                    sec.add(self.favgrid)
                    i += 1
                    continue
                if k == "clearAll":
                    sec.add(self._mask_footer(c))
                    i += 1
                    continue
                if t == "hidden":  # stored on the layer, no widget (the stamp's place)
                    i += 1
                    continue
                if k == "shiftHint":  # goes in the mask footer, as BumpMesh
                    i += 1
                    continue
                # Consecutive buttons share a row (BumpMesh's button rows);
                # a group with one show rule shares a box (Rotate, Cylinder Axis).
                if c.get("group") in ("rotate", "cylinder") or t in ("button",) or (t == "toggle" and k != "lockScale"):
                    j = i
                    while j < len(rows) and self._joins(rows[i], rows[j]):
                        j += 1
                    sec.add(self._row_of(rows[i:j]))
                    i = j
                    continue
                sec.add(self._build(c))
                i += 1
            if sec.sid == "load":
                self.mesh_info = QtWidgets.QLabel("")
                self.mesh_info.setObjectName("meshInfo")
                sec.add(self.mesh_info)
            if sec.sid == "displacementMap":
                self.credit_line = QtWidgets.QLabel("")
                self.credit_line.setObjectName("textureCredit")
                self.credit_line.setWordWrap(True)
                sec.add(self.credit_line)
            if sec.sid == "export":
                self.status = QtWidgets.QLabel("")
                self.status.setObjectName("status")
                self.status.setWordWrap(True)
                sec.add(self.status)

        @staticmethod
        def _joins(a, b):
            if a.get("group") in ("rotate", "cylinder"):
                return b.get("group") == a["group"]
            plain = ("button", "toggle")
            return b["type"] in plain and b["key"] != "lockScale" and not b.get("group") and b["key"] not in ("addLayer", "clearAll")

        def _row_of(self, cs):
            """One row (or a box) for several controls: buttons beside each other,
            numbers as label + box."""
            w = QtWidgets.QWidget()
            w.setObjectName("row_" + cs[0]["key"])
            outer = QtWidgets.QVBoxLayout(w)
            outer.setContentsMargins(0, 0, 0, 0)
            nums = [c for c in cs if c["type"] == "number"]
            others = [c for c in cs if c["type"] != "number"]
            if nums:
                h = FlowLayout()
                for c in nums:
                    b = self._number(c, w)
                    pair = QtWidgets.QWidget()  # a label stays with its box when the row wraps
                    ph = QtWidgets.QHBoxLayout(pair)
                    ph.setContentsMargins(0, 0, 0, 0)
                    lab = QtWidgets.QLabel(c["label"] or "")
                    ph.addWidget(lab)
                    ph.addWidget(b.box)
                    h.addWidget(pair)
                    b.lab = lab
                outer.addLayout(h)
            if others:
                h = FlowLayout()
                for c in others:
                    b = self._button(c, w) if c["type"] == "button" else self._toggle(c, w)
                    h.addWidget(b.widgets[0])
                    if not c.get("group"):
                        b.row = b.widgets[0]  # its own show rule (Position on model)
                outer.addLayout(h)
            return w

        def _row(self, c):
            w = QtWidgets.QWidget()
            w.setObjectName("row_" + c["key"])
            if c.get("tooltip"):
                w.setToolTip(c["tooltip"])
            return w

        def _later(self, c, w):
            if c.get("later"):
                w.setEnabled(False)
                w.setToolTip(((c.get("tooltip") or "") + f"\n(later: {c['later']})").strip())

        def _build(self, c):
            t = c["type"]
            if t in ("slider", "log slider"):
                return self._slider(c).row
            if t == "checkbox":
                return self._checkbox(c).row
            if t == "combo":
                return self._combo(c).row
            if t == "buttons":
                return self._buttons(c).row
            if t == "toggle":
                return self._toggle(c, None).row
            if t == "number":
                row = self._row(c)
                h = QtWidgets.QHBoxLayout(row)
                h.setContentsMargins(0, 0, 0, 0)
                b = self._number(c, row)
                b.lab = QtWidgets.QLabel(c["label"] or "")
                h.addWidget(b.lab, 1)
                h.addWidget(b.box)
                return row
            if t == "faces":
                return self._faces(c).row
            if t in ("heading", "note"):
                return self._text(c).row
            return self._button(c, None).row

        def _register(self, b):
            self.binds[b.key] = b
            return b

        def _slider(self, c):
            row = self._row(c)
            b = self._register(_Bind(c, row))
            v = QtWidgets.QVBoxLayout(row)
            v.setContentsMargins(0, 0, 0, 0)
            lab = QtWidgets.QLabel(c["label"])
            if c.get("tooltip"):
                lab.setToolTip(c["tooltip"])
            v.addWidget(lab)
            h = QtWidgets.QHBoxLayout()
            sl = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            sl.setObjectName("slider_" + c["key"])
            lo, hi = positions(c)
            sl.setRange(lo, hi)
            h.addWidget(sl, 1)
            if c.get("box"):
                box = QtWidgets.QDoubleSpinBox()
                box.setObjectName("box_" + c["key"])
                bx = c["box"]
                box.setDecimals(max(decimals(bx.get("step")), decimals(c["slider"].get("step"))) if c["type"] != "log slider" else 2)
                box.setRange(float(bx["min"]), float(bx["max"]))
                st = bx.get("step")
                box.setSingleStep(0.1 if st in (None, "any") else float(st))
                box.setKeyboardTracking(False)
                box.valueChanged.connect(lambda val, k=c["key"]: self._edit(k, val))
                h.addWidget(box)
            else:
                box = QtWidgets.QLabel()
                box.setObjectName("readout_" + c["key"])
                h.addWidget(box)
            v.addLayout(h)
            if c.get("captions"):
                cap = QtWidgets.QHBoxLayout()
                a, z = QtWidgets.QLabel(c["captions"][0]), QtWidgets.QLabel(c["captions"][1])
                z.setAlignment(QtCore.Qt.AlignRight)
                cap.addWidget(a)
                cap.addWidget(z)
                v.addLayout(cap)
            sl.valueChanged.connect(lambda pos, k=c["key"]: self._slid(k, pos))
            sl.sliderReleased.connect(self.draft.end_coalesce)
            b.slider, b.box, b.lab = sl, box, lab
            b.widgets = [sl, box]
            self._later(c, row)

            def set_(val, b=b):
                b.slider.setValue(value_to_pos(b.c, val, self.anchor))
                if isinstance(b.box, QtWidgets.QLabel):
                    b.box.setText(format_m(val) if b.c.get("readout") == "M" else str(val))
                else:
                    b.box.setValue(float(val if val is not None else 0))

            b.set = set_
            b.label = lambda: lab.text()
            return b

        def _checkbox(self, c):
            row = self._row(c)
            b = self._register(_Bind(c, row))
            h = QtWidgets.QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            long_ = len(c["label"] or "") > LONG_CHECK
            # A long label ("Reverse direction (push in instead of out)")
            # wraps beside its box, which a check box's own text cannot; a
            # click on it ticks the box.
            cb = QtWidgets.QCheckBox("" if long_ else c["label"])
            cb.setObjectName("check_" + c["key"])
            if c.get("tooltip"):
                cb.setToolTip(c["tooltip"])
            cb.toggled.connect(lambda on, k=c["key"]: self._edit(k, bool(on)))
            h.addWidget(cb)
            b.widgets = [cb]
            b.set = lambda val: cb.setChecked(bool(val))
            b.label = lambda: cb.text()
            if long_:
                lab = QtWidgets.QLabel(c["label"])
                lab.setWordWrap(True)
                lab.mousePressEvent = lambda _e, cb=cb: cb.isEnabled() and cb.click()
                h.addWidget(lab, 1)
                b.wrapped = lab
                b.label = lambda: lab.text()
            self._later(c, row)
            return b

        def _combo(self, c):
            row = self._row(c)
            b = self._register(_Bind(c, row))
            h = QtWidgets.QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            lab = QtWidgets.QLabel(c["label"])
            cb = QtWidgets.QComboBox()
            cb.setObjectName("combo_" + c["key"])
            # The combo shrinks with the dock (its arrow never under the
            # scrollbar); the open list still shows each option in full.
            cb.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(6)
            b.values = []
            for o in c["options"]:
                if o.get("hidden"):
                    continue  # BumpMesh hides Surface normal until a stamp asks for it
                cb.addItem(o["label"])
                b.values.append(o["value"])
            cb.currentIndexChanged.connect(lambda ix, b=b: self._edit(b.key, b.values[ix]) if 0 <= ix < len(b.values) else None)
            h.addWidget(lab)
            h.addWidget(cb, 1)
            b.widgets = [cb]
            b.combo, b.lab = cb, lab

            def set_(val, b=b):
                if val not in b.values:
                    o = next((o for o in b.c["options"] if o["value"] == val), None)
                    if o is None:
                        return
                    b.values.append(val)
                    b.combo.addItem(o["label"])
                b.combo.setCurrentIndex(b.values.index(val))

            b.set = set_
            b.label = lambda: lab.text()
            b.items = lambda: [cb.itemText(i) for i in range(cb.count())]
            return b

        def _buttons(self, c):
            row = self._row(c)
            b = self._register(_Bind(c, row))
            h = FlowLayout(row)
            lab = None
            if c.get("label") and c["type"] == "buttons" and c["key"] != "maskFaces":
                lab = QtWidgets.QLabel(c["label"])
                if c.get("tooltip"):
                    lab.setToolTip(c["tooltip"])
                h.addWidget(lab)
            b.buttons = []
            for o in c["options"]:
                btn = QtWidgets.QPushButton(title_case(o["label"]))
                btn.setObjectName(f"opt_{c['key']}_{o['value']}")
                btn.setCheckable(True)
                if o.get("tooltip"):
                    btn.setToolTip(o["tooltip"])
                if o.get("later"):
                    btn.setEnabled(False)
                    btn.setToolTip(((o.get("tooltip") or "") + f"\n(later: {o['later']})").strip())
                btn.clicked.connect(lambda on, b=b, v=o["value"]: self._pick_option(b, v, on))
                h.addWidget(btn)
                b.buttons.append((o["value"], btn))
            b.widgets = [x for _, x in b.buttons]
            self._later(c, row)

            def set_(val, b=b):
                for v, btn in b.buttons:
                    btn.setChecked(v == val)

            b.set = set_
            b.label = (lambda: lab.text()) if lab is not None else (lambda: c.get("label"))
            b.option_labels = lambda: [x.text() for _, x in b.buttons]
            return b

        def _pick_option(self, b, v, on):
            # A tool button pressed again turns the tool off (BumpMesh's Brush / Fill).
            if b.key == "exclusionTool" and not on:
                v = None
            self._edit(b.key, v)
            b.set(self._get(b.key))

        def _toggle(self, c, row):
            own = row is None
            if own:
                row = self._row(c)
            b = self._register(_Bind(c, row))
            btn = QtWidgets.QPushButton(title_case(c["label"]) if c.get("label") else "🔒")
            btn.setObjectName("toggle_" + c["key"])
            btn.setCheckable(True)
            if c.get("tooltip"):
                btn.setToolTip(c["tooltip"])
            btn.toggled.connect(lambda on, k=c["key"]: self._edit(k, bool(on)))
            if own:
                h = QtWidgets.QHBoxLayout(row)
                h.setContentsMargins(0, 0, 0, 0)
                h.addWidget(btn)
            b.widgets = [btn]
            b.set = lambda val: btn.setChecked(bool(val))
            b.label = lambda: btn.text() if c.get("label") else None
            self._later(c, btn)
            return b

        def _button(self, c, row):
            own = row is None
            if own:
                row = self._row(c)
            b = self._register(_Bind(c, row))
            btn = QtWidgets.QPushButton(title_case(c["label"]))
            btn.setObjectName("btn_" + c["key"])
            if c.get("tooltip"):
                btn.setToolTip(c["tooltip"])
            btn.clicked.connect(lambda *_, k=c["key"]: self.press(k))
            if own:
                h = QtWidgets.QHBoxLayout(row)
                h.setContentsMargins(0, 0, 0, 0)
                h.addWidget(btn)
            b.widgets = [btn]
            b.label = lambda: btn.text().split("  ")[0]  # without the Texture Gallery count
            self._later(c, btn)
            return b

        def _number(self, c, row):
            b = self._register(_Bind(c, row))
            if c["default"] is None:  # nullable (cylinder axis): empty = BumpMesh's bounding-box value
                box = QtWidgets.QLineEdit()
                box.setPlaceholderText("auto")
                box.setValidator(QtGui.QDoubleValidator())
                box.editingFinished.connect(lambda k=c["key"], x=box: self._edit(k, float(x.text()) if x.text().strip() else None))
                b.set = lambda val: box.setText("" if val is None else f"{float(val):g}")
            else:
                box = QtWidgets.QDoubleSpinBox()
                bx = c.get("box") or {}
                box.setDecimals(decimals(bx.get("step")))
                box.setRange(float(bx["min"]) if bx.get("min") is not None else -360.0,
                             float(bx["max"]) if bx.get("max") is not None else 360.0)
                box.setSingleStep(float(bx["step"]) if bx.get("step") not in (None, "any") else 0.1)
                box.setKeyboardTracking(False)
                if c["scope"] != "frame":  # Rotate X/Y/Z wait for Apply, as BumpMesh
                    box.valueChanged.connect(lambda val, k=c["key"]: self._edit(k, val))
                b.set = lambda val: box.setValue(float(val or 0))
            box.setObjectName("box_" + c["key"])
            if c.get("tooltip"):
                box.setToolTip(c["tooltip"])
            b.box = box
            b.widgets = [box]
            b.label = lambda: b.lab.text() if getattr(b, "lab", None) is not None else c.get("label")
            return b

        def _text(self, c):
            row = self._row(c)
            b = self._register(_Bind(c, row))
            v = QtWidgets.QVBoxLayout(row)
            v.setContentsMargins(0, 0, 0, 0)
            lab = QtWidgets.QLabel(c["label"])
            lab.setObjectName(("heading_" if c["type"] == "heading" else "note_") + c["key"])
            lab.setWordWrap(True)
            if c["type"] == "heading":
                f = lab.font()
                f.setBold(True)
                lab.setFont(f)
            if c.get("tooltip"):
                lab.setToolTip(c["tooltip"])
            v.addWidget(lab)
            if c.get("note"):
                n = QtWidgets.QLabel(c["note"])
                n.setWordWrap(True)
                v.addWidget(n)
            b.widgets = [lab]
            b.label = lambda: lab.text()
            b.set = lambda val: lab.setText(val) if isinstance(val, str) else None
            return b

        def _faces(self, c):
            row = self._row(c)
            b = self._register(_Bind(c, row))
            v = QtWidgets.QVBoxLayout(row)
            v.setContentsMargins(0, 0, 0, 0)
            lst = QtWidgets.QListWidget()
            lst.setObjectName("faces_list")
            lst.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
            lst.setMaximumHeight(110)
            v.addWidget(lst)
            h = FlowLayout()
            add = QtWidgets.QPushButton("Add Selected")
            add.clicked.connect(lambda *_: self.add_selected_faces())
            rm = QtWidgets.QPushButton("Remove")
            rm.clicked.connect(lambda *_: self.draft.mask_remove(faces=[i.text() for i in lst.selectedItems()]))
            by = QtWidgets.QToolButton()
            by.setText("Add By…")
            by.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            menu = QtWidgets.QMenu(by)
            for kind, text in ADD_BY:
                act = menu.addAction(text)
                act.triggered.connect(lambda *_, k=kind: self.press("maskAddBy", add_by=k))
            by.setMenu(menu)
            for x in (add, rm, by):
                h.addWidget(x)
            v.addLayout(h)
            b.list, b.widgets = lst, [lst, add, rm, by]

            def set_(_val=None, b=b):
                b.list.clear()
                for f in self.draft.mask()["faces"]:
                    b.list.addItem(f)

            b.set = set_
            return b

        def _mask_footer(self, c):
            row = self._row(c)
            h = FlowLayout(row)
            self.mask_count = QtWidgets.QLabel(S("excl.initExcluded"))
            self.mask_count.setObjectName("maskCount")
            hint = controls.BY_KEY["shiftHint"]
            self.shift_hint = QtWidgets.QLabel(hint["label"])
            self._register(_Bind(hint, self.shift_hint)).label = lambda: self.shift_hint.text()
            b = self._button(c, row)
            h.addWidget(self.mask_count)
            h.addWidget(self.shift_hint)
            h.addWidget(b.widgets[0])
            return row

        def _footer(self):
            w = QtWidgets.QWidget()
            w.setObjectName("footer")
            v = QtWidgets.QVBoxLayout(w)
            h = QtWidgets.QHBoxLayout()
            self.progress_bar = QtWidgets.QProgressBar()
            self.progress_bar.setRange(0, 1000)
            self.progress_label = QtWidgets.QLabel("")
            self.cancel = QtWidgets.QPushButton("Cancel")
            self.cancel.clicked.connect(lambda *_: self.press("cancel"))
            h.addWidget(self.progress_bar, 1)
            h.addWidget(self.cancel)
            v.addWidget(self.progress_label)
            v.addLayout(h)
            self.credit = QtWidgets.QLabel(CREDIT_HTML)
            self.credit.setObjectName("credit")
            self.credit.setTextFormat(QtCore.Qt.RichText)
            self.credit.setOpenExternalLinks(True)
            self.credit.setWordWrap(True)
            v.addWidget(self.credit)
            self.progress(None)
            return w

        # -- widget -> draft ------------------------------------------------------
        def _get(self, key):
            return self.draft.get(key)

        def _slid(self, key, pos):
            if self._updating:
                return
            b = self.binds[key]
            self._edit(key, pos_to_value(b.c, pos, self.anchor), coalesce=True)

        def _edit(self, key, value, coalesce=False):
            if self._updating:
                return
            try:
                self.draft.set(key, value, coalesce=coalesce)
            except (ValueError, KeyError) as e:
                self.say(str(e))
            # The draft's signal moves the widgets; panel state (ui) may change rows.
            self._sync(key)
            self._visibility()

        # -- draft -> widgets -------------------------------------------------------
        def _sync(self, key):
            b = self.binds.get(key)
            if b is None or b.c["type"] in ("button", "heading", "note") or b.c["scope"] == "action":
                return
            if b.c["type"] == "faces":
                val = None
            else:
                try:
                    val = self._get(key)
                except KeyError:
                    return
            self._updating = True
            try:
                b.set(val)
            finally:
                self._updating = False

        def _on_draft(self, ev):
            kind = ev["kind"]
            self._header()
            if kind == "set" and ev["layer"] not in (None, self.draft.active):
                return
            if kind == "set":
                self._sync(ev["key"])
                self._visibility()
                return
            if kind == "mask":
                self._sync("includeOnly")
                self._sync("maskFaces")
                self._mask_text()
                return
            if kind == "frame":
                for k in ("rotateX", "rotateY", "rotateZ"):
                    self._sync(k)
                return
            if kind == "texture":
                self.refresh_texture()
                return
            self.refresh()

        def refresh(self):
            """Every widget from the draft (after undo, reset, load, layer switch)."""
            for k in self.binds:
                self._sync(k)
            self.layers.refresh()
            self.refresh_texture()
            self._mask_text()
            self._visibility()
            self._header()

        def refresh_texture(self):
            tid = self.draft.layer()["texture"]
            self.favgrid.set_active(tid)
            e = next((e for e in self.textures() if e["id"] == tid), None)
            self.credit_line.setText(gallery.credit_line(e) if e else (tid or ""))
            self.layers.refresh()
            n = len(self.textures())
            g = self.binds["textureGallery"].widgets[0]
            g.setText(title_case(controls.BY_KEY["textureGallery"]["label"]) + (f"  {n}" if n else ""))  # BumpMesh's count

        def _mask_text(self):
            inc = self.draft.layer()["includeOnly"]
            self.mask_count.setText(mask_count_text(self.draft.mask_count(), inc))
            self.mask_count.setToolTip(S("excl.hintInclude") if inc else S("excl.hintExclude"))

        def _visibility(self):
            for b in self.binds.values():
                if b.c.get("show"):
                    b.row.setVisible(shown(b.c, self._get))

        def _header(self):
            self.binds["undo"].widgets[0].setEnabled(self.draft.can_undo())
            self.binds["redo"].widgets[0].setEnabled(self.draft.can_redo())


        # -- actions ---------------------------------------------------------------
        def press(self, key, **extra):
            """A button: the draft's own, or a tool through the runner."""
            d = self.draft
            if key == "undo":
                d.undo()
            elif key == "redo":
                d.redo()
            elif key == "resetSettings":
                if self.confirm():
                    d.reset()
            elif key == "clearAll":
                d.mask_clear()
            elif key == "addLayer":
                d.add_layer()
            elif key == "rotateApply":
                d.set_frame(rotation=[self.binds[k].box.value() for k in ("rotateX", "rotateY", "rotateZ")])
            elif key == "rotateReset":
                d.reset_frame()
                for k in ("rotateX", "rotateY", "rotateZ"):
                    self.binds[k].set(0)
            elif key == "textureGallery":
                self.show_gallery()
            else:
                return self.run(key, **extra)
            self._header()
            return True, {}

        def run(self, key, **extra):
            tool, args = ACTIONS[key]
            args = dict(args, layer=self.draft.active, **extra)
            if self.runner is None:
                self.say(f"{key}: not available here")
                return False, {"error": "no runner"}
            try:
                ok, out = self.runner(tool, args)
            except Exception as e:  # a runner error is shown, never raised into Qt
                ok, out = False, {"error": str(e)}
            self.last = (tool, args, ok, out)
            msg = out.get("error") or out.get("message") if isinstance(out, dict) else str(out)
            self.say(msg or ("done" if ok else "failed"))
            self._header()
            return ok, out

        def add_selected_faces(self):
            faces = list(self.selection() or [])
            if faces:
                self.draft.mask_add(faces=faces)
            else:
                self.run("maskAddSelected")

        def say(self, text):
            self.status.setText(text or "")

        def _pick(self, entry):
            self.draft.set_texture(entry["id"], defaults=entry.get("defaults"))

        def show_gallery(self):
            """The Texture Gallery, slid out over the settings inside this
            panel."""
            if self.open_gallery is not None:
                return self.open_gallery()
            if self._gallery is None:
                self._gallery = gallery.GalleryDock(self.textures, self.favourites, draft=self.draft, runner=self.runner,
                                                    on_favourites=self.favgrid.refresh, on_done=self.hide_gallery)
                page = QtWidgets.QWidget()
                v = QtWidgets.QVBoxLayout(page)
                v.setContentsMargins(0, 0, 0, 0)
                head = QtWidgets.QLabel(S("gallery.title"))
                head.setProperty("role", "title")
                v.addWidget(head)
                v.addWidget(self._gallery.widget(), 1)
                self.gallery_page = page
                self.pages.addWidget(page)
            self.pages.setCurrentWidget(self.gallery_page)
            self.show()
            return self._gallery

        def hide_gallery(self):
            """The gallery slid back: the settings again."""
            if self._gallery is not None:
                self._gallery.stop_spin()
            self.pages.setCurrentWidget(self.scroll)

        def gallery_open(self):
            return self._gallery is not None and self.pages.currentWidget() is self.gallery_page

        # -- outside updates ------------------------------------------------------------
        def set_anchor(self, mm):
            """The part's largest bounding-box edge: the log slider's anchor (BumpMesh)."""
            self.anchor = float(mm) if mm else 50.0
            self._sync("scaleU")
            self._sync("scaleV")

        def set_mesh_info(self, triangles, size, mb=None):
            sx, sy, sz = size
            self.mesh_info.setText(S("ui.meshInfo", n=f"{int(triangles):,}", mb=f"{mb:.1f}" if mb is not None else "–",
                                     sx=f"{sx:.1f}", sy=f"{sy:.1f}", sz=f"{sz:.1f}"))
            self.set_anchor(max(size))

        def set_estimate(self, text):
            self.binds["estimate"].set(text)

        def progress(self, stage, fraction=None):
            """stage None hides the bar; fraction None is busy."""
            on = stage is not None
            for w in (self.progress_bar, self.cancel, self.progress_label):
                w.setVisible(on)
            if on:
                self.progress_label.setText(stage)
                if fraction is None:
                    self.progress_bar.setRange(0, 0)
                else:
                    self.progress_bar.setRange(0, 1000)
                    self.progress_bar.setValue(int(max(0.0, min(1.0, fraction)) * 1000))

        # -- reading (tests, Agent Link snapshots) ------------------------------------------
        def section_titles(self):
            return [s.title for s in self.sections]

        def labels(self):
            return {k: b.label() for k, b in self.binds.items()}

        def _ask_reset(self):
            r = QtWidgets.QMessageBox.question(self, TITLE, S("alerts.resetConfirm"))
            return r == QtWidgets.QMessageBox.Yes

        def closeEvent(self, ev):  # noqa: N802 (Qt)
            if self._unsub:
                self._unsub()
                self._unsub = None
            super().closeEvent(ev)


def _freecad_faces():
    """Face names picked in FreeCAD's 3D view, if FreeCAD's GUI is here."""
    try:
        import FreeCADGui

        out = []
        for s in FreeCADGui.Selection.getSelectionEx():
            out.extend(n for n in s.SubElementNames if n.startswith("Face"))
        return out
    except Exception:
        return []


def dock(main_window, sidebar):
    """Dock the sidebar on the right, as BumpMesh's sidebar sits."""
    main_window.addDockWidget(QtCore.Qt.RightDockWidgetArea, sidebar)
    sidebar.show()
    return sidebar
