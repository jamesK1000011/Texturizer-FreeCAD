# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# The gallery's parts, labels, categories, default favourites, credit badges
# and filter rules follow BumpMesh (stlTexturizer) by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer
# (js/textureGallery.js, js/presetTextures.js, index.html).
"""The Texture Gallery dock and the favourites grid of the sidebar.

The library comes through an injected list_textures() -> [entry]; an entry is
{id, name, category, thumb, image, credit, licence, source_url, defaults}
(the engine's library.list()). Picks go to on_pick(entry), by default
draft.set_texture on the active layer. The plain parts (filter_entries,
badge, Favourites) need no Qt.
"""
import json
import os

from . import controls
from .qt import QtCore, QtGui, QtWidgets

S = controls.string
COLUMNS = 4  # BumpMesh's favourites grid is 4 wide, one row per 4
CUSTOM = "custom"
CATEGORY_LABEL = {c["id"]: c["label"] for c in controls.CATEGORIES}
CATEGORY_LABEL[CUSTOM] = S("gallery.catCustom")
# Filter chips in BumpMesh's order: All, Favorites, Your textures, then the categories.
FILTERS = ([("all", S("gallery.catAll")), ("favourites", S("gallery.catFavourites")), (CUSTOM, S("gallery.catCustom"))]
           + [(c["id"], c["label"]) for c in controls.CATEGORIES])
BUMPMESH_CREDIT = "Based on BumpMesh by Stefan Hermann (CNC Kitchen) · AGPL-3.0"
BUMPMESH_URL = "https://github.com/CNCKitchen/stlTexturizer"


def is_custom(entry):
    return entry.get("category") == CUSTOM or str(entry.get("credit", "")).lower() in ("custom", "yours")


def badge(entry):
    """(badge, title) for a tile: BumpMesh's HP / CC0 / FF, Yours for your
    own maps, CNC Kitchen for BumpMesh's own textures."""
    if is_custom(entry):
        return "Yours", S("gallery.customTitle")
    cr = entry.get("credit")
    key = cr.lower() if isinstance(cr, str) else None
    if key in controls.CREDITS:
        b = controls.CREDITS[key]
        return b["badge"], b["title"]
    text = str(cr or "")
    if "hero" in text.lower():
        return controls.CREDITS["hero"]["badge"], controls.CREDITS["hero"]["title"]
    if "cc0" in text.lower() or "ambientcg" in text.lower() or "poly haven" in text.lower():
        return controls.CREDITS["cc0"]["badge"], controls.CREDITS["cc0"]["title"]
    if "filter forge" in text.lower():
        return controls.CREDITS["ff"]["badge"], controls.CREDITS["ff"]["title"]
    return "CNC Kitchen", text or "CNC Kitchen (BumpMesh)"


def credit_line(entry):
    """name · source · licence (· link), for the Displacement Map section."""
    if not entry:
        return ""
    parts = [entry.get("name") or entry.get("id"), badge(entry)[1]]
    if entry.get("licence"):
        parts.append(entry["licence"])
    if entry.get("source_url"):
        parts.append(entry["source_url"])
    return " · ".join(p for p in parts if p)


def _matches(entry, q):
    if not q:
        return True
    cat = CATEGORY_LABEL.get(entry.get("category"), str(entry.get("category") or "")).lower()
    return q in str(entry.get("name", "")).lower() or q in cat


def filter_entries(entries, flt="all", query="", favourites=()):
    """The gallery body as [(section title or None, [entry])], by BumpMesh's
    rules: All with no search shows Your textures then each category; a search
    or another filter shows one flat list; a search matches the name or the
    category's label."""
    q = (query or "").strip().lower()
    entries = list(entries)
    by_id = {e["id"]: e for e in entries}
    customs = [e for e in entries if is_custom(e)]
    presets = [e for e in entries if not is_custom(e)]
    if flt == CUSTOM:
        keys = [e for e in customs if _matches(e, q)]
        return [(S("gallery.customTitle"), keys)] if keys else []
    if flt == "all" and not q:
        out = []
        if customs:
            out.append((S("gallery.customTitle"), customs))
        for c in controls.CATEGORIES:
            keys = [e for e in presets if e.get("category") == c["id"]]
            if keys:
                out.append((c["label"], keys))
        return out
    if flt == "favourites":
        pool = [by_id[f] for f in favourites if f in by_id]
    elif flt == "all":
        pool = customs + presets
    else:
        pool = [e for e in presets if e.get("category") == flt]
    keys = [e for e in pool if _matches(e, q)]
    return [(None, keys)] if keys else []


