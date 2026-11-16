# Texturizer-FreeCAD: hardening findings (texture group, 11 tools) — 16 Nov 2026

Nothing here ran on FreeCAD. The engine (plain Python) did run here on a 12-triangle box for the model's numbers.
Tests: `python3 -m pytest -q` 401 passed, 23 skipped (FreeCAD ones skip here).
Checked against FreeCAD 1.1 source (tag 1.1.0; the Mac runs 1.1.3): src/Mod/Part/App/TopoShape.pyi and TopoShapePyImp.cpp
(tessellate, hashCode), TopoShapeFace.pyi (normalAt), GeometrySurface.pyi (parameter), src/App/ComplexGeoData.pyi and
ComplexGeoDataPyImp.cpp (ElementMap, ElementReverseMap), src/Mod/Mesh/App/Mesh.pyi (harmonizeNormals, CountFacets, Topology).
No FreeCAD wiki page covers this add-on; behaviour was checked against its own code, docs/linked-texture.md and tests.

Credits: every `source` field and every credit string is kept; "CNC Kitchen" now appears 14 times in tools.json (12 before).

## Fixed in code (texturizer/handlers.py)

1. Relative and ~ paths. texture_prepare wrote a relative `path` into FreeCAD's working folder while Agent Link's path guard
   had judged it against the output folder; texture_export and texture_apply's export joined the output folder but took
   "~/x.stl" as a folder named "~" there; texture_check, texture_draft { project } and texture_set { texture: { file } } read
   relative paths from the working folder. One helper `_local` now does all of them (~ expanded, relative = output folder).
   Effect: texture_check { path: "x.stl" } finds the file texture_export { path: "x.stl" } wrote. texture_prepare also makes
   the folder.
2. texture_mask { triangles }: indices were stored unchecked (any integer); now refused outside 0..n-1 with where to get n.
3. texture_mask on a Mesh part: `remove` did nothing without a word, `add_by` with faces crashed (AttributeError on a shape
   that is None); both now refuse and name fill { triangles } / paint.
4. texture_export { object } on an object with no Mesh (a Body) crashed (AttributeError); now a refusal naming texture_apply.
5. 49 refusal lines touched (about 45 texts rewritten, 4 new) to name the wrong input, what is allowed and the call that fixes it (texture ids, layers,
   hidden layers, 4-layer limit, cylinder autofit, stamp, picks that need the window, memory limit, "stopped before it finished").

## Fixed in data and docs

- tools.json: all 11 descriptions rewritten; 57 input descriptions added or sharpened; `workbench`, `commands`
  (only names registered in texturizer/commands.py) and `words` on every tool. No input added, removed or renamed.
- guides/texture.md: the section "Out of date and Update" said a Textured object "never reruns on its own". The code
  (linked.TexturedFeature.execute -> linked.rerun) reruns a draft-made object when its body changes and only marks an
  argument-made one OutOfDate. Section rewritten; a "Size, time and limits" section added.
- README.md tool list regenerated (tools/gen_readme_tools.py).

## Per tool

| Tool | Checked against | Wrong and fixed | Still doubtful, needs a live check | Description |
|---|---|---|---|---|
| texture_list | engine/library.py, textures/catalog.json | category refusal now echoes the wrong value | nothing | rewritten |
| texture_settings | engine/index.py DEFAULTS, engine.estimate, mesh.face_triangles; TopoShape.tessellate | nothing in code | estimate.safetyCap is `n >= 10 000 000` and the warning text says 10 M, but subdivision.SAFETY_CAP is 16 000 000: which is BumpMesh's figure is not verified, left as is | rewritten (limits, 420 bytes per triangle, x4 per halving) |
| texture_draft | docdraft.py, live.choose_part | project path made local | It is `changes: false` yet writes the hidden TextureDraft object in its own transaction: no shared health check runs on it (by design; say if that is unwanted). project "ask" blocks on a dialog (described) | rewritten |
| texture_set | draft.set, controls, projection_extras | file path made local; refusals | setting a texture resets scaleU/scaleV from the library (described); frame { face }, cylinder from_face on real faces | rewritten |
| texture_mask | draft.mask_add, paint.py; TopoShape.hashCode (ShapeMapHasher, orientation-free, used by fill) | triangles range, Mesh-part remove/add_by | add_by / fill with no `faces` and no window fall back to the window's picks; hashCode collisions are possible in principle (never seen) | rewritten (order of operations inside one call) |
| texture_layer | draft.add_layer / remove_layer / move_layer | refusals | nothing | rewritten |
| texture_preview | engine._preview_edge, preview.TRI_BUDGET (500 k / 1.5 M), MEMORY_LIMIT 1 GB | refusals | Declared heavy + read, so Agent Link runs it as a job: it calls live.GUI.show_result from the job — whether that is on the GUI thread is Agent Link's doing, not verified here | rewritten |
| texture_apply | linked.py (Mesh::FeaturePython, PropertyLinkGlobal, execute/rerun), docs/linked-texture.md, engine estimate | export path; refusals | (a) the result is written to a Mesh::FeaturePython's Mesh, and execute() only overwrites it when the body's signature changed, so it is not lost at recompute — by code, not run. (b) a body with a Placement inside an App::Part: the mesh is built from obj.Shape in its own frame; whether the Textured object lands on the body there is not verified. (c) time on a real part: only measured here on a box (29 442 triangles, about 4 s) | rewritten (three ways to call, draft-made vs one-call following, limits, cannot be a job) |
| texture_export | engine.export, writers, project.save | object-not-a-mesh crash; ~ paths | without `path` and with a window it opens a file dialog and waits (described; behaviour kept because the sidebar's button uses it) | rewritten |
| texture_prepare | mesh.prepare, mesh.face_triangles | relative path | `next` names bumpmesh_texture_file, a tool of the separate BumpMesh server (kept; described as such) | rewritten |
| texture_check | mesh.check, meshdist | paths made local; refusals say the third way (body + path + mask) | cost on a 750 000-triangle result not measured | rewritten (every reply field) |

## Module start-up and recompute (the brief's two lessons)

- No texture tool writes a shape into a parametric object: the body is never written; results are Mesh features.
- The tools import FreeCAD's Mesh module (`import Mesh`) and, shown live, Agent Link switches to TexturizerWorkbench
  (fam_texture.WORKBENCHES). Neither is another workbench's GUI module with a first-run dialog as far as the code shows;
  not run.

## Wishes (not done: outside this checkout or a behaviour change)

- texture_export should not open a dialog for an agent's call at all (ask defaulting to false when the call comes through
  Agent Link). It is a behaviour change, so only described.
- A health.register reading for the texture group (TextureState "mask needs re-pick", OutOfDate) would put the linked
  object's state in every changing reply; texture_apply already answers state / maskReport itself.

## Test model

texturizer-model.json: 52 calls on a 40 x 30 x 10 box, 9 of them expected refusals. Not reached: the calls that wait for a
person, bake, cylinder / stamp / frame, a Mesh part (listed in notCovered).
