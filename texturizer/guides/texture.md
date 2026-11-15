# Surface textures (Texturizer, based on BumpMesh by CNC Kitchen)

- Name the faces that must not change first — mating faces, press-fits, threads, a face another part slides on — and say them back to the person.
- `texture_list` gives the library (ids, categories, credits); `texture_settings { body, texture }` gives BumpMesh's settings and, for the body, the predicted triangles and a suggested `refineLength`.
- `texture_apply { body, texture, faces, mode, settings }` runs BumpMesh's engine in FreeCAD and adds the textured mesh beside the body (the body is untouched): `include` textures only the faces (a grip), `exclude` keeps them plain. Depth `textureHeight` (mm), tile `scaleU` (mm), `mappingMode` 0-8, up to 4 `layers`. `export: "part.stl"` (or .3mf) writes the file too.
- `texture_prepare { body, faces, mode }` writes the part as an STL and a mask of those faces' triangles, for texturing outside FreeCAD.
- Check what came back: `texture_check { body, path }` — the depth on each textured face, `leaked` (a face meant to stay plain that moved: fix the mask, texture again), `bare` (meant for texture, barely moved: the texture is finer than `refineLength`), and whether the mesh is closed. Only an `ok` goes to the slicer. A textured part is a mesh: slice it as it is.

