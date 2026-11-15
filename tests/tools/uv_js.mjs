// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2026 the Texturizer for FreeCAD contributors.
// Runs computeUV from BumpMesh's js/mapping.js (stlTexturizer by Stefan
// Hermann, CNC Kitchen, AGPL-3.0) on the cases in a JSON file, for
// tests/test_quicklook.py's comparison with texturizer/uvmath.py.
//
//   node tests/tools/uv_js.mjs BUMPMESH_DIR CASES.json   -> JSON on stdout
//
// CASES.json: [{mode, settings, bounds: {min, size, center} (3-arrays),
//               points: [[px, py, pz, nx, ny, nz], ...]}, ...]
// Output: one list per case, per point a list of [u, v, w] samples (a plain
// {u, v} result is one sample of weight 1).
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';

const [src, casesPath] = process.argv.slice(2);
const { computeUV } = await import(pathToFileURL(join(src, 'js', 'mapping.js')).href);
const v3 = (a) => ({ x: a[0], y: a[1], z: a[2] });
const cases = JSON.parse(readFileSync(casesPath, 'utf8'));
const out = cases.map((c) => {
  const b = { min: v3(c.bounds.min), size: v3(c.bounds.size), center: v3(c.bounds.center) };
  return c.points.map((p) => {
    const r = computeUV(v3(p), v3(p.slice(3)), c.mode, c.settings, b);
    return r.triplanar ? r.samples.map((s) => [s.u, s.v, s.w]) : [[r.u, r.v, 1]];
  });
});
process.stdout.write(JSON.stringify(out));
