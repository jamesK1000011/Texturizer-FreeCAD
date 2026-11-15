# Changelog

All notable changes to Texturizer for FreeCAD. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

Texturizer for FreeCAD is based on BumpMesh by Stefan Hermann (CNC Kitchen),
github.com/CNCKitchen/stlTexturizer, AGPL-3.0.

## [Unreleased]

### Added

- `texture_check { object }` checks a Textured object against its body with the mask it stores (draft or arguments), with no `texture_prepare` mask; `path` of its `texture_export` STL alone works too. Faces BumpMesh's angle limits cover whole are listed as `angleMasked`, not `bare`. The `-mask.json` route is unchanged.

- Paint brush, ported from BumpMesh: Single / Circle, Size, Hardness (soft coverage per vertex), Shift to erase, one undo step per stroke; Fill on meshes; `texture_mask { paint, fill }`. Painted masks round-trip `.bumpmesh` projects exactly. Precision mode comes later.

- Bake Textures: Mask just-baked faces, as BumpMesh does it. After a bake,
  the fresh layer on the baked part starts with the faces just textured
  excluded, so the next pass doesn't texture them twice. Turn it off with the
  "Mask just-baked faces from further texturing" box (`bakeMask`).
- Release preparation: a beginner's README with the agent tool list generated
  from `texturizer/data/tools.json` (`tools/gen_readme_tools.py`, with
  `--check`), complete Addon Manager metadata (bug tracker, readme and
  documentation links, tags, the numpy dependency), every file's licence
  stated in its header or in `REUSE.toml` with the texts in `LICENSES/`, and
  tests for all of it (`tests/test_release.py`).

### Changed

- Settings sidebar and Texture Gallery fit their dock: no horizontal scrollbar, texture tiles reflow to the width (at most BumpMesh's 4 in the sidebar) with even gaps, the layer name gives way before the row's buttons, and the dock's minimum width is the content's. The gallery's hint has its own row, Reset favorites is a button, the body no longer repeats the dock's title, and Turntable works (spins the 3D view while you browse). The view commands' tooltips say what they do.

- `texture_apply` with `layers`: a layer without its own `texture`, `faces` or `mode` takes the top-level one (they were ignored, so the whole body was textured); a layer that gives a different one, or a setting outside `settings`, is refused. Top-level layer settings are every layer's defaults.
- Bake Textures runs BumpMesh's bake pipeline: no decimation, so the baked
  mesh keeps the triangles the mask refers to. Textured objects made by a bake
  are marked `Baked` and update the same way.
- The Bake Textures controls are no longer marked "later" in the sidebar.
- Faster texture runs, output unchanged to the bit: decimation builds its
  quadrics, edge list, vertex-face lists and first heap fill with numpy (each
  vertex's sum still taken in the JS's order) and its heap keeps costs in a
  flat list; regularize no longer recomputes normals nothing reads; the
  T-junction repair finds open edges with numpy. Decimation is about 1.7-2x
  faster, regularize about 1.5x, a whole run about 1.45x.

## 0.3.0 — 2026-11-14

- The Settings sidebar, laid out as BumpMesh's: its sections, headings,
  labels, ranges and defaults in its order, docked on the right; the Texture
  Gallery dock beside it with favourites, categories, your own maps and a
  credit badge per tile. Entering the workbench opens it with the selected
  body loaded.
- Live preview on BumpMesh's engine: the shader Quick look and the
  CPU-displaced mesh (Draft 500 k, Fine 1.5 M), with a safe fallback where the
  shader can't run.
- Toolbars: BumpMesh's header row (Undo, Redo, Reset to defaults, Save
  Project, Load Project, Texture Gallery, Export STL, Export 3MF), "View"
  (FreeCAD's Wireframe, Perspective View and Section View under BumpMesh's
  labels) and "Check" (Prepare for texture, Check textured mesh).
- Save Project / Load Project in BumpMesh's `.bumpmesh` format.
- Textured objects are re-texturable: they keep their settings, are marked
  out of date when the body changes and rerun only on Update.
- Agent tools on the same draft the sidebar shows: `texture_draft`,
  `texture_set`, `texture_mask`, `texture_layer`, `texture_preview`,
  `texture_export`; `texture_apply` gains Update and Bake Textures.
- The default texture is Knurling (BumpMesh's next default that ships);
  Crystal, Brick and Leather 2 (Filter Forge, not shipped) are out of the
  default favourites.
- The Quick look and Rotate X / Y / Z use the engine's frame exactly
  (three.js Euler XYZ, R = Rx·Ry·Rz).

## 0.2.0 — 2026-11-14

- BumpMesh's engine ported to Python and numpy, bit-identical to BumpMesh on
  the reference cases; the texture library; `texture_list`,
  `texture_settings`, `texture_apply`.
