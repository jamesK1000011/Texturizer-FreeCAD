# SPDX-License-Identifier: AGPL-3.0-only
# Texturizer's FreeCAD commands, no agent needed. The Settings sidebar
# (Texturizer_Texture) and BumpMesh's header row as a toolbar: Undo, Redo,
# Reset to defaults, Save Project, Load Project, Texture Gallery, Export STL,
# Export 3MF (each the sidebar's own button, so it runs the same tool through
# the same runner); Add to document / Update; the "View" toolbar, FreeCAD's
# own view commands under BumpMesh's labels; Prepare for texture, Check
# textured mesh and the Texture panel. The texturing is BumpMesh's engine
# (Stefan Hermann, CNC Kitchen) in texturizer.engine, run in FreeCAD.
import os

from . import CREDIT, controls, handlers, overrides, panel, title_case

PREPARE, CHECK, PANEL = "Texturizer_Prepare", "Texturizer_Check", "Texturizer_Panel"
TEXTURE, ADD = "Texturizer_Texture", "Texturizer_AddToDocument"
# BumpMesh's header row: (command, the sidebar button it presses, icon).
# Undo, Redo and Export STL are FreeCAD's own (Edit > Undo undoes texture
# edits; File > Export exports the object Add to Document makes):
# overrides.OWNED. Save / Load Project share with the BumpMesh web app.
HEADER = [("Texturizer_Reset", "resetSettings"),
          ("Texturizer_SaveProject", "saveProject"), ("Texturizer_LoadProject", "loadProject"),
          ("Texturizer_Gallery", "textureGallery"), ("Texturizer_Export3MF", "export3mf")]
# Section runs FreeCAD's Std_ToggleClipPlane (toggle_section only tabs its
# dock so the window keeps its size). Wireframe and Perspective are FreeCAD's
# Draw Style and Std_PerspectiveCamera / Std_OrthographicCamera.
VIEW = [("Texturizer_Section", "Section View", "Cut the view with FreeCAD's clipping plane; press again to close it."),
        ("Texturizer_Turntable", "Turntable",
         "Spin the 3D view slowly about the part's vertical axis; press again, or click, wheel or move the SpaceMouse in the view, to stop.")]
# A toolbar button's tooltip says what it does, then the sidebar's own
# BumpMesh tooltip (kept word for word). Where BumpMesh's tooltip only
# repeats the name, the first part carries the meaning. No credit here:
# macOS cuts long tooltips short, and the credit is in About, the sidebar's
# footer and the workbench's own tooltip.
TIPS = {"resetSettings": "Put every texture setting back to its default.",
        "saveProject": "Write the texture settings and masks (and the model) as a BumpMesh project for the BumpMesh web app.",
        "loadProject": "Read a BumpMesh project's texture settings and masks (and its model) into this document.",
        "textureGallery": "Browse the textures and pick one.",
        "export3mf": "Export the textured mesh as 3MF."}


def _sentence(text):
    text = (text or "").strip()
    return text if not text or text[-1] in ".!?" else text + "."


def tooltip(key):
    """The command's tooltip for a sidebar button key (see TIPS)."""
    c = controls.BY_KEY[key]
    if overrides.label(c):   # FreeCAD's meaning, not BumpMesh's button
        return TIPS.get(key) or overrides.tooltip(c)
    own = _sentence(c.get("tooltip") or c["label"])
    lead = TIPS.get(key)
    parts = [lead] if lead else []
    if own.rstrip(".").lower() not in (c["label"].replace(" ⓘ", "").lower(), (lead or "").rstrip(".").lower()):
        parts.append(own)
    elif not lead:
        parts.append(own)
    return " ".join(parts)


TOOLBAR = [n for n, _ in HEADER]
VIEW_TOOLBAR = [n for n, _l, _t in VIEW]
CHECK_TOOLBAR = [PREPARE, CHECK]
NAMES = [TEXTURE] + TOOLBAR + [ADD] + VIEW_TOOLBAR + [PREPARE, CHECK, PANEL]


def faces_from_selection(sel):
    """(body label, [face names]) from FreeCADGui.Selection.getSelectionEx()
    entries: the first object with faces picked, its picked faces only."""
    for s in sel or []:
        faces = [n for n in (getattr(s, "SubElementNames", None) or []) if n.startswith("Face")]
        if faces:
            return s.Object.Label, sorted(set(faces), key=lambda n: int(n[4:]) if n[4:].isdigit() else 0)
    return None, []


