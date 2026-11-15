# Texturizer for FreeCAD

Surface textures for printed parts in FreeCAD, **based on BumpMesh by Stefan
Hermann (CNC Kitchen)** — [CNCKitchen/stlTexturizer](https://github.com/CNCKitchen/stlTexturizer),
AGPL-3.0. This add-on is licensed AGPL-3.0 to match.

Not affiliated with or endorsed by the FreeCAD project, the FreeCAD Project
Association, or CNC Kitchen.

## What it does

**BumpMesh's engine runs inside FreeCAD.** `texturizer.engine` is a Python and
numpy port of BumpMesh's texturing engine (`engine/` and the `js/` modules it
uses: subdivision, regularize, mapping, outline wrap, displacement, decimation,
mesh repair, exclusion, soft masks, the export pipeline, texture analysis,
smart resolution, the subdivision estimate and the STL / 3MF writers). No
browser, Node or server: a body goes in, a printable textured mesh comes out,
with BumpMesh's setting names, its mapping modes 0–8 (planar XY / XZ / YZ,
cylindrical, spherical, triplanar, cubic, surface normal, outline wrap), up to
4 layers with masks, and its texture library. On the reference cases in
`tests/reference/` the port's output is bit-identical to BumpMesh's own
engine (same triangles, same float32 coordinates); see
`tests/test_engine_parity.py`.

- **Texture a part:** a body, a texture (or up to 4 layers) and the faces to
  texture or keep plain; the result is a new Mesh object beside the body and,
  if asked, an STL or 3MF. The body itself is not changed.
- **Prepare a part for texturing elsewhere:** the part written as one closed
  STL, face by face, and a mask of exactly the faces to texture.
- **Check a textured result** against the part, face by face: how deep the
  texture stands on each textured face, any face it leaked onto, faces meant
  for texture that barely moved, and whether the mesh is still closed.

## In FreeCAD

The **Texturizer** workbench has, on its toolbar and menu:

- **Prepare for texture** — select the faces on a body, then set include or
  exclude, the tolerance and where the STL goes (a task panel); the mask is
  written beside it.
- **Check textured mesh** — the textured STL, reach and flatness (a task
  panel); the result goes to the Texture panel.
- **Texture panel** — the last prepared files and the last check.

BumpMesh's own Settings sidebar and Texture Gallery, rebuilt in FreeCAD with
its labels and layout, are the next step.

With Agent Link installed, the Texture panel is a tab in Agent Link's panel
and its buttons run through Agent Link (shown and logged); alone, it is a
dock of its own beside the model tree. Its accent colours follow FreeCAD's
theme; it never sets a stylesheet on FreeCAD itself.

## Installs alone

Texturizer needs FreeCAD (1.0 or newer) and numpy, and no other add-on.
`texturizer.engine` imports neither FreeCAD nor Agent Link (PNG heightmaps
are read with zlib; Pillow, if present, reads other formats):

```python
from texturizer import engine
engine.library.list()                        # id, name, category, credit, licence, defaults
r = engine.texture(positions, {"texture": "Knurling", "scaleU": 5, "textureHeight": 0.5,
                               "refineLength": 0.4}, mask=engine.by_normal(positions, (0, 0, -1), 10))
engine.export(positions, settings, mask, "part.3mf")
```

`texturizer.mesh` is the FreeCAD side (a body as one mesh, face by face).

## For agents

When [Agent Link for FreeCAD](https://github.com/jamesK1000011/AgentLink-FreeCAD)
is installed beside it, agents get these tools (defined in
`texturizer/data/tools.json`):

| Tool | What it does |
|---|---|
| `texture_list` | The texture library: id, name, category, credit, licence, default tile size. |
| `texture_settings` | BumpMesh's settings, units and mapping modes; for a body, the predicted triangles and memory and BumpMesh's suggested resolution. |
| `texture_apply` | BumpMesh's engine on a body: a new textured Mesh object (and STL / 3MF), one texture or up to 4 layers with face masks. |
| `texture_prepare` | A body as an STL plus a mask of the faces to texture. |
| `texture_check` | A textured STL checked against the body face by face; verdict `ok` or `check`. |

## Credits

The texturing engine is **BumpMesh by Stefan Hermann (CNC Kitchen)**,
[github.com/CNCKitchen/stlTexturizer](https://github.com/CNCKitchen/stlTexturizer),
AGPL-3.0, ported to Python file by file; each module names the BumpMesh file
it ports. The texture library is BumpMesh's (`texturizer/textures/LICENSES.md`):
textures based on Hero Patterns by Steve Schoger (CC BY 4.0), maps from
ambientCG and Poly Haven (CC0), and BumpMesh's own (AGPL-3.0). BumpMesh's
Filter Forge textures are not included.

## Licences

Code: AGPL-3.0-only (`LICENSE`); `texturizer/meshdist.py` is a copy of Agent
Link's, kept under its LGPL-2.1-or-later. Data files: CC BY 4.0
(`LICENSE-DATA`). Textures: as `texturizer/textures/LICENSES.md` lists them
(CC BY 4.0, CC0 1.0, AGPL-3.0; texts in `LICENSES/`).

## Icons

Icons adapted from Tabler Icons (MIT, tabler.io) and Pictogrammers Material Design Icons (Apache-2.0, pictogrammers.com). FreeCAD's own icons are used by name from FreeCAD. The textures and the texturing come from BumpMesh by Stefan Hermann (CNC Kitchen); the icons are new drawings. See `texturizer/icons/CREDITS.md`.
