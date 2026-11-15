// SPDX-License-Identifier: AGPL-3.0-only
// Reference outputs from BumpMesh's own engine (stlTexturizer, Stefan Hermann /
// CNC Kitchen, AGPL-3.0) for tests/test_engine_parity.py.
//   BUMPMESH=<stlTexturizer checkout with three and fflate installed> node tests/reference/make_refs.mjs
// Writes tests/reference/refs.json: each case's input mesh, settings and
// a compact digest of BumpMesh's result.
import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const BM = process.env.BUMPMESH;
if (!BM) throw new Error('Set BUMPMESH to a stlTexturizer checkout (npm i three fflate in it).');
const imp = (p) => import(pathToFileURL(path.join(BM, p)).href);
const { textureMesh } = await imp('engine/index.js');
const { loadPreset } = await imp('engine/node.js');
const { prepareMap } = await imp('engine/maps.js');
const HERE = path.dirname(fileURLToPath(import.meta.url));

function box(sx, sy, sz, ox = 0, oy = 0, oz = 0) {
  const P = [];
  const v = (x, y, z) => [x * sx + ox, y * sy + oy, z * sz + oz];
  const q = (a, b, c, d) => P.push(...a, ...b, ...c, ...a, ...c, ...d);
  q(v(0, 0, 0), v(0, 1, 0), v(1, 1, 0), v(1, 0, 0)); q(v(0, 0, 1), v(1, 0, 1), v(1, 1, 1), v(0, 1, 1));
  q(v(0, 0, 0), v(1, 0, 0), v(1, 0, 1), v(0, 0, 1)); q(v(0, 1, 0), v(0, 1, 1), v(1, 1, 1), v(1, 1, 0));
  q(v(0, 0, 0), v(0, 0, 1), v(0, 1, 1), v(0, 1, 0)); q(v(1, 0, 0), v(1, 1, 0), v(1, 1, 1), v(1, 0, 1));
  return Float32Array.from(P);
}
function cylinder(r, h, n) {
  const P = [];
  for (let i = 0; i < n; i++) {
    const a0 = (i / n) * 2 * Math.PI, a1 = ((i + 1) / n) * 2 * Math.PI;
    const p0 = [r * Math.cos(a0), r * Math.sin(a0)], p1 = [r * Math.cos(a1), r * Math.sin(a1)];
    P.push(p0[0], p0[1], 0, p1[0], p1[1], 0, p1[0], p1[1], h, p0[0], p0[1], 0, p1[0], p1[1], h, p0[0], p0[1], h);
    P.push(0, 0, 0, p1[0], p1[1], 0, p0[0], p0[1], 0, 0, 0, h, p0[0], p0[1], h, p1[0], p1[1], h);
  }
  return Float32Array.from(P);
}
function sphere(r, rings, segs) {
  const pt = (i, j) => { const t = (i / rings) * Math.PI, p = (j / segs) * 2 * Math.PI; return [r * Math.sin(t) * Math.cos(p), r * Math.sin(t) * Math.sin(p), r * Math.cos(t) + r]; };
  const P = [];
  for (let i = 0; i < rings; i++) for (let j = 0; j < segs; j++) {
    const a = pt(i, j), b = pt(i + 1, j), c = pt(i + 1, j + 1), d = pt(i, j + 1);
    if (i > 0) P.push(...a, ...b, ...d);
    if (i < rings - 1) P.push(...d, ...b, ...c);
  }
  return Float32Array.from(P);
}
const MESHES = { cube: box(20, 15, 10, -7, 3, 0), cylinder: cylinder(10, 16, 24), sphere: sphere(9, 8, 12) };
const paint = (n, tris) => { const p = new Float32Array(n * 3); for (const t of tris) p.fill(1, t * 3, t * 3 + 3); return p; };

