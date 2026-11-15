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

The **Texturizer** workbench is laid out as BumpMesh, so someone who knows
BumpMesh finds every control where it was; that familiarity is part of the
credit to Stefan Hermann (CNC Kitchen).

- **Settings sidebar** (right, as BumpMesh's): BumpMesh's sections, headings,
  labels, ranges and defaults in its order — Load Model…, Place on Face and
  Rotate; Displacement Map (layer strip, favourites, Texture Gallery, Upload
  custom map); Projection; Texture Depth (3D Preview, Quality Draft / Fine);
  Transform; Masking (By Angle, By Surface with B-rep faces, Add by…, Fill,
  Smooth Mask); Advanced / Beta Features; Export (Suggest values, Resolution,
  Output Triangles, Add to document / Update, Export STL / 3MF).
- **Texture Gallery** dock beside it: search, categories, favourites, your
  own maps, and a credit badge on each tile (HP, CC0, CNC Kitchen, Yours).
- **Live preview** in the 3D view: the shader Quick look while you change
  things (3D Preview off, BumpMesh's default), and BumpMesh's engine's
  displaced mesh with 3D Preview on (500 k triangles Draft, 1.5 M Fine). It
  falls back to the CPU preview where the shader can't run.
- **Toolbars**: "Texturizer" is BumpMesh's header row (Undo, Redo, Reset to
  defaults, Save Project, Load Project, Texture Gallery, Export STL, Export
  3MF); "View" is FreeCAD's own Wireframe, Perspective View and Section View
  under BumpMesh's labels; "Check" has Prepare for texture and Check
  textured mesh. Save / Load Project read and write BumpMesh's own
  `.bumpmesh` files.
- **Textured objects** keep the settings they were made with. When the body
  changes they are marked out of date (never rerun on their own); Update
  textures them again, and Load Model… on one brings its settings back.

Entering the workbench opens the sidebar with the selected body loaded. With
Agent Link installed, sidebar clicks run through Agent Link (shown and
logged in Activity), and a small Texture tab in Agent Link's panel shows the
last results and opens the sidebar; alone, Texturizer runs them itself. Its
accent colours follow FreeCAD's theme; it never sets a stylesheet on FreeCAD
itself.

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
| `texture_draft` | Start or read the draft the sidebar shows: the part (a Textured object brings back its settings), a whole draft, Reset to defaults, Load Project. |
| `texture_set` | Texture, settings by BumpMesh's names, Rotate / Place on Face, cylinder Auto-fit / Reset / From face, stamp; the sidebar moves live. |
| `texture_mask` | By Surface: include / exclude, faces added or removed, Add by…, Fill, Clear All, per layer. |
| `texture_layer` | The layer strip: add, remove, move, solo, visible, Cover / Add, name, active (up to 4). |
| `texture_preview` | The engine's preview of the draft, shown in the 3D view: edge, triangles, time; Suggest values; Cancel. |
| `texture_apply` | Add to document / Update / Bake Textures: a Textured Mesh object beside the body, re-texturable; or one texture / up to 4 layers given directly. |
| `texture_export` | STL or 3MF (a Textured object, or the draft), or Save Project as `.bumpmesh`. |
| `texture_prepare` | A body as an STL plus a mask of the faces to texture. |
| `texture_check` | A textured STL checked against the body face by face; verdict `ok` or `check`. |

The texturing in every one of them is BumpMesh's engine, ported to Python
and credited to Stefan Hermann (CNC Kitchen).

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
