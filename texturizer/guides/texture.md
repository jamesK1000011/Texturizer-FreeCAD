# Surface textures (Texturizer, based on BumpMesh by CNC Kitchen)

- Name the faces that must not change first — mating faces, press-fits, threads, a face another part slides on — and say them back to the person.
- `texture_prepare { body, faces, mode }` writes the part as an STL and a mask of exactly those faces' triangles: `include` textures only them (a grip), `exclude` all but them. It answers the `bumpmesh_texture_file` call to make (BumpMesh's own engine, its library textures, depth `textureHeight`, tile `scaleU`).
- Check what came back: `texture_check { body, path }` — the depth on each textured face, `leaked` (a face meant to stay plain that moved: fix the mask, texture again), `bare` (meant for texture, barely moved: the texture is finer than `refineLength`), and whether the mesh is closed. Only an `ok` goes to the slicer. A textured part is a mesh: slice it as it is.