const CASES = [
  ['planarXY', 'cube', [{ texture: 'Knurling', settings: { mappingMode: 0, scaleU: 6 } }], {}],
  ['planarXZ', 'cube', [{ texture: 'Bubbles', settings: { mappingMode: 1, scaleU: 8, rotation: 30 } }], {}],
  ['planarYZ', 'cube', [{ texture: 'Bark', settings: { mappingMode: 2, scaleU: 10, offsetU: 0.25 } }], {}],
  ['cylindrical', 'cylinder', [{ texture: 'Knurling', settings: { mappingMode: 3, scaleU: 5 } }], {}],
  ['spherical', 'sphere', [{ texture: 'Dots', settings: { mappingMode: 4, scaleU: 4 } }], {}],
  ['triplanar', 'sphere', [{ texture: 'Bubbles', settings: { mappingMode: 5, scaleU: 7, textureHeight: 0.8 } }], {}],
  ['cubic', 'cube', [{ texture: 'Dots', settings: { mappingMode: 6, scaleU: 5, invertDisplacement: true } }], {}],
  ['surfaceNormalStamp', 'cube', [{ texture: 'Bubbles', settings: { mappingMode: 7, scaleU: 9, stampOnce: true, stampCenterX: 3, stampCenterY: 10.5, stampCenterZ: 10, stampNormalX: 0, stampNormalY: 0, stampNormalZ: 1 } }], {}],
  ['outlineWrap', 'cylinder', [{ texture: 'Bark', settings: { mappingMode: 8, scaleU: 9 } }], {}],
  ['smoothedSymmetric', 'cube', [{ texture: 'Knurling', settings: { mappingMode: 5, scaleU: 6, textureSmoothing: 3, symmetricDisplacement: true } }], { bottomAngleLimit: 0 }],
  ['maskedExclude', 'cube', [{ texture: 'Knurling', settings: { mappingMode: 5, scaleU: 6 }, mask: { mode: 'exclude', tris: [2, 3, 6, 7] } }], {}],
  ['maskedInclude', 'cylinder', [{ texture: 'Dots', settings: { mappingMode: 3, scaleU: 5 }, mask: { mode: 'include', tris: [0, 2, 4, 6, 8, 10] } }], { maxTriangles: 3000 }],
  ['twoLayers', 'cylinder', [
    { texture: 'Knurling', settings: { mappingMode: 3, scaleU: 6, textureHeight: 0.4 } },
    { texture: 'Dots', settings: { mappingMode: 5, scaleU: 4, textureHeight: 0.3 }, blendAdd: true, mask: { mode: 'include', tris: [0, 1, 2, 3, 4, 5, 6, 7] } }], {}],
  ['twoLayersCover', 'cube', [
    { texture: 'Bark', settings: { mappingMode: 5, scaleU: 10 } },
    { texture: 'Bubbles', settings: { mappingMode: 6, scaleU: 6, boundaryFalloff: 2 }, mask: { mode: 'exclude', tris: [0, 1, 2, 3] } }], {}],
];

const b64 = (a) => Buffer.from(a.buffer, a.byteOffset, a.byteLength).toString('base64');
const out = { generator: 'make_refs.mjs', engine: 'BumpMesh engine/index.js', meshes: {}, cases: [] };
for (const [k, m] of Object.entries(MESHES)) out.meshes[k] = b64(m);
for (const [name, meshName, layers, extra] of CASES) {
  const pos = MESHES[meshName];
  const n = pos.length / 9;
  const settings = { refineLength: 1.2, ...extra };
  const L = layers.map((l) => {
    const pre = loadPreset(l.texture);
    return {
      image: prepareMap(pre.image, l.settings), settings: l.settings, blendAdd: !!l.blendAdd,
      mask: l.mask ? { mode: l.mask.mode, paint: paint(n, l.mask.tris) } : null,
    };
  });
  const t0 = Date.now();
  const r = await textureMesh({ positions: pos.slice(), layers: L, settings });
  const p = r.positions;
  const T = p.length / 9;
  const sample = [];
  const step = Math.max(1, Math.floor(T / 48));
  for (let t = 0; t < T; t += step) sample.push(t);
  const mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity], sum = [0, 0, 0];
  for (let i = 0; i < p.length; i += 3) for (let a = 0; a < 3; a++) { const v = p[i + a]; if (v < mn[a]) mn[a] = v; if (v > mx[a]) mx[a] = v; sum[a] += v; }
  out.cases.push({
    name, mesh: meshName, settings, layers: layers.map(({ texture, settings: s, blendAdd, mask }) => ({ texture, settings: s, blendAdd: !!blendAdd, mask: mask || null })),
    result: {
      triangles: T, inputTriangles: r.inputTriangles, repair: r.repair, warnings: r.warnings,
      min: mn, max: mx, mean: sum.map((s) => s / (p.length / 3)),
      sha256: createHash('sha256').update(Buffer.from(p.buffer, p.byteOffset, p.byteLength)).digest('hex'),
      sample: sample.map((t) => Array.from(p.subarray(t * 9, t * 9 + 9))), sampleIdx: sample,
    },
    ms: Date.now() - t0,
  });
  console.log(name, T, Date.now() - t0, 'ms');
}
writeFileSync(path.join(HERE, 'refs.json'), JSON.stringify(out));
