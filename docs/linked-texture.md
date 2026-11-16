<!-- SPDX-License-Identifier: AGPL-3.0-only -->
# The textured result follows the CAD model

**Goal.** A Textured object behaves like any FreeCAD feature. When the body
it textures changes, it textures that body again. When a mask no longer fits
the body, it says so and never guesses.

## The object

`Mesh::FeaturePython` with a Python proxy (`texturizer.linked.TexturedFeature`)
and these properties:

| Property | Type | Holds |
|---|---|---|
| `Source` | `App::PropertyLink` | the body: FreeCAD's dependency graph, renames, deletion |
| `TextureDraft` / `TextureArgs` | String | what to run again (as before) |
| `MaskAnchors` | String (JSON) | the masks as geometry, not indices (below) |
| `TextureState` | String | `up to date`, `updating…`, or `mask needs re-pick: …` |
| `NewFaces` | String (JSON) | faces made after setup, left smooth until included |
| `SourceSignature` | String | the body's geometry the mesh was made from |
| `SourceBody`, `Credit`, `TextureSettings`, `OutOfDate`, `Baked` | (as before) | `SourceBody` mirrors `Source.Name` for the code that reads it |

## Recompute

A body change touches the Textured object through `Source`, so FreeCAD calls
`execute()`. It compares `live.signature(Source)` with `SourceSignature`. If
they match there is nothing to do; the mesh is current. Otherwise, on the
GUI thread, it:

1. tessellates the body (`live.mesh_for`, cached);
2. maps the masks onto it (below);
3. builds the engine run from the stored draft.

With FreeCAD's window it then sets `TextureState = "updating…"` and queues
the engine run on a background thread (`linked.Reruns`). Each object keeps a
generation number, so a newer change makes an older run stale; the engine's
`should_abort` stops it. The finished mesh is posted back to the GUI thread
and written only when no transaction is open. The write covers `Mesh`,
`SourceSignature`, `TextureState` and `MaskAnchors`, all outside any
transaction, so the person gets no extra Undo step. Then `purgeTouched()`
keeps the recompute clean.

Headless, or before the window exists, the run is synchronous inside
`execute()`. Update (`texture_apply {update}`, the sidebar button) runs the
same path synchronously.

## Masks as geometry

`MaskAnchors` keeps, per layer:

- **Faces:** the mapped (topological naming) name from
  `Shape.ElementReverseMap`, if there is one, and up to 24 sample points
  (centroid and normal of the face's own tessellation triangles).
- **Painted triangles** (brush, Fill, mesh parts): the old tessellation's
  triangle centroids, normals and radii, once per object; the painted
  indices stay in the draft.

Remapping onto the new body:

- **Faces.** If the mapped name is still in `Shape.ElementMap`, that face is
  used. Otherwise, each new face that has a sample point on its surface
  (distance within the tolerance, normal within about 18°) is used; a face
  that was split maps to both pieces. If no sample point lands, the face is
  gone.
- **Painted triangles.** A new triangle is painted when its centroid's
  nearest old triangle was painted and lies within that triangle's radius
  with a similar normal. Soft brush values move to new corners with the
  same position; otherwise the nearest corner of the matched triangle.
  Precision-brush splits move only to triangles with the same three corners;
  the rest are dropped and reported.

## New faces

The anchors also keep **known**, every face the texture was set up with (or
last confirmed by Add to Document / Include them). After a rebuild, any face
that no known face maps to was made by a later step (a pocket, hole, fillet
or boss). It stays smooth whatever the layer's mode: an exclude layer
excludes it, an include layer never lists it. It goes in `NewFaces`; the
sidebar lists it with **Include Them**, which adds it to the include layers
and the known set (`texture_apply {update, include_new}` for the agent). The
sidebar also says in one line that the texture applies to the finished part,
so later CAD steps don't build on it.

## The sidebar's draft follows too

`linked.DraftFollower` keeps a snapshot of the draft's part: its
tessellation and mapped names, the geometry the masks were made on. After a
recompute of that part (`slotRecomputedObject`, then `slotRecomputedDocument`,
still inside the CAD step's transaction, so its Undo undoes both), and before
any `texture_*` tool reads the draft, a changed signature moves the draft's
masks. It uses the same anchors and rules as above: flagged, never guessed;
new faces excluded and listed with Include Them. So Add to Document never
applies old face names to renumbered faces (live on the Mac: a Hole turned
the Pad's top from Face6 into Face5).

## When a mask can't be mapped

That part is left out, the layer is flagged, and the message goes to the
Report view and to `TextureState`, for example: "Layer 1: 2 of 3 masked faces
no longer exist; re-pick them." The sidebar shows the Textured object's
state. Update stays a manual button.

## Old documents

On open (`slotFinishRestoreDocument`), and before any Update, a `Mesh::Feature`
with a string `SourceBody` becomes a linked FeaturePython. It keeps its mesh,
properties and label, and the old object is removed. Anchors come from the
current body only when the object was up to date. If it was out of date, its
face names may already be wrong, so it is flagged for re-pick.

## Later: mid-step marker

Not built yet: a texture "marker" step placed mid-tree, with the texturing
worked out from face history (which later faces descend from the marked
ones). Today the texture applies to the finished part only.

## FreeCAD API this relies on (needs a live check on 1.1.3)

- `Mesh::FeaturePython` with a proxy `execute`, `onDocumentRestored`,
  `dumps` / `loads`;
- `App::PropertyLink` dependency recompute;
- `obj.purgeTouched()`;
- `doc.HasPendingTransaction`;
- `Shape.ElementMap` and `Shape.ElementReverseMap` on PartDesign bodies;
- `DocumentObserver.slotFinishRestoreDocument`, `slotRecomputedObject`, `slotRecomputedDocument`;
- `FreeCAD.Console.PrintWarning`;
- `doc.removeObject`;
- setting `obj.Mesh` outside `execute()`.
