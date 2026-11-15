# SPDX-License-Identifier: AGPL-3.0-only
# The Texturizer workbench, laid out as BumpMesh by Stefan Hermann (CNC
# Kitchen): the Settings sidebar on the right with the Texture Gallery beside
# it, BumpMesh's header row as the "Texturizer" toolbar, a "View" toolbar of
# FreeCAD's own view commands under BumpMesh's labels, and "Check" (Prepare
# for texture, Check textured mesh). The texturing is BumpMesh's engine,
# ported to Python (texturizer.engine). Needs no other add-on and no agent;
# never imports Agent Link.

import os

import FreeCADGui
import texturizer

_ICONS = os.path.join(os.path.dirname(texturizer.__file__), "icons")
FreeCADGui.addIconPath(_ICONS)


class _About:
    def GetResources(self):
        return {"Pixmap": "Texturizer_Workbench", "MenuText": "About Texturizer",
                "ToolTip": "Texturizer's version, licence and credit. " + texturizer.CREDIT}

    def IsActive(self):
        return True

    def Activated(self):
        from PySide import QtGui

        try:
            from PySide import QtWidgets
        except ImportError:
            QtWidgets = QtGui
        QtWidgets.QMessageBox.about(None, "About Texturizer", texturizer.about())


class TexturizerWorkbench(FreeCADGui.Workbench):
    MenuText = "Texturizer"
    ToolTip = "Surface textures for printed parts. " + texturizer.CREDIT
    Icon = os.path.join(_ICONS, "Texturizer_Workbench.svg")

    def Initialize(self):
        from texturizer import commands

        commands.register()
        FreeCADGui.addCommand("Texturizer_About", _About())
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
            from texturizer import commands

            commands.open_sidebar(load=True)
        except Exception as e:  # noqa: BLE001 — never stop the bench
            import FreeCAD

            FreeCAD.Console.PrintWarning(f"Texturizer: sidebar not shown ({e})\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(TexturizerWorkbench())
