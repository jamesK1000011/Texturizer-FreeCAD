# Changelog

All notable changes to Texturizer for FreeCAD. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

Texturizer for FreeCAD is based on BumpMesh by Stefan Hermann (CNC Kitchen),
github.com/CNCKitchen/stlTexturizer, AGPL-3.0.

## [Unreleased]

The dated versions below were never tagged: the first tagged release is
being prepared.

### Added

- Precision brush, ported from BumpMesh's paint tree: Mode Precision (the default, as BumpMesh) splits the triangles under the rim so the mask follows the circle on any tessellation, with Hardness for a soft edge; Standard paints whole triangles and hides Hardness. The 3D view shows BumpMesh's hover preview of what a click marks (Standard, Single, Fill). Split paint reaches the preview (sampled per refined corner), the export (the flattened tree, as BumpMesh's) and `.bumpmesh` projects both ways; `texture_mask { paint { mode } }`.
- Projection gizmos in the 3D view, as BumpMesh's: with Rotate on, X / Y / Z rings on the part turn the projection as they are dragged (one undo step a drag; the part stays put, so the projection turns with the cursor); a stamp's patch is dragged over the surface from where it was grabbed (Surface normal follows the surface and stops at an edge), Position on model arms a click that places it and drags on, and after a press on the stamp the wheel resizes it until the next press elsewhere.
- Turntable in the Texture Gallery as BumpMesh's: the view spins about Z through the model's centre, one turn every 24 s, from the moment the gallery opens unless switched off (the choice sticks); a press, the wheel or a SpaceMouse in the view hands it back.

- README: how to uninstall, and a privacy statement.

- `texture_check { object }` checks a Textured object against its body with the mask it stores (draft or arguments), with no `texture_prepare` mask; `path` of its `texture_export` STL alone works too. Faces BumpMesh's angle limits cover whole are listed as `angleMasked`, not `bare`. The `-mask.json` route is unchanged.

- Paint brush, ported from BumpMesh: Single / Circle, Size, Hardness (soft coverage per vertex), Shift to erase, one undo step per stroke; Fill on meshes; `texture_mask { paint, fill }`. Painted masks round-trip `.bumpmesh` projects exactly.

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

- The texture draft lives in the FreeCAD document, so Edit ▸ Undo / Redo (Std_Undo / Std_Redo) undo texture edits with the rest of the model: each part's draft is a hidden object (its settings as JSON, the masks and brush paint as an included file), written inside a "Texture: <what changed>" transaction. A slider, Rotate ring, stamp or wheel drag is one Undo (open on the first move, committed on release); a `texture_*` tool is one step, joining the tool's own transaction; the sidebar, rings and preview follow an Undo (the preview reruns from the stage it changed). The draft is saved with the document and comes back when it is reopened. Without FreeCAD the draft keeps its own undo. Importing a BumpMesh project with its model is one step. FreeCAD's Redo brings a texture edit back (the draft follows the draft object's property changes, without relying on the redo signal or `doc.Transacting`, and compares documents by name, not by Python object); an Undo or Redo reloads the draft once, after the step, and the sidebar syncs only what it changed. With the timing probe on, the path prints `docdraft changed`, `docdraft signal undo/redo`, `docdraft reload waited`, `docdraft reload`, `docdraft read`, `docdraft apply` and `docdraft write` lines; the draft object is never left touched for a recompute.
- The CPU preview lands ~4x sooner after a Rotate ring drag (40 x 30 x 20 mm box, Knurling, offline: the displace stage 440 ms to 100 ms, the whole rerun 490 ms to 135 ms; on the Mac it was 1.67 s): the displace stage keeps the vertex weld and the 32-pass blend-normal smoothing between runs on the same mesh and checks them against the new input (the smoothing turns with the frame, so a new frame takes it turned rather than rerun), the texture sampling wraps without divisions, and the preview's vertex normals sum with bincount. A first preview, or the same inputs again, is bit-identical to before; a turned frame's positions agree within float32 rounding (under 4e-6 mm).
- Texturizer leaves to FreeCAD what FreeCAD already does: Undo and Redo are Edit ▸ Undo / Redo (the sidebar's and toolbar's own are gone); Export STL is File ▸ Export of the Textured object Add to Document makes (the agent's `texture_export` is unchanged); Wireframe and Perspective View are FreeCAD's Draw Style and camera commands. Save / Load Project are now Export / Import BumpMesh Project… (the same `.bumpmesh` files, for sharing with the BumpMesh web app). Load Model… is Use Selected Part: the selected solid, else this document's last textured part, else the one solid shown; an STL is imported with File ▸ Import first. Section View still runs FreeCAD's clipping plane, tabbing its dock so the window keeps its size.
- Faster live editing in the 3D view: a Rotate ring, stamp or brush drag no longer rebuilds the CPU-displaced preview on every move (the Quick look follows the drag; one rebuild on release, or every 120 ms of pause without the Quick look), with the same mesh once settled; Undo / Redo reruns the preview once and only from the stage it changed (a Size undo from the map stage, not from subdivide), and the sidebar rebuilds its layer strip once and reads the texture library once a refresh; a brush dab rewrites only the overlay colours it changed; the part's geometry check runs once a view event. A timing probe (parameter `Mod/Texturizer` Bool `TimingProbe`, off by default) prints `Texturizer timing: <stage> <ms>` to the Report view for each event handler, draft change and listener, preview stage (worker), Coin rebuild, overlay draw and sidebar refresh, and the CPU preview's render when on before the sidebar opens; view callback errors reach the Report view with their traceback.
- Settings sidebar: a layer row stays on one line in a narrow dock: the name elides at its end first ("Layer 1 · Knu…", left-aligned, measured on the button's own contents width as the theme pads it; the whole name in its tooltip), and Solo, Cover, ▲, ▼, ✕ are one group that never splits.
- A draft with no part takes the selected solid, else the last part this document's draft used, else the one best visible solid (a PartDesign Body before other features, scripted objects last, a container repeating its children left out); never a hidden object (after a restart it took a hidden scripted object over the Body). Hiding the draft's part hides its preview, rings and brush with it; showing it brings them back.
- `texture_set { stamp }` places in the texturing frame when Rotate is set (it used the document's). Moving a stamp reruns the preview from the map stage, not from subdivide.
- The CPU preview rebuilds the masks from the map stage, so a mask change shows in it at once (it reused the previous masks).
- Settings sidebar: rows of buttons (the header row, the layer rows, option rows, Rotate, the mask footer) wrap instead of running under the vertical scrollbar, the combos shrink with the dock, and the dock's minimum width (content plus scrollbar) is measured again once the theme has styled it.
- About Texturizer shows an About box (name, version, licence, the BumpMesh credit with its link); on macOS it and Settings Sidebar keep their place in the Texturizer menu.
- Section View toggles FreeCAD's Clipping dock and opens it tabbed in its area, without growing the main window.
- Tooltips say what a command does and no longer carry the credit (macOS cut them short); the credit stays in About, the sidebar's footer and the workbench's tooltip. The Texture panel says Add to Document, not the tool's name.
- Title Case for command names, buttons, menu items and group titles, as FreeCAD's design guide asks, BumpMesh-derived ones included (Reset to Defaults, Add to Document, Add Layer, Upload Custom Map…); labels and checkboxes keep BumpMesh's wording.

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
