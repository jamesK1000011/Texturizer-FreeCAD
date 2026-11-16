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

- Turntable on the View toolbar beside Section View (the gallery's spin, one shared: press again, or click, wheel or move the SpaceMouse in the view, to stop) and for agents `texture_preview { turntable }`: `true` spins the view about the part's vertical axis, `false` stops it, a number of degrees turns it once for a render series (turn, `view_snapshot`, turn, ...), without rebuilding the preview.
- Brush Snap: Whole Faces (the sidebar's Snap row under the brush, Off by default; `texture_mask { paint { snap: "faces" } }`): every CAD face the brush touches is painted in full and kept in the mask by its face name, so it follows the part through CAD changes as a picked face does; the hover shows the faces a click takes; erasing takes them out. Without it the Precision brush paints by surface position, a fine hard edge at a small Size and Hardness 100. A part without faces (a mesh) refuses snap in words.
- The full textured preview lands within about 0.5 s of letting go of a brush stroke or a mask pick (it took ~1.7 s on the Mac; offline here ~1.2-1.3 s, now 0.2-0.35 s on a 30 k-triangle part, `tests/test_preview_budget.py` holds it to the budget): with Preserve Untextured on (the default) a mask change needs a new subdivision, so its first look now runs on the part's last full subdivision (the textured result at once, the untextured faces denser, the note "Preview: finishing the untextured faces…") and the exact subdivision replaces it from the same job; a Precision brush stroke's split paint is sampled in numpy, each shared corner once and only for the split triangles the stroke changed or touches (a stroke over a box's top: 1.0-1.5 s to 40-60 ms, the same values); the mask's corner interpolation skips triangles whose parent is one value. The timing probe names the first look's subdivision `sibling`.
- Progressive first preview (roadmap v19 item 5): when a part's subdivision isn't kept yet, the sidebar's 3D Preview first shows a coarse look (edges 3x the preview's, about 20 k triangles, no sliver clean-up; ~0.13 s offline on a 40 x 30 x 10 box with a 3.8 mm hole) and the full preview replaces it on the same worker job. A change drops both; a change from a later stage (scale, rotation, texture, depth) lets the cold subdivide finish and keep it, so the rerun after it is warm instead of starting over; a subdivide change or Cancel stops it. Warm edits are unchanged (one run, no coarse pass).
- The preview's subdivision is kept by the part's triangles and the edge it comes to (Draft and Fine share it when they reach the same edge) and, through `Engine(cache_dir=...)`, on disk in the user data folder's `Texturizer/cache` (the newest 8), so the first preview of a part already seen is warm after a restart.
- Timing probe on the preview path: the engine's own stages (`subdivide1`, `regularize`, `subdivide2`, `subdivide total (miss|hit|disk|waited)`, `map`, `displace`, `colours`, `drawable`) for the sidebar's worker (`preview … (engine)`, `preview coarse … (engine)`) and for `texture_preview` (`texture_preview … (engine)`, plus `mesh_for`, `show`, `estimate`); before, the cold `texture_preview` printed no stage times.

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

- The sliver clean-up (`regularize`) runs its collapses in batches: each batch takes every candidate with no earlier pending candidate sharing a triangle with its corners, checks the same gates (frozen, wing count, folded apex, link condition, plane, edge cap, degenerate, normal) for all of them at once in numpy and applies them together. Nothing earlier can still change what such a candidate reads, so the collapses, output and reject totals are the one-at-a-time run's bit for bit; it is used on both the preview and the export path (BumpMesh parity unchanged), with the one-at-a-time run kept as `regularize_sequential` and used below 20 k triangles, where it is quicker. Offline on a 40 x 30 x 10 box with a 3.8 mm hole, Knurling defaults, edge 0.5 (229 k triangles in, 61 k collapses): 2.8-3.0 s to 1.15-1.3 s, about 2.4x; the preview's `regularize` stage 1.4 s against about 3 s. Short of the 5x aimed for: the order the JS visits candidates in (thinnest first, ties by index) makes sweeping chains of about 180 batches, and the bookkeeping that keeps the order costs about as much as the gates. The welding inside it groups exact float copies first (same vertex ids as before). Which per-triangle gate a refused edge is counted under in `rejectStats` can differ (totals do not).

- `regularize` (the sliver clean-up after subdividing, shared with the export) does less per collapse (a vertex's triangles need no deleted check, wings found by set membership): about 5-10 % faster, outputs and reject counts bit-identical (BumpMesh parity unchanged). On the cold preview it collapses ~120 k edges of the ~340 k-triangle first subdivision, one by one as the JS does: still the cold preview's main cost (~5.7 s of ~7 s offline).

- "Points on edges" now judged by FreeCAD's own rule (MeshEvalPointOnEdge: a point within 0.001 mm of a facet edge and inside that edge's box, not one of its corners), which the earlier cap check (0.1 µm) missed: printable() splits caps out, collapses a chain of nearly collinear points on a flat patch onto its end (manifold, nothing turned over, surface moved under 0.005 mm), and the export's untangling keeps displaced walls 0.01 mm apart (vertices included), so the decimation after can't bring them within FreeCAD's 0.001 mm. Offline: 3.8 mm round hole 15 -> 0, 3.6 mm hole on a 24 mm pad 5 -> 0, 4 mm square hole 13 -> 0; still closed, manifold, no self-intersections. texture_apply answers `engine` (refine length, triangles subdivided / displaced / out, ms per stage, points on edges left) and, with the timing probe, prints it; a running preview steps aside during texture_apply and reruns after (live, an Update after a CAD change took 26 s against 5 s with the preview subdividing the changed part beside it).
- The textured mesh evaluates clean in FreeCAD's Mesh evaluation on parts with holes: where the surface is concave (a hole's wall, a pocket's inner corner) or creased, displaced walls that would cross, or land on one another, have their displacement pulled back (halved per round) until they don't (engine/untangle.py, export only; flat and convex regions are untouched, so the BumpMesh parity cases stay bit-identical), and cap triangles (a corner on the opposite edge: "points on edges", which BumpMesh's own output has too) are split out before the mesh reaches FreeCAD or a file (engine.printable). Offline, 40 x 30 x 20 box, Knurling: 3.6 mm round hole 96 self-intersecting pairs and 1 cap -> 0 and 0; 4 mm square hole 35 pairs, 18 non-manifold edges and 1 cap -> 0, 0 and 0.
- A linked Textured object no longer sits at "updating…": a recompute with the geometry already being rebuilt no longer restarts the run (repeated recomputes kept replacing it), a transaction left open delays the write by at most 2 s, and a failed or stopped run says so in TextureState and the Report view; the timing probe prints each rebuild's queued / started / finished / waiting / written / skipped (why) / failed. Its `Source` is an `App::PropertyLinkGlobal`, so a body inside an Assembly no longer warns "Link(s) … go out of the allowed scope" on every recompute (older objects convert on open or Update). Faces are wound by their own normal where they are (a hole's cylinder was checked across its axis and came out flipped, so the texture pushed the wall into the part), the whole tessellation is then made consistent and outward, and the textured mesh is harmonized by FreeCAD's Mesh module.
- The sidebar's draft follows its part too: after a CAD change (a Hole turned a Pad's top from Face6 into Face5), its masks move by the same anchors (mapped names, then surface samples; brush paint by surface), in the CAD step's own Undo step; what can't move is flagged in the sidebar and the Report view, and the new faces stay smooth (excluded) and are listed with Include Them. Add to Document after a CAD change never applies old face names to renumbered faces.
- The Textured object follows its body like any FreeCAD feature (docs/linked-texture.md): it is a `Mesh::FeaturePython` linked to the body (`Source`, an `App::PropertyLink`), and when the body changes its recompute textures it again from the stored draft, in the background with the window (`updating…` until the mesh is written, outside any transaction, no extra Undo step; a newer change makes an older run stale), in place headless; Update stays. Masks follow by FreeCAD's mapped (toponaming) face names, else by sample points on the face's surface; brush paint by the surface it was on. What no longer fits is left out and flagged in the sidebar and the Report view ("Layer 1: 2 of 3 masked faces no longer exist; re-pick them."), never guessed. Faces a later CAD step makes (a pocket, hole, fillet) stay smooth whatever the mask mode and are listed with Include Them (`texture_apply {update, include_new}`); the sidebar says the texture applies to the finished part. Old documents' Textured objects convert when they open.
- The texture draft lives in the FreeCAD document, so Edit ▸ Undo / Redo (Std_Undo / Std_Redo) undo texture edits with the rest of the model: each part's draft is a hidden object (its settings as JSON, the masks and brush paint as an included file), written inside a "Texture: <what changed>" transaction. A slider, Rotate ring, stamp or wheel drag is one Undo (open on the first move, committed on release); a `texture_*` tool is one step, joining the tool's own transaction; the sidebar, rings and preview follow an Undo (the preview reruns from the stage it changed). The draft is saved with the document and comes back when it is reopened. Without FreeCAD the draft keeps its own undo. Importing a BumpMesh project with its model is one step. FreeCAD's Redo brings a texture edit back (the draft follows the draft object's property changes, without relying on the redo signal or `doc.Transacting`, and compares documents by name, not by Python object); an Undo or Redo reloads the draft once, after the step, and the sidebar syncs only what it changed. With the timing probe on, the path prints `docdraft changed`, `docdraft signal undo/redo`, `docdraft reload waited`, `docdraft reload`, `docdraft read`, `docdraft apply` and `docdraft write` lines; the draft object is never left touched for a recompute.
- An Undo no longer reruns the preview from subdivide for seconds: the subdivided-mesh key reads 1 and 1.0 as one setting (an Undo's JSON int against a slider's float missed the cache), a Visibility the Undo restored no longer asks the preview for a subdivide when the part and its mesh are the same, two runs of one key wait for one subdivide instead of making it side by side, and a stale preview's sliver clean-up stops within 1024 candidates. With the timing probe on: `preview start <stage> (missing …; dirty …; last request <stage> by <function> < <caller>)` and `preview subdivide cache key … how hit|miss|waited mesh … settings … hard … budget …`.
- A texture switch, or an Undo / Redo of one, no longer subdivides the part again: subdivided meshes are kept by what they are made from (the part's triangles, refine length, preview quality, triangle budget, regularize settings and, with Preserve Untextured, the faces no layer textures), never the texture, its scale or the frame. Offline on a 40 x 30 x 20 mm box with an M3-size through hole (320 triangles), a preview rerun from subdivide went from 6.3 s to 0.13 s with the same mesh; a first preview of a part (or a new refine length) still subdivides, and its sliver clean-up (BumpMesh's sequential edge collapses, ~110k on that part) is most of that time. The layer strip updates its rows in place when the layers stay the same in number (a new thumbnail and name only where a texture or name changed), and the timing probe names the stage each change asks the preview for (`preview request <stage> (<kind> <what>)`).
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

### Fixed

- `texture_set` takes `textureHeight` (BumpMesh's name for the depth, used by `texture_apply`, `.bumpmesh` projects and the texture guide's own example) as the height slider (`amplitude`); engine tuning settings only `texture_apply` takes are refused in words that say so. A test checks every setting name in the guide's and the tool descriptions' examples against both tools.

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