def default_check_path():
    """The textured STL named for the last prepared file."""
    last = (handlers.LAST["prepared"] or [{}])[0]
    stl = last.get("stl")
    return (os.path.splitext(stl)[0].replace("-for-texture", "") + "-textured.stl") if stl else ""


def _qt():
    from PySide import QtGui

    try:
        from PySide import QtWidgets
    except ImportError:
        QtWidgets = QtGui
    return QtWidgets


def _say(title, ok, out):
    import FreeCAD

    if ok:
        FreeCAD.Console.PrintMessage(f"Texturizer: {out.get('note', '')}\n")
        return True
    msg = (out.get("error") or {}).get("message", str(out))
    FreeCAD.Console.PrintWarning(f"Texturizer: {msg}\n")
    QtWidgets = _qt()
    QtWidgets.QMessageBox.warning(None, title, msg)
    return False


def reveal(path):
    if not path:
        return
    from PySide import QtCore, QtGui

    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(os.path.dirname(path)))


def _path_row(QtWidgets, edit, save, caption):
    row = QtWidgets.QHBoxLayout()
    row.addWidget(edit)
    b = QtWidgets.QPushButton("…")
    b.setFixedWidth(32)
    b.setToolTip("Select the STL file.")

    def browse():
        f = (QtWidgets.QFileDialog.getSaveFileName if save else QtWidgets.QFileDialog.getOpenFileName)(None, caption, edit.text() or os.path.expanduser("~"), "STL (*.stl)")
        f = f[0] if isinstance(f, tuple) else f
        if f:
            edit.setText(f)

    b.clicked.connect(browse)
    row.addWidget(b)
    return row


class PrepareTask:
    """Task panel: the selected faces, include or exclude, tolerance, where
    the STL goes (the mask is written beside it)."""

    def __init__(self, body, faces):
        QtWidgets = _qt()
        self.body, self.faces = body, faces
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("Prepare for Texture")
        lay = QtWidgets.QFormLayout(self.form)
        lay.addRow("Body", QtWidgets.QLabel(body))
        lay.addRow("Faces", QtWidgets.QLabel(", ".join(faces)))
        self.mode = QtWidgets.QComboBox()
        self.mode.addItem("Texture only these faces", "include")
        self.mode.addItem("Texture all but these faces", "exclude")
        self.mode.setToolTip("Select which faces get the texture.")
        lay.addRow("Mode", self.mode)
        self.tol = QtWidgets.QDoubleSpinBox()
        self.tol.setDecimals(3)
        self.tol.setRange(0.001, 1.0)
        self.tol.setSingleStep(0.005)
        self.tol.setValue(0.02)
        self.tol.setSuffix(" mm")
        self.tol.setToolTip("Set how far the STL may be from the body, in mm. A smaller value gives a larger file.")
        lay.addRow("Tolerance", self.tol)
        out = handlers.Context().out_dir
        self.path = QtWidgets.QLineEdit(os.path.join(out, body.replace(" ", "_") + "-for-texture.stl"))
        self.path.setToolTip("Type the path of the STL that this step writes. The mask goes in the same folder.")
        lay.addRow("STL", _path_row(QtWidgets, self.path, True, "STL for BumpMesh"))
        note = QtWidgets.QLabel(CREDIT)
        note.setWordWrap(True)
        lay.addRow(note)

    def accept(self):
        import FreeCADGui

        args = {"body": self.body, "faces": self.faces, "mode": self.mode.currentData(), "tolerance": self.tol.value()}
        if self.path.text().strip():
            args["path"] = self.path.text().strip()
            os.makedirs(os.path.dirname(args["path"]) or ".", exist_ok=True)
        ok, out = panel.run("texture_prepare", args)
        if _say("Prepare for Texture", ok, out):
            FreeCADGui.Control.closeDialog()
            panel.show_dock()
        return ok

    def reject(self):
        import FreeCADGui

        FreeCADGui.Control.closeDialog()
        return True


