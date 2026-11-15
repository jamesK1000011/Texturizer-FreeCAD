# SPDX-License-Identifier: AGPL-3.0-only
# The Texturizer workbench, laid out as BumpMesh by Stefan Hermann (CNC
# Kitchen): the Settings sidebar on the right with the Texture Gallery beside
# it, BumpMesh's header row as the "Texturizer" toolbar, a "View" toolbar of
# FreeCAD's own view commands under BumpMesh's labels, and "Check" (Prepare
# for texture, Check textured mesh). The texturing is BumpMesh's engine,
# ported to Python (texturizer.engine). Needs no other add-on and no agent;
# never imports Agent Link.
# The workbench is built in texturizer/workbench.py; FreeCAD runs this file
# at start.

import FreeCADGui

from texturizer import workbench as _texturizer_workbench

_texturizer_workbench.add(FreeCADGui)
