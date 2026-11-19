<!-- SPDX-License-Identifier: AGPL-3.0-only -->
# Acknowledgement

Texturizer for FreeCAD is a port of **BumpMesh by CNC Kitchen** (Stefan
Hermann), the browser tool that puts surface textures on 3D-print meshes:
https://github.com/CNCKitchen/stlTexturizer (https://bumpmesh.com). Its
texturing engine, its mapping modes, its layers and masks, its settings
sidebar and Texture Gallery, its texture library and its `.bumpmesh`
project format were designed and written there. Here they are translated
from JavaScript to Python on top of FreeCAD. Each ported module names the
BumpMesh file it comes from. BumpMesh's own labels and tooltips stay word
for word. Credit for this work goes to Stefan Hermann (CNC Kitchen), and
the workbench says so in words where BumpMesh's work loads.

BumpMesh is licensed under the GNU Affero General Public License, version
3, and this port is licensed under AGPL-3.0-only: see LICENSE. Changes made
in porting are this port's own, and they are under the same licence.
`texturizer/meshdist.py` is a copy of Agent Link for FreeCAD's and keeps
its LGPL-2.1-or-later.

The textures keep their own licences, file by file, as
`texturizer/textures/LICENSES.md` lists them: Hero Patterns by Steve
Schoger (CC BY 4.0), ambientCG and Poly Haven maps (CC0 1.0), and
BumpMesh's own textures by CNC Kitchen (AGPL-3.0-only). BumpMesh's Filter
Forge textures are not included: their licence does not allow it. The
icons are new drawings; `texturizer/icons/CREDITS.md` names the glyphs they
follow. The licence texts are in `LICENSES/`.

The BumpMesh and CNC Kitchen names are used here only to say where the work
comes from. This port is not made, endorsed or supported by CNC Kitchen, by
the FreeCAD project or by the FreeCAD Project Association (FPA). Please
report its problems here, not to BumpMesh.