class CheckTask:
    """Task panel: the textured STL, reach (0: from the part's size) and
    flatness (how far a point may move and still count as plain)."""

    def __init__(self, body):
        QtWidgets = _qt()
        self.body = body
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("Check Textured Mesh")
        lay = QtWidgets.QFormLayout(self.form)
        lay.addRow("Body", QtWidgets.QLabel(body or "the one body shown"))
        self.path = QtWidgets.QLineEdit(default_check_path())
        self.path.setToolTip("Type the path of the textured STL to check.")
        lay.addRow("Textured STL", _path_row(QtWidgets, self.path, False, "Textured STL"))
        self.reach = QtWidgets.QDoubleSpinBox()
        self.reach.setRange(0, 100)
        self.reach.setDecimals(2)
        self.reach.setSpecialValueText("from the part's size")
        self.reach.setSuffix(" mm")
        self.reach.setToolTip("Set how far from the body a point may be and still count, in mm. 0 uses the size of the part.")
        lay.addRow("Reach", self.reach)
        self.flat = QtWidgets.QDoubleSpinBox()
        self.flat.setRange(0.001, 1)
        self.flat.setDecimals(3)
        self.flat.setValue(0.01)
        self.flat.setSuffix(" mm")
        self.flat.setToolTip("Set how far a point may move and still count as smooth, in mm.")
        lay.addRow("Flatness", self.flat)
        note = QtWidgets.QLabel(CREDIT)
        note.setWordWrap(True)
        lay.addRow(note)

    def accept(self):
        import FreeCADGui

        args = {"path": self.path.text().strip(), "flat": self.flat.value()}
        if self.body:
            args["body"] = self.body
        if self.reach.value() > 0:
            args["reach"] = self.reach.value()
        last = (handlers.LAST["prepared"] or [{}])[0]
        if last.get("stl") and os.path.splitext(last["stl"])[0].replace("-for-texture", "") == os.path.splitext(args["path"])[0].replace("-textured", ""):
            args["mask"] = last["mask"]
            args.setdefault("body", last.get("body"))
        ok, out = panel.run("texture_check", args)
        if _say("Check Textured Mesh", ok, out):
            FreeCADGui.Control.closeDialog()
            panel.show_dock()
        return ok

    def reject(self):
        import FreeCADGui

        FreeCADGui.Control.closeDialog()
        return True


def show_prepare():
    import FreeCADGui

    body, faces = faces_from_selection(FreeCADGui.Selection.getSelectionEx())
    if not faces:
        _qt().QMessageBox.information(None, "Prepare for Texture", "Select the faces to texture (or to keep plain) on one body first, then Prepare for Texture.")
        return
    FreeCADGui.Control.showDialog(PrepareTask(body, faces))


def show_check():
    import FreeCADGui

    body, _faces = faces_from_selection(FreeCADGui.Selection.getSelectionEx())
    if body is None:
        sel = FreeCADGui.Selection.getSelection()
        body = sel[0].Label if sel else (handlers.LAST["prepared"] or [{}])[0].get("body")
    FreeCADGui.Control.showDialog(CheckTask(body))


class _Prepare:
    def GetResources(self):
        return {"Pixmap": "Texturizer_Prepare", "MenuText": "Prepare for Texture",
                "ToolTip": "Write the body as an STL for texturing, with a mask of the selected faces (texture only them, or all but them)."}

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        show_prepare()


class _Check:
    def GetResources(self):
        return {"Pixmap": "Texturizer_Check", "MenuText": "Check Textured Mesh",
                "ToolTip": "Measure a textured STL against its body face by face: depth on the textured faces, any face it leaked onto, a closed mesh."}

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        show_check()


class _Panel:
    def GetResources(self):
        return {"Pixmap": "Texturizer_Workbench", "MenuText": "Texture Panel",
                "ToolTip": "Show the Texture panel: the last result, the last check, and the sidebar."}

    def IsActive(self):
        return True

    def Activated(self):
        panel.show_dock()


def open_sidebar(load=False):
    """The Settings sidebar, where the panel layout setting puts it; its Texture Gallery slides out inside it."""
    from . import window

    return window.get().open(load=load)