def empty_note(flt, query):
    return S("ui.noFavourites") if flt == "favourites" and not (query or "").strip() else S("gallery.noResults")


def _user_dir():
    d = os.environ.get("TEXTURIZER_HOME")
    if d:
        return d
    try:
        import FreeCAD

        return os.path.join(FreeCAD.getUserAppDataDir(), "Texturizer")
    except Exception:
        return None


class Favourites:
    """Starred textures (ids), kept in the user data folder as BumpMesh keeps
    them in localStorage. Starts from BumpMesh's DEFAULT_FAVOURITES, matched
    by name or id once the library is known."""

    def __init__(self, path=None, entries=None):
        if path is None:
            d = _user_dir()
            path = os.path.join(d, "favourites.json") if d else None
        self.path = path
        self.ids = None
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.ids = [str(i) for i in json.load(f)]
            except (OSError, ValueError):
                self.ids = None
        if self.ids is None:
            self.ids = self.defaults(entries or [])

    @staticmethod
    def defaults(entries):
        names = {str(e.get("name", "")).lower(): e["id"] for e in entries}
        ids = {str(e["id"]).lower(): e["id"] for e in entries}
        out = []
        for n in controls.DEFAULT_FAVOURITES:
            i = names.get(n.lower()) or ids.get(n.lower())
            out.append(i if i is not None else n)
        return out

    def shown(self, entries):
        known = {e["id"] for e in entries}
        return [i for i in self.ids if i in known]

    def __contains__(self, i):
        return i in self.ids

    def toggle(self, i):
        self.ids = [x for x in self.ids if x != i] if i in self.ids else self.ids + [i]
        self.save()
        return i in self.ids

    def reset(self, entries=()):
        self.ids = self.defaults(list(entries))
        self.save()

    def save(self):
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.ids, f)


_PIX = {}


