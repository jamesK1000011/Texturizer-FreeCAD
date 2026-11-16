<!-- SPDX-License-Identifier: AGPL-3.0-only -->
# Texturizer for FreeCAD

> Not affiliated with or endorsed by the FreeCAD project, the FreeCAD Project
> Association (FPA) or CNC Kitchen. FreeCAD is the FPA's trademark. Get FreeCAD
> itself from [freecad.org](https://freecad.org).

Texturizer puts a surface texture on a part you designed in FreeCAD: knurling
on a grip, wood grain on a box, a weave on a lid. It is **BumpMesh by Stefan
Hermann (CNC Kitchen)**, ported into FreeCAD. BumpMesh is the free web tool at
[github.com/CNCKitchen/stlTexturizer](https://github.com/CNCKitchen/stlTexturizer)
that textures STL files for 3D printing. Texturizer runs the same engine, with
the same settings and the same texture library, directly on your FreeCAD
part. You get a textured mesh beside your part, ready to print as STL or 3MF.
Your part itself is never changed.

<!-- screenshot: a FreeCAD part with a knurled grip, the Texturizer sidebar open on the right -->

## Who it is for

- Anyone who 3D prints FreeCAD parts and wants a texture on them without
  leaving FreeCAD for another tool.
- People who already know BumpMesh: every control is where BumpMesh has it.
- People who let an AI agent work in FreeCAD through
  [Agent Link for FreeCAD](https://github.com/jamesK1000011/AgentLink-FreeCAD).
  The agent can pick a texture, mask faces and make the mesh for you.

You do not need to know Python, mesh editing or BumpMesh. You do not need an
agent.

## Install

You need FreeCAD 1.0 or newer. Texturizer also needs numpy, which comes with
FreeCAD.

**With the Addon Manager (custom repository).**

1. Open **Edit ▸ Preferences ▸ Addon Manager**.
2. Under **Custom repositories**, add
   `https://github.com/jamesK1000011/Texturizer-FreeCAD` with branch `main`.
3. Open **Tools ▸ Addon manager**, find **Texturizer**, and click **Install**.
4. Restart FreeCAD.

**With the Agent Link extension.** If you use Agent Link for FreeCAD from
Claude Desktop, ask the agent to add Texturizer as an extension. Agent Link
asks you to type `install jamesK1000011/Texturizer-FreeCAD` yourself, since an
extension is code FreeCAD runs. It then installs Texturizer beside Agent Link
in FreeCAD's `Mod` folder. Restart FreeCAD when it says so.

**By hand.** Put this folder in FreeCAD's `Mod` folder as `Texturizer` and
restart FreeCAD.

## What you see in FreeCAD

**The Texturizer workbench.** Pick **Texturizer** from the workbench list.
Entering it opens the sidebar with the part you selected loaded.

<!-- screenshot: the workbench list with Texturizer picked -->

**BumpMesh's sidebar**, docked on the right. It has BumpMesh's sections, in
BumpMesh's order, with its labels, ranges and defaults:

- **Use Selected Part** (the selected solid; else this document's last
  textured part, else the one solid shown; import an STL with File ▸ Import
  first), **Place on Face** and **Rotate**.
- **Displacement Map**: the layer strip (up to 4 layers), favourites, the
  Texture Gallery and Upload custom map.
- **Projection**, **Texture Depth** and **Transform**: how the texture wraps,
  how deep it goes, and its size and angle.
- **Masking**: keep faces plain or texture only some, by angle or by picking
  FreeCAD faces.
- **Advanced / Beta Features**, including **Bake Textures**. A bake makes the
  textured mesh your new part, so you can add another texture on top. With
  **Mask just-baked faces** on (the default), the faces you just textured
  stay as they are on the next layer.
- **Export**: Suggest values, Resolution, Output Triangles, **Add to
  document** / **Update**, and **Export 3MF**. For an STL, export the
  Textured object with FreeCAD's File ▸ Export.

<!-- screenshot: the Texturizer sidebar, Displacement Map and Masking sections open -->

**The Texture Gallery**, a dock beside the sidebar. Search, categories,
favourites, your own heightmaps, and a credit badge on each tile (HP, CC0,
CNC Kitchen, Yours).

<!-- screenshot: the Texture Gallery with the Grip category open -->

**The toolbars.**

- **Texturizer** is BumpMesh's header row: Reset to Defaults, Export
  BumpMesh Project…, Import BumpMesh Project…, Texture Gallery, Export 3MF.
  The texture draft is saved with the FreeCAD document, and FreeCAD's own
  Edit ▸ Undo / Redo undo texture edits; the BumpMesh project files
  (`.bumpmesh`) are for sharing with the BumpMesh web app.
- **View** has Section View (FreeCAD's clipping plane). For wireframe and
  perspective, use FreeCAD's own Draw Style and camera commands.
- **Check** has Prepare for Texture and Check Textured Mesh.

**Quick look and the preview.** While you change settings, **Quick look**
shows the texture on the part right away (a shader, no mesh is built). Turn on
**3D Preview** for the real thing: BumpMesh's engine builds the displaced
mesh, so what you see is what prints (Draft 500 k triangles, Fine 1.5 M).
Where the shader can't run, it falls back to the engine preview.

<!-- screenshot: Quick look on a part, then the same part with 3D Preview on -->

**Textured objects.** Add to Document puts a Textured mesh beside your part.
It remembers the settings it was made with. When you change the part, it is
marked out of date; click **Update** to texture it again. It never reruns on
its own.

Panels take their colours from your FreeCAD theme. Texturizer never sets a
stylesheet on FreeCAD itself.

## What an agent can do with it

When Agent Link for FreeCAD is installed, an agent gets these tools. They
work on the same draft the sidebar shows, so you watch the sliders move as
the agent works. The list is generated from `texturizer/data/tools.json` by
`tools/gen_readme_tools.py`.

<!-- tools:begin (generated by tools/gen_readme_tools.py) -->
| Tool | Group | What it does |
|---|---|---|
| `texture_list` | texture | BumpMesh's texture library as Texturizer ships it: id, name, category, credit and licence, and defaults. |
| `texture_settings` | texture | BumpMesh's settings: names, defaults, units, mapping modes 0-8, layer and run keys. |
| `texture_draft` | texture | Start or read the texture draft, the one the Texturizer sidebar shows: part, draft, reset, project. |
| `texture_set` | texture | Change the draft: texture, settings by BumpMesh's names for the layer and the run, frame, cylinder, stamp, active. |
| `texture_mask` | texture | BumpMesh's Masking, By Surface, for one layer: mode exclude or include, add or remove faces, add_by, fill, triangles, paint, clear. |
| `texture_layer` | texture | BumpMesh's layer strip: add, remove, move, solo, visible, blend, name, active. |
| `texture_preview` | texture | Build the CPU preview of the draft with BumpMesh's engine and wait for it: shown in the 3D view, answered with the preview edge, triangles, time, and … |
| `texture_apply` | texture | Add to document / Update / Bake Textures with BumpMesh's engine, ported to Python and run inside FreeCAD; the body is never changed. |
| `texture_export` | texture | Write a file: format stl or 3mf, or bumpmesh. |
| `texture_prepare` | texture | A body made ready for a surface texture by BumpMesh: an STL written face by face from one mesh of the whole part, and a mask file of the triangles of … |
| `texture_check` | texture | Check a textured mesh against the body it was made from, face by face: on each face the texture was for, the share of points moved and how deep … |
<!-- tools:end -->

The texturing in every one of them is BumpMesh's engine, ported to Python.

## Alone or with Agent Link

| | Alone | With Agent Link |
|---|---|---|
| Workbench, sidebar, Texture Gallery, toolbars | Yes | Yes |
| Quick look, 3D Preview, Add to Document, Update, Bake | Yes | Yes |
| Export STL / 3MF, Save / Load Project | Yes | Yes |
| Sidebar clicks | Run by Texturizer itself | Run through Agent Link, shown and logged in its Activity |
| Texture tab in Agent Link's panel (last results, open the sidebar) | No | Yes |
| Agent tools above | No | Yes |

Texturizer never needs Agent Link. Only `texturizer/agentlink_families.py`
and the `fam_*` module it loads import Agent Link, and only Agent Link loads
them. Agent Link carries no cost for Texturizer when Texturizer is not
installed.

## Uninstall

- **Addon Manager**: Tools ▸ Addon manager ▸ Texturizer ▸ Uninstall, then
  restart FreeCAD.
- **By hand, or installed through Agent Link**: delete the `Texturizer`
  folder from FreeCAD's `Mod` folder, and restart FreeCAD.
- Exports and `.bumpmesh` projects are yours to keep: they go where you
  save them (or to `~/Texturizer` when an agent gives no folder). Export a
  Textured object as STL or 3MF first if you want it without Texturizer.
- The preview keeps a few refined copies of recently previewed parts in
  `Texturizer/cache` inside FreeCAD's user data folder (the newest 8, so a
  part opens fast after a restart); delete that folder to clear them.

## Privacy

Texturizer has no telemetry and makes no network connection: the texture
library ships with it, and images you add are read from your own disk. Your
part, its textures and exports stay on your computer (the preview's cache
of refined parts too, in FreeCAD's user data folder). With Agent Link, what
the agent reads goes to the AI provider your MCP client uses, under that
provider's terms.

## Texture licences

The texture library is BumpMesh's, as `texturizer/textures/LICENSES.md` lists
it, file by file:

- **Hero Patterns** by Steve Schoger, CC BY 4.0. Exports that use one carry
  its credit line.
- **ambientCG** and **Poly Haven** maps, CC0 1.0 (public domain).
- **BumpMesh's own** textures by CNC Kitchen, AGPL-3.0.

BumpMesh's Filter Forge textures are left out: their licence does not allow
them to be shipped here. The licence texts are in `LICENSES/`.

## For developers

```
AGENTLINK_HOME=$(mktemp -d) python3 -m unittest discover -s tests
python3 tools/gen_readme_tools.py           # refresh the tool list above
python3 tools/gen_readme_tools.py --check   # exit 1 if it is out of date
```

`texturizer.engine` is the ported engine and imports neither FreeCAD nor
Agent Link. On the reference cases in `tests/reference/` its output is
bit-identical to BumpMesh's own engine (`tests/test_engine_parity.py`).

```python
from texturizer import engine
engine.library.list()                        # id, name, category, credit, licence, defaults
r = engine.texture(positions, {"texture": "Knurling", "scaleU": 5, "textureHeight": 0.5,
                               "refineLength": 0.4}, mask=engine.by_normal(positions, (0, 0, -1), 10))
engine.export(positions, settings, mask, "part.3mf")
```

## Licence

Code: AGPL-3.0-only (`LICENSE`), to match BumpMesh. `texturizer/meshdist.py`
is a copy of Agent Link's and keeps its LGPL-2.1-or-later. Data files
(`texturizer/data/*.json`): CC BY 4.0 (`LICENSE-DATA`). Textures: as above.
Each file's licence is stated in its header or in `REUSE.toml`; the texts are
in `LICENSES/`.

## Credits

- The texturing engine, the settings sidebar's layout, the texture library
  and the `.bumpmesh` format are **BumpMesh by Stefan Hermann (CNC Kitchen)**,
  [github.com/CNCKitchen/stlTexturizer](https://github.com/CNCKitchen/stlTexturizer),
  AGPL-3.0. They are ported to Python file by file; each module names the
  BumpMesh file it ports. Laying the workbench out as BumpMesh is part of
  that credit.
- Textures based on Hero Patterns by Steve Schoger (CC BY 4.0); maps from
  ambientCG and Poly Haven (CC0).
- Icons are new drawings in FreeCAD's icon style. Their designs follow glyphs
  from [Tabler Icons](https://tabler.io/icons) (MIT) and
  [Pictogrammers Material Design Icons](https://pictogrammers.com)
  (Apache-2.0). See `texturizer/icons/CREDITS.md`. FreeCAD's own icons are
  used by name from FreeCAD.
