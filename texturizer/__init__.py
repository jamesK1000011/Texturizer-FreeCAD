# SPDX-License-Identifier: AGPL-3.0-only
"""Texturizer for FreeCAD: surface textures for printed parts, based on
BumpMesh by Stefan Hermann (CNC Kitchen), github.com/CNCKitchen/stlTexturizer.

- mesh: a body's faces as one closed mesh, face by face; STL read and
  written; the mask of faces to texture; a textured mesh checked against the
  body it came from, face by face.

Plain Python on FreeCAD and numpy; it needs no other add-on and imports no
agent. Tools for agents come
through Agent Link for FreeCAD when it is installed (agentlink_families).
"""

VERSION = "0.1.0"
CREDIT = "Based on BumpMesh by Stefan Hermann (CNC Kitchen), github.com/CNCKitchen/stlTexturizer (AGPL-3.0)"
