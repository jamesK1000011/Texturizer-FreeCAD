# Texturizer for FreeCAD

Surface textures for printed parts in FreeCAD, **based on BumpMesh by Stefan
Hermann (CNC Kitchen)** — [CNCKitchen/stlTexturizer](https://github.com/CNCKitchen/stlTexturizer),
AGPL-3.0. This add-on is licensed AGPL-3.0 to match.

Not affiliated with or endorsed by the FreeCAD project, the FreeCAD Project
Association, or CNC Kitchen.

## What it does today

- **Prepare a part for texturing:** the part written as one closed STL, face
  by face, and a mask of exactly the faces to texture (`include`: only them,
  a grip; `exclude`: all but them, a mating face kept plain).
- **Check the textured result** against the part, face by face: how deep the
  texture stands on each textured face, any face it leaked onto, faces meant
  for texture that barely moved, and whether the mesh is still closed.

The texturing itself runs in BumpMesh for now; bringing its engine into
FreeCAD is this add-on's next step.

## In FreeCAD

The **Texturizer** workbench has, on its toolbar and menu:

- **Prepare for texture** — select the faces on a body, then set include or
  exclude, the tolerance and where the STL goes (a task panel); the mask is
  written beside it.
- **Check textured mesh** — the textured STL, reach and flatness (a task
  panel); the result goes to the Texture panel.
- **Open BumpMesh** — bumpmesh.com in the browser, to texture the STL there.
  Nothing is uploaded from FreeCAD.
- **Texture panel** — the last prepared files and the last check.

With Agent Link installed, the Texture panel is a tab in Agent Link's panel
and its buttons run through Agent Link (shown and logged); alone, it is a
dock of its own beside the model tree. Its accent colours follow FreeCAD's
theme; it never sets a stylesheet on FreeCAD itself.

## Installs alone

Texturizer needs FreeCAD (1.0 or newer) and numpy, and no other add-on. Its
API is `texturizer.mesh` (plain FreeCAD and numpy).

## For agents

When [Agent Link for FreeCAD](https://github.com/jamesK1000011/AgentLink-FreeCAD)
is installed beside it, agents get two tools (defined in
`texturizer/data/tools.json`):

| Tool | What it does |
|---|---|
| `texture_prepare` | A body as an STL plus a mask of the faces to texture; answers the BumpMesh call to make. |
| `texture_check` | A textured STL checked against the body face by face; verdict `ok` or `check`. |

## Licences

Code: AGPL-3.0-only (`LICENSE`); `texturizer/meshdist.py` is a copy of Agent
Link's, kept under its LGPL-2.1-or-later. Data files: CC BY 4.0
(`LICENSE-DATA`).

## Icons

Icons adapted from Tabler Icons (MIT, tabler.io) and Pictogrammers Material Design Icons (Apache-2.0, pictogrammers.com). FreeCAD's own icons are used by name from FreeCAD. The textures and the texturing come from BumpMesh by Stefan Hermann (CNC Kitchen); the icons are new drawings. See `texturizer/icons/CREDITS.md`.
