// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2026 the Texturizer for FreeCAD contributors.
// Runs the mask brush of BumpMesh (stlTexturizer by Stefan Hermann, CNC
// Kitchen, AGPL-3.0, github.com/CNCKitchen/stlTexturizer) for
// tests/test_paint.py's comparison with texturizer/paint.py.
//
//   node tests/tools/paint_js.mjs BUMPMESH_DIR CASES.json   -> JSON on stdout
//
// js/paintTree.js and js/exclusion.js are imported as they are. main.js's
// _paintSingleHit and _brushEdgeLimit are cut out of its source text,
// unchanged, and run with its module state as plain variables; the stroke
// loop is paintAt's (the previous point of the drag, dropped on a miss).
// Strokes split the tree as BumpMesh's do (Precision), so a snapshot is the
// flattened mesh with the layer's paint on it.
//
// CASES.json: [{positions, ops: [{kind: 'stroke', hits: [{point, triangle,
//   view} | null], circle, size, hardness (%), precision, erase} | {kind:
//   'fill', seed, maxAngle, erase} | {kind: 'snapshot'} | {kind: 'save'} |
//   {kind: 'load', json}]}]
// Output: per case, per snapshot op {hard: [0|1 per flattened triangle],
// paint: [per corner], positions, parent} (flatten + flatPaint of slot 0);
// per save op {json} (PaintTree.toJSON of serialize({leafCov: true}),
// main.js's paint.json); load replays a paint.json as importProject.
import { readFileSync } from 'node:fs';
import { register } from 'node:module';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { join, delimiter, dirname } from 'node:path';

const [src, casesPath] = process.argv.slice(2);
// 'three' (exclusion.js imports it) as bm_js.mjs finds it: BumpMesh's own
// node_modules, $BM_NODE_ROOTS, last the shims in js_shims/.
const roots = [src, join(src, 'mcp'), ...(process.env.BM_NODE_ROOTS || '').split(delimiter).filter(Boolean),
  join(dirname(fileURLToPath(import.meta.url)), 'js_shims')];
register('./bm_hooks.mjs', import.meta.url, { data: { roots } });
const imp = (p) => import(pathToFileURL(join(src, p)).href);
const THREE = await import('three');
const { PaintTree } = await imp('js/paintTree.js');
const { buildAdjacency, bucketFill } = await imp('js/exclusion.js');
const MAIN = readFileSync(join(src, 'js', 'main.js'), 'utf8');

function cut(name) {
  const at = MAIN.indexOf(`function ${name}(`);
  if (at < 0) throw new Error(`main.js: no ${name}`);
  let i = MAIN.indexOf('{', at), depth = 0;
  for (; i < MAIN.length; i++) {
    if (MAIN[i] === '{') depth++;
    else if (MAIN[i] === '}' && --depth === 0) break;
  }
  return MAIN.slice(at, i + 1);
}

// main.js's module state the two functions read, set per op.
const make = new Function('ctx', `
  let paintTree, brushIsRadius, brushRadius, brushHardness, brushPrecision, eraseMode, viewDir;
  const _activeSlot = () => 0;
  const _baseFaceOf = (faceIndex) => faceIndex;
  const _viewDirFor = () => viewDir;
  ${cut('_brushEdgeLimit')}
  ${cut('_paintSingleHit')}
  return (s) => {
    paintTree = s.tree; brushIsRadius = s.circle; brushRadius = s.radius; brushHardness = s.hardness;
    brushPrecision = s.precision; eraseMode = s.erase; viewDir = s.view;
    _paintSingleHit(s.hit, { geometry: null }, s.from);
  };`);
const paintSingleHit = make();

const cases = JSON.parse(readFileSync(casesPath, 'utf8'));
const out = cases.map((c) => {
  const positions = Float32Array.from(c.positions);
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  const adj = buildAdjacency(geo);
  const tree = new PaintTree({ positions, vertId: adj.vertId, vertCount: adj.vertCount,
                               adjacency: adj.adjacency, faceNormals: adj.faceNormals });
  tree.addLayer(1);
  const snaps = [];
  for (const op of c.ops) {
    if (op.kind === 'snapshot') {
      const flat = tree.flatten();
      const { paint, hard } = tree.flatPaint(0, flat);
      snaps.push({ hard: Array.from(hard), paint: Array.from(paint), positions: Array.from(flat.positions),
                   parent: Array.from(flat.faceParentId) });
    } else if (op.kind === 'save') {
      snaps.push({ json: PaintTree.toJSON(tree.serialize({ leafCov: true })) });
    } else if (op.kind === 'load') {
      if (!tree.deserialize(PaintTree.fromJSON(op.json))) throw new Error('paint.json not restored');
    } else if (op.kind === 'fill') {
      tree.paintFaces(0, bucketFill(op.seed, adj.adjacency, op.maxAngle), !!op.erase);
    } else {
      const h = op.hardness / 100;
      let prev = null;
      for (const hit of op.hits) {
        if (!hit) { prev = null; continue; }
        const v = { x: hit.view[0], y: hit.view[1], z: hit.view[2] };
        const p = { x: hit.point[0], y: hit.point[1], z: hit.point[2] };
        paintSingleHit({ tree, circle: op.circle, radius: op.size / 2, hardness: h, precision: op.precision !== false,
                         erase: !!op.erase, view: v, hit: { point: p, faceIndex: hit.triangle }, from: prev });
        prev = p;
      }
    }
  }
  return snaps;
});
process.stdout.write(JSON.stringify(out));
