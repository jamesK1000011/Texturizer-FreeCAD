# SPDX-License-Identifier: AGPL-3.0-only
# Texturizer's FreeCAD commands, no agent needed: Prepare for texture (the
# selected faces written as an STL and a mask), Check textured mesh (the
# textured STL measured against its body) and the Texture panel. Each runs
# the same handler the agent's tool does, through the panel's runner. The
# texturing is BumpMesh's engine (Stefan Hermann, CNC Kitchen) in
# texturizer.engine, run in FreeCAD.
import os

from . import CREDIT, handlers, panel

PREPARE, CHECK, PANEL = "Texturizer_Prepare", "Texturizer_Check", "Texturizer_Panel"
NAMES = [PREPARE, CHECK, PANEL]


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
        self.form.setWindowTitle("Prepare for texture")
        lay = QtWidgets.QFormLayout(self.form)
        lay.addRow("Body", QtWidgets.QLabel(body))
        lay.addRow("Faces", QtWidgets.QLabel(", ".join(faces)))
        self.mode = QtWidgets.QComboBox()
        self.mode.addItem("Texture only these faces", "include")
        self.mode.addItem("Texture all but these faces", "exclude")
        lay.addRow("Mode", self.mode)
        self.tol = QtWidgets.QDoubleSpinBox()
        self.tol.setDecimals(3)
        self.tol.setRange(0.001, 1.0)
        self.tol.setSingleStep(0.005)
        self.tol.setValue(0.02)
        self.tol.setSuffix(" mm")
        lay.addRow("Tolerance", self.tol)
        out = handlers.Context().out_dir
        self.path = QtWidgets.QLineEdit(os.path.join(out, body.replace(" ", "_") + "-for-texture.stl"))
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
        if _say("Prepare for texture", ok, out):
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
        self.form.setWindowTitle("Check textured mesh")
        lay = QtWidgets.QFormLayout(self.form)
        lay.addRow("Body", QtWidgets.QLabel(body or "the one body shown"))
        self.path = QtWidgets.QLineEdit(default_check_path())
        lay.addRow("Textured STL", _path_row(QtWidgets, self.path, False, "Textured STL"))
        self.reach = QtWidgets.QDoubleSpinBox()
        self.reach.setRange(0, 100)
        self.reach.setDecimals(2)
        self.reach.setSpecialValueText("from the part's size")
        self.reach.setSuffix(" mm")
        lay.addRow("Reach", self.reach)
        self.flat = QtWidgets.QDoubleSpinBox()
        self.flat.setRange(0.001, 1)
        self.flat.setDecimals(3)
        self.flat.setValue(0.01)
        self.flat.setSuffix(" mm")
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
        if _say("Check textured mesh", ok, out):
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
        _qt().QMessageBox.information(None, "Prepare for texture", "Select the faces to texture (or to keep plain) on one body first, then Prepare for texture.")
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
        return {"Pixmap": "Texturizer_Prepare", "MenuText": "Prepare for texture",
                "ToolTip": "Write the body as an STL for BumpMesh with a mask of the selected faces (texture only them, or all but them). " + CREDIT}

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        show_prepare()


class _Check:
    def GetResources(self):
        return {"Pixmap": "Texturizer_Check", "MenuText": "Check textured mesh",
                "ToolTip": "Measure a textured STL against its body face by face: depth on the textured faces, any face it leaked onto, a closed mesh. " + CREDIT}

    def IsActive(self):
        import FreeCAD

        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        show_check()


class _Panel:
    def GetResources(self):
        return {"Pixmap": "Texturizer_Workbench", "MenuText": "Texture panel",
                "ToolTip": "The Texture panel: last prepared files and the last check. " + CREDIT}

    def IsActive(self):
        return True

    def Activated(self):
        panel.show_dock()


def register():
    import FreeCADGui

    for name, cmd in ((PREPARE, _Prepare()), (CHECK, _Check()), (PANEL, _Panel())):
        FreeCADGui.addCommand(name, cmd)
    return NAMES
