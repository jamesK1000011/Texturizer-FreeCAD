# SPDX-License-Identifier: AGPL-3.0-only
# The Texturizer workbench, laid out as BumpMesh by Stefan Hermann (CNC
# Kitchen), and its About command; InitGui.py calls add(). It is built here,
# in a real module, because FreeCAD runs InitGui.py with exec() and a
# separate locals dict: a class body or a method body there sees only
# FreeCAD's own globals, never the names InitGui.py defines.

import os

from . import CREDIT, about

ICONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")


class About:
    def GetResources(self):
        return {"Pixmap": "Texturizer_Workbench", "MenuText": "About Texturizer",
                "ToolTip": "Texturizer's version, licence and credit. " + CREDIT}

    def IsActive(self):
        return True

    def Activated(self):
        from PySide import QtGui

        try:
            from PySide import QtWidgets
        except ImportError:
            QtWidgets = QtGui
        QtWidgets.QMessageBox.about(None, "About Texturizer", about())


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
