# SPDX-License-Identifier: AGPL-3.0-only
# The Texturizer workbench, laid out as BumpMesh by Stefan Hermann (CNC
# Kitchen), and its About command; InitGui.py calls add(). It is built here,
# in a real module, because FreeCAD runs InitGui.py with exec() and a
# separate locals dict: a class body or a method body there sees only
# FreeCAD's own globals, never the names InitGui.py defines.

import os

from . import CREDIT, about_html

ICONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")


class About:
    def GetResources(self):
        return {"Pixmap": "Texturizer_Workbench", "MenuText": "About Texturizer",
                "ToolTip": "Show Texturizer's version, licence and credit."}

    def IsActive(self):
        return True

    def Activated(self):
        show_about()


def show_about(parent=None):
    """The About box (name, version, licence, the BumpMesh credit with its
    link), on FreeCAD's main window so it comes up in front of it."""
    from .qt import QtCore, QtGui, QtWidgets

    if parent is None:
        try:
            import FreeCADGui

            parent = FreeCADGui.getMainWindow()
        except Exception:  # noqa: BLE001
            parent = None
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle("About Texturizer")
    box.setTextFormat(QtCore.Qt.RichText)
    box.setText(about_html())
    box.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
    pm = QtGui.QPixmap(os.path.join(ICONS, "Texturizer_Workbench.svg"))
    if not pm.isNull():
        box.setIconPixmap(pm.scaled(64, 64, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
    box.setStandardButtons(QtWidgets.QMessageBox.Ok)
    (box.exec_ if hasattr(box, "exec_") else box.exec)()
    return box


# On macOS Qt moves a menu item whose text starts with "About" or
# "Settings" into the application menu (its text heuristic), where FreeCAD's
# own About and Preferences already sit: About Texturizer then showed
# nothing, and Settings Sidebar could take Preferences' place. Our actions
# keep their place in the Texturizer menu.
ROLE_PLAIN = ("Texturizer_About", "Texturizer_Texture")


def plain_menu_roles(actions, no_role, names=ROLE_PLAIN):
    """Set no_role (QAction.NoRole) on the actions named in names (FreeCAD
    names a command's action after the command); how many."""
    n = 0
    for a in actions:
        if a.objectName() in names:
            a.setMenuRole(no_role)
            n += 1
    return n


def _fix_menu_roles(Gui):
    from .qt import QtGui, QtWidgets

    QAction = getattr(QtGui, "QAction", None) or QtWidgets.QAction
    return plain_menu_roles(Gui.getMainWindow().findChildren(QAction), QAction.NoRole)


def make_class(Gui):
    """TexturizerWorkbench on this FreeCADGui's Workbench base."""

    class TexturizerWorkbench(Gui.Workbench):
        MenuText = "Texturizer"
        ToolTip = "Surface textures for printed parts. " + CREDIT
        Icon = os.path.join(ICONS, "Texturizer_Workbench.svg")

        def Initialize(self):
            from . import commands

            commands.register()
            Gui.addCommand("Texturizer_About", About())
            self.appendToolbar("Texturizer", commands.TOOLBAR)
            self.appendToolbar("View", commands.VIEW_TOOLBAR)
            self.appendToolbar("Check", commands.CHECK_TOOLBAR)
            self.appendMenu("Texturizer", [commands.TEXTURE, "Separator"] + commands.TOOLBAR + [commands.ADD, "Separator"]
                            + commands.VIEW_TOOLBAR + ["Separator"] + commands.CHECK_TOOLBAR
                            + [commands.PANEL, "Separator", "Texturizer_About"])

        def Activated(self):
            try:
                _fix_menu_roles(Gui)
            except Exception:  # noqa: BLE001 — no Qt main window (tests)
                pass
            # As BumpMesh opens on its sidebar: the sidebar and the gallery, with
            # the selected body loaded (hosted or not; the Texture tab stays in
            # Agent Link's panel).
            try:
                from . import commands

                commands.open_sidebar(load=True)
            except Exception as e:  # noqa: BLE001 — never stop the bench
                import FreeCAD

                FreeCAD.Console.PrintWarning(f"Texturizer: sidebar not shown ({e})\n")

        def GetClassName(self):
            return "Gui::PythonWorkbench"

    return TexturizerWorkbench


def add(Gui=None):
    """The icons and the workbench: what InitGui.py calls."""
    if Gui is None:
        import FreeCADGui as Gui
    Gui.addIconPath(ICONS)
    cls = make_class(Gui)
    Gui.addWorkbench(cls())
    return cls
