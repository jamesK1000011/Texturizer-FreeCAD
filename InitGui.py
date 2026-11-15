# SPDX-License-Identifier: AGPL-3.0-only
# The Texturizer workbench: Prepare for texture, Check textured mesh and the
# Texture panel; the texturing is BumpMesh's engine by Stefan Hermann (CNC
# Kitchen), ported to Python (texturizer.engine). Needs no other add-on and no
# agent; never imports Agent Link.

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

        names = commands.register()
        FreeCADGui.addCommand("Texturizer_About", _About())
        self.appendToolbar("Texturizer", names)
        self.appendMenu("Texturizer", names + ["Separator", "Texturizer_About"])

    def Activated(self):
        if not getattr(texturizer, "_hosted", False):
            try:
                from texturizer import panel

                panel.show_dock()
            except Exception as e:  # noqa: BLE001 — never stop the bench
                import FreeCAD

                FreeCAD.Console.PrintWarning(f"Texturizer: panel not shown ({e})\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(TexturizerWorkbench())
