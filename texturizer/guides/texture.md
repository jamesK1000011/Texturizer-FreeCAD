# Surface textures (Texturizer, based on BumpMesh by CNC Kitchen)

The engine, the settings and the sidebar are BumpMesh's by Stefan Hermann (CNC Kitchen), ported to Python and credited. Setting names are BumpMesh's (`texture_settings` lists them).

- Name the faces that must not change first — mating faces, press-fits, threads, a face another part slides on — and say them back to the person.
- `texture_list` gives the library (ids, categories, credits); `texture_settings { body, texture }` gives BumpMesh's settings and, for the body, the predicted triangles and a suggested `refineLength`.

## The draft (what the sidebar shows)

There is one draft per window, shared by the sidebar, the gallery and these tools: each call moves the sidebar live and the preview follows.

1. `texture_draft { part }` — the body (or a Mesh, or a Textured object to texture again: its settings come back). Opens the sidebar. `reset: true` is BumpMesh's Reset to defaults; `project: "<file>.bumpmesh"` its Load Project (`mode` all or settings).
2. `texture_mask { mode, add }` — `exclude` keeps the faces plain, `include` textures only them (switching clears the layer's faces, as BumpMesh). `add_by` direction / box / surface from picked faces; `fill { faces, maxAngle }` (a Mesh part: `fill { triangles: [seed], maxAngle, erase }`); `paint { strokes: [[{point, normal, triangle}, ...]], size, hardness, type, mode, snap, erase }` is BumpMesh's Brush on the tessellation, one undo step a stroke (mode `precision`, the default, splits triangles under the rim and Hardness under 100 fades to it, so a small `size` with `hardness` 100 is a fine hard edge by surface position; `standard` paints whole triangles; `snap: "faces"` paints every CAD face the brush touches whole and keeps it by its face name, so it follows the part through CAD changes); `clear`.
3. `texture_set { texture, settings }` — e.g. `{ textureHeight: 0.5, scaleU: 5, mappingMode: "cubic" }`; `frame { rotation }` (BumpMesh's Rotate, Euler XYZ), `frame { direction: [x, y, z] }` (a Planar projection looks along it; Triplanar / Cubic: the frame's Z; what the 3D view's projection arrow sets, with `rotation` and `scaleU` / `scaleV` in settings for its dial and size knob) or `frame { face }` (Place on Face); `cylinder` autofit / reset / from_face; `stamp` on / off / `{ point, normal }`.
4. `texture_layer { action }` — add (a copy of the active layer, Include Only), remove, move, solo, visible, blend cover / add, up to 4.
5. `texture_preview` — the engine's preview in the 3D view (Draft 500 k, Fine 1.5 M): edge, triangles, time, the export's size. Look with `view_snapshot`. `suggest: true` is BumpMesh's Suggest values. `turntable: true` spins the view about the part's vertical axis (`false` stops it); `turntable: 45` turns it once by 45° for a render series (turn, `view_snapshot`, turn, ...).
6. Say back the texture, depth, tile and the plain faces, then `texture_apply` — a Textured Mesh object beside the body (the body is untouched). Applying the same draft again updates that object; `new: true` makes another; `bake: true` is Bake Textures: the result becomes the part, and with Mask just-baked faces on (`bakeMask`, the default) the fresh layer excludes what was just textured. Like PartDesign hiding the previous feature, the Body is hidden while its textured result is shown (`bodyHidden` in the answer; its flat faces would otherwise show through the texture, and faces left smooth are part of the result): hiding or deleting the result, or Undo, shows the Body again.
7. `texture_export { format: stl | 3mf | bumpmesh, path }` — the file (3MF and STL carry the credit); `bumpmesh` is BumpMesh's Save Project.

## Following the body, and Update

A Textured object made from the draft is linked to its body (`Source`) and stores the draft. When the body changes it textures the body again by itself: with FreeCAD's window in the background (`TextureState` reads `updating…` until the new mesh is in), without a window inside the recompute. Its masks follow the faces by FreeCAD's stable names or their surface; a mask that no longer fits is left out and reported (`mask needs re-pick`), and faces a later CAD step made stay smooth until `texture_apply { update: "<object>", include_new: true }`. `texture_apply { update: "<object>" }` runs it again at once and answers `state`, `maskReport` and `newFaces`.

A Textured object made in one call (`texture_apply { body, texture, ... }`, no draft) stores its arguments instead: when its body changes it is only marked `OutOfDate`, and `texture_apply { update: "<object>" }` runs the same arguments again. Its faces are names, so check them after a CAD change.

## Size, time and limits

- The engine refines the part's mesh until no edge is longer than `refineLength` (1 mm by default), displaces it, then reduces it to `maxTriangles` (750 000 by default). Halving `refineLength` makes about four times the triangles, the memory and the time.
- `texture_settings { body, texture, settings }` and `texture_preview` answer the estimate before anything runs (`subdividedTriangles`, `memoryBytes`; about 420 bytes per refined triangle). `texture_apply` and `texture_export` refuse a run estimated over `memoryLimitGB` (4, about 10 million refined triangles) and say what to raise; the engine also stops refining at its own cap and says so in `warnings`.
- `texture_apply` changes the document, so it cannot run as a job: it runs inside the call and FreeCAD's window is busy until it ends. `texture_preview`, `texture_export` and `texture_prepare` are long reads: past about 45 s the call answers with a job id and `job_status` gives the answer.
- On a large part, a mask (only the faces that need texture) costs far less than a finer `refineLength`: plain faces are kept as they are (`preserveUntextured`).
- `texture_export` without `path` (and without `ask: false`), `texture_draft { project: "ask" }`, `texture_set { texture: { upload: true } }`, `stamp: "place"`, `face: "selected"` and `add: "selected"` wait for a person at the window. Give paths, names and points instead.
- A path in `texture.file`, `mask`, `project` or `export.path` is checked as Agent Link checks `path`. A file in a credentials or settings folder is refused. A file name with hidden characters is refused: the person renames the file.
- Faces are `Face1`..`FaceN` of the part as it is now (from 1); triangles are indices from 0 of the part's tessellation at 0.02 mm (`texture_draft` answers the count). Both change when the CAD model changes.
- The result is a mesh beside the body, never a solid: fillets, holes and booleans do not build on it. Texture last.

## Without the draft

- `texture_apply { body, texture, faces, mode, settings }` runs BumpMesh's engine in one call: `include` textures only the faces (a grip), `exclude` keeps them plain. Depth `textureHeight` (mm), tile `scaleU` (mm), `mappingMode` 0-8, up to 4 `layers`, each `{texture, settings, faces, mode}`: a layer that gives no `texture`, `faces` or `mode` takes the top-level one; one that gives a different one is refused. Settings go in `settings` (every layer) or the layer's `settings`. `export: "part.stl"` (or .3mf) writes the file too. It can be updated the same way.
- `texture_prepare { body, faces, mode }` writes the part as an STL and a mask of those faces' triangles, for texturing outside FreeCAD.

## Check

`texture_check { object }` — the Textured object `texture_apply` made, checked against its body with the mask it stores (no `texture_prepare` needed; `path` of its `texture_export` STL works too). For a file textured outside FreeCAD, `texture_check { body, path }` with `texture_prepare`'s mask. It answers the depth on each textured face, `leaked` (a face meant to stay plain that moved: fix the mask, texture again), `bare` (meant for texture, barely moved: the texture is finer than `refineLength`), and whether the mesh is closed. Only an `ok` goes to the slicer. A textured part is a mesh: slice it as it is.