class _Texture:
    def GetResources(self):
        return {"Pixmap": "Texturizer_Workbench", "MenuText": "Settings Sidebar",
                "ToolTip": "Open the settings sidebar and the Texture Gallery, with the selected body loaded."}

    def IsActive(self):
        return True

    def Activated(self):
        from . import layout, live

        # Slid out over the 3D view (PanelLayout left-slide): opens and
        # closes it; else shows the sidebar.
        layout.toggle_or(lambda: open_sidebar(load=live.draft().part is None))


class _Press:
    """A header button: the sidebar's own button of the same name."""

    def __init__(self, name, key):
        self.name, self.key = name, key

    def GetResources(self):
        c = controls.BY_KEY[self.key]
        # Title Case, as FreeCAD's design guide asks of command names.
        menu = overrides.label(c) or title_case(c["label"].replace(" ⓘ", ""))
        return {"Pixmap": self.name, "MenuText": menu,
                "ToolTip": tooltip(self.key)}

    def IsActive(self):
        import FreeCAD

        return self.key in ("textureGallery", "loadProject") or FreeCAD.ActiveDocument is not None

    def Activated(self):
        from . import window

        window.get().press(self.key)


class _View:
    def __init__(self, name, label, tip):
        self.name, self.label, self.tip = name, label, tip

    def GetResources(self):
        return {"Pixmap": self.name, "MenuText": self.label, "ToolTip": self.tip}

    def IsActive(self):
        import FreeCADGui

        return FreeCADGui.ActiveDocument is not None

    def Activated(self):
        import FreeCADGui

        if self.name == "Texturizer_Turntable":
            return toggle_turntable()
        toggle_section(FreeCADGui.getMainWindow(), lambda: FreeCADGui.runCommand("Std_ToggleClipPlane", 0))


def toggle_turntable(tt=None):
    """The View toolbar's Turntable (BumpMesh's, turntable.py): spin the view
    about the part's vertical axis, or stop it. True when it spins."""
    from . import turntable

    tt = tt or turntable.shared()
    if tt.spinning:
        tt.stop()
        return False
    return tt.start()


def _clipping_dock(mw):
    """FreeCAD's Clipping dock (Std_ToggleClipPlane's), or None. Found by its
    object name, which FreeCAD's dock manager sets and never translates."""
    from .qt import QtWidgets

    for d in mw.findChildren(QtWidgets.QDockWidget):
        if d.objectName() == "Clipping":
            return d
    return None


def _later(fn):
    from .qt import QtCore

    QtCore.QTimer.singleShot(0, fn)


def toggle_section(mw, run, find=None, later=None):
    """Section View as a toggle: close FreeCAD's Clipping dock when it is
    open, else open it with run(). FreeCAD adds that dock to the left area
    stacked under the Combo View, which on a short screen grew the main
    window (2082 -> 2256 px on the Mac): it is tabbed with the first other
    visible dock of that area instead (where FreeCAD puts it, no new
    height), and the window keeps its size. True when it opened."""
    find = find or _clipping_dock
    later = later or _later
    dock = find(mw)
    if dock is not None and dock.isVisible():
        w = dock.widget()
        # Its own Close: turns the clipping planes off and removes the dock.
        (getattr(w, "reject", None) or dock.close)()
        return False
    size = mw.size()
    whole = mw.isMaximized() or mw.isFullScreen()
    run()
    dock = find(mw)
    if dock is not None and not dock.isFloating():
        area = mw.dockWidgetArea(dock)
        mates = [d for d in mw.findChildren(type(dock)) if d is not dock and d.isVisible() and not d.isFloating()
                 and mw.dockWidgetArea(d) == area and not d.objectName().startswith("Texturizer")]
        if mates:
            mw.tabifyDockWidget(mates[0], dock)
            dock.show()
            dock.raise_()

    def keep():
        if not whole and mw.size() != size:
            mw.resize(size)

    keep()
    later(keep)  # the dock's layout settles after this event
    return True


def commands():
    """{name: command object}: every Texturizer command."""
    out = {TEXTURE: _Texture()}
    for name, key in HEADER:
        out[name] = _Press(name, key)
    out[ADD] = _Press(ADD, "addToDocument")
    for name, label, tip in VIEW:
        out[name] = _View(name, label, tip)
    out.update({PREPARE: _Prepare(), CHECK: _Check(), PANEL: _Panel()})
    return out


def register():
    import FreeCADGui

    for name, cmd in commands().items():
        FreeCADGui.addCommand(name, cmd)
    return NAMES