def thumb_icon(entry, size=56):
    """The tile picture: the entry's thumb (or image) file; a blank icon when
    Qt can't read it (WebP may be missing on the Mac)."""
    path = entry.get("thumb") or entry.get("image")
    key = (path, size)
    if key not in _PIX:
        pm = QtGui.QPixmap(path) if path and os.path.exists(str(path)) else QtGui.QPixmap()
        if not pm.isNull():
            pm = pm.scaled(size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        _PIX[key] = QtGui.QIcon(pm) if not pm.isNull() else QtGui.QIcon()
    return _PIX[key]


def _tile_tip(entry):
    b, title = badge(entry)
    return f"{entry.get('name', entry['id'])} · {title}"


if QtWidgets is not None:

    class Tile(QtWidgets.QToolButton):
        """One texture: thumbnail, name, credit badge; checked when active."""

        def __init__(self, entry, on_pick, size=56, parent=None):
            super().__init__(parent)
            self.entry = entry
            self.setObjectName("tile_" + str(entry["id"]))
            self.setCheckable(True)
            self.setAutoRaise(True)
            self.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
            self.setIcon(thumb_icon(entry, size))
            self.setIconSize(QtCore.QSize(size, size))
            self.badge = badge(entry)[0]
            self.setText(f"{entry.get('name', entry['id'])}\n[{self.badge}]")
            self.setToolTip(_tile_tip(entry))
            self.clicked.connect(lambda *_: on_pick(entry))

    class FavouritesGrid(QtWidgets.QWidget):
        """The sidebar's favourites grid (section 2): 4 wide, one row per 4,
        plus a tile for a picked texture that is not a favourite, as BumpMesh."""

        def __init__(self, list_textures, favourites=None, on_pick=None, parent=None):
            super().__init__(parent)
            self.setObjectName("TexturizerFavourites")
            self.list_textures = list_textures
            self.favourites = favourites or Favourites(entries=list_textures())
            self.on_pick = on_pick or (lambda e: None)
            self.active = None
            self.tiles = []
            self.grid = QtWidgets.QGridLayout(self)
            self.grid.setContentsMargins(0, 0, 0, 0)
            self.empty = QtWidgets.QLabel(S("ui.noFavourites"))
            self.empty.setWordWrap(True)
            self.refresh()

        def ids(self):
            entries = self.list_textures()
            ids = self.favourites.shown(entries)
            known = {e["id"] for e in entries}
            if self.active is not None and self.active not in ids and self.active in known:
                ids = ids + [self.active]
            return ids

        def refresh(self):
            while self.grid.count():
                w = self.grid.takeAt(0).widget()
                if w is not None and w is not self.empty:  # later: the clicked tile may be one of them
                    w.hide()
                    w.deleteLater()
            by_id = {e["id"]: e for e in self.list_textures()}
            self.tiles = []
            ids = self.ids()
            for n, i in enumerate(ids):
                t = Tile(by_id[i], self.on_pick, size=40)
                t.setChecked(i == self.active)
                self.grid.addWidget(t, n // COLUMNS, n % COLUMNS)
                self.tiles.append(t)
            self.empty.setVisible(not ids)
            if not ids:
                self.grid.addWidget(self.empty, 0, 0, 1, COLUMNS)

        def set_active(self, texture_id):
            self.active = texture_id
            self.refresh()

    class GalleryDock(QtWidgets.QDockWidget):
        """BumpMesh's Texture Gallery side panel as a dock: Search textures,
        Filter by category, the grid with stars, Favorites: n, Reset
        favorites, Your textures, the hint, Done, and a credit per tile."""

        def __init__(self, list_textures, favourites=None, on_pick=None, draft=None, runner=None,
                     on_favourites=None, parent=None):
            super().__init__(S("gallery.title"), parent)
            self.setObjectName("TexturizerGallery")
            self.list_textures = list_textures
            self.favourites = favourites or Favourites(entries=list_textures())
            self.draft = draft
            self.runner = runner
            self.on_favourites = on_favourites or (lambda: None)
            self.on_pick = on_pick or self._pick_into_draft
            self.filter = "all"
            self.tiles = {}
            self._unsub = draft.subscribe(self._draft_changed) if draft is not None else None

            body = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(body)
            head = QtWidgets.QHBoxLayout()
            title = QtWidgets.QLabel(S("gallery.title"))
            title.setProperty("role", "title")
            head.addWidget(title)
            head.addStretch(1)
            self.turntable = QtWidgets.QPushButton(S("gallery.turntable"))
            self.turntable.setToolTip(S("gallery.turntableTitle") + " (later)")
            self.turntable.setEnabled(False)
            head.addWidget(self.turntable)
            self.done = QtWidgets.QPushButton(S("gallery.done"))
            self.done.clicked.connect(lambda *_: self.close())
            head.addWidget(self.done)
            v.addLayout(head)

            self.search = QtWidgets.QLineEdit()
            self.search.setPlaceholderText(S("gallery.search"))
            self.search.setClearButtonEnabled(True)
            self.search.textChanged.connect(lambda *_: self.render())
            v.addWidget(self.search)

            chips = QtWidgets.QWidget()
            chips.setAccessibleName(S("gallery.filterLabel"))
            chips.setToolTip(S("gallery.filterLabel"))
            flow = QtWidgets.QGridLayout(chips)
            flow.setContentsMargins(0, 0, 0, 0)
            self.chips = {}
            self.chip_group = QtWidgets.QButtonGroup(self)
            self.chip_group.setExclusive(True)
            for n, (fid, label) in enumerate(FILTERS):
                b = QtWidgets.QToolButton()
                b.setText(label)
                b.setCheckable(True)
                b.setChecked(fid == self.filter)
                b.clicked.connect(lambda *_, f=fid: self.set_filter(f))
                self.chip_group.addButton(b)
                flow.addWidget(b, n // 4, n % 4)
                self.chips[fid] = b
            v.addWidget(chips)

            status = QtWidgets.QHBoxLayout()
            self.hint = QtWidgets.QLabel(S("gallery.hint"))
            self.hint.setWordWrap(True)
            status.addWidget(self.hint, 1)
            self.fav_count = QtWidgets.QLabel()
            status.addWidget(self.fav_count)
            self.fav_reset = QtWidgets.QPushButton(S("gallery.resetFavourites"))
            self.fav_reset.setFlat(True)
            self.fav_reset.clicked.connect(lambda *_: self.reset_favourites())
            status.addWidget(self.fav_reset)
            v.addLayout(status)

            self.scroll = QtWidgets.QScrollArea()
            self.scroll.setWidgetResizable(True)
            v.addWidget(self.scroll, 1)

            credits = QtWidgets.QLabel(S("gallery.credits") + "<br>" + BUMPMESH_CREDIT
                                       + f' · <a href="{BUMPMESH_URL}">{BUMPMESH_URL}</a>')
            credits.setWordWrap(True)
            credits.setTextFormat(QtCore.Qt.RichText)
            credits.setOpenExternalLinks(True)
            v.addWidget(credits)
            self.credits = credits
            self.setWidget(body)
            self.render()

        # -- state ---------------------------------------------------------------
        def active_id(self):
            return self.draft.layer()["texture"] if self.draft is not None else None

        def _pick_into_draft(self, entry):
            if self.draft is not None:
                self.draft.set_texture(entry["id"], defaults=entry.get("defaults"))

        def _draft_changed(self, ev):
            if ev["kind"] in ("texture", "active", "layers", "undo", "redo", "reset", "load"):
                self.sync()

        def set_filter(self, fid):
            self.filter = fid
            for f, b in self.chips.items():
                b.setChecked(f == fid)
            self.render()

        def toggle_favourite(self, entry):
            self.favourites.toggle(entry["id"])
            self.on_favourites()
            if self.filter == "favourites":
                self.render()
            else:
                self.sync()

        def reset_favourites(self):
            self.favourites.reset(self.list_textures())
            self.on_favourites()
            self.render()

        def sections(self):
            return filter_entries(self.list_textures(), self.filter, self.search.text(), self.favourites.ids)

        def shown_ids(self):
            return [e["id"] for _, es in self.sections() for e in es]

        # -- drawing ---------------------------------------------------------------
        def render(self):
            body = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(body)
            self.tiles = {}
            self.stars = {}
            secs = self.sections()
            for title, es in secs:
                if title:
                    h = QtWidgets.QLabel(title)
                    h.setProperty("role", "title")
                    v.addWidget(h)
                g = QtWidgets.QGridLayout()
                for n, e in enumerate(es):
                    cell = QtWidgets.QWidget()
                    cv = QtWidgets.QVBoxLayout(cell)
                    cv.setContentsMargins(0, 0, 0, 0)
                    t = Tile(e, self.on_pick)
                    cv.addWidget(t)
                    row = QtWidgets.QHBoxLayout()
                    star = QtWidgets.QToolButton()
                    star.setAutoRaise(True)
                    star.clicked.connect(lambda *_, x=e: self.toggle_favourite(x))
                    row.addWidget(star)
                    if is_custom(e):
                        dl = QtWidgets.QToolButton()
                        dl.setText("⤓")
                        dl.setToolTip(S("gallery.downloadCustom"))
                        dl.clicked.connect(lambda *_, x=e: self._run("texture_custom", {"action": "download", "id": x["id"]}))
                        rm = QtWidgets.QToolButton()
                        rm.setText("✕")
                        rm.setToolTip(S("gallery.deleteCustom"))
                        rm.clicked.connect(lambda *_, x=e: self._run("texture_custom", {"action": "delete", "id": x["id"]}))
                        row.addWidget(dl)
                        row.addWidget(rm)
                    cv.addLayout(row)
                    g.addWidget(cell, n // COLUMNS, n % COLUMNS)
                    self.tiles[e["id"]] = t
                    self.stars[e["id"]] = star
                v.addLayout(g)
            if self.filter == CUSTOM and not secs and not self.search.text().strip():
                self.empty = QtWidgets.QLabel(S("gallery.customEmpty"))
            elif not secs:
                self.empty = QtWidgets.QLabel(empty_note(self.filter, self.search.text()))
            else:
                self.empty = None
            if self.empty is not None:
                self.empty.setWordWrap(True)
                v.addWidget(self.empty)
            v.addStretch(1)
            old = self.scroll.takeWidget()
            if old is not None:  # later: a star's own click may be what re-renders
                old.hide()
                old.deleteLater()
            self.scroll.setWidget(body)
            self.sync()

        def sync(self):
            """Stars, the active tile and Favorites: n, without a rebuild."""
            active = self.active_id()
            for i, t in self.tiles.items():
                t.setChecked(i == active)
            for i, s in self.stars.items():
                on = i in self.favourites
                s.setText("★" if on else "☆")
                s.setToolTip(S("gallery.removeFavourite") if on else S("gallery.addFavourite"))
            self.fav_count.setText(S("gallery.favCount", n=len(self.favourites.shown(self.list_textures()))))

        def _run(self, tool, args):
            if self.runner is None:
                return False, {"error": "no runner"}
            out = self.runner(tool, args)
            self.render()
            return out

