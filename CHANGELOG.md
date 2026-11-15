# Changelog

All notable changes to Texturizer for FreeCAD. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

Texturizer for FreeCAD is based on BumpMesh by Stefan Hermann (CNC Kitchen),
github.com/CNCKitchen/stlTexturizer, AGPL-3.0.

## [Unreleased]

### Added

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

- Bake Textures runs BumpMesh's bake pipeline: no decimation, so the baked
  mesh keeps the triangles the mask refers to. Textured objects made by a bake
  are marked `Baked` and update the same way.
- The Bake Textures controls are no longer marked "later" in the sidebar.

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
