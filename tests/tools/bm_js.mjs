// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2026 the Texturizer for FreeCAD contributors.
// Runs BumpMesh's own project and projection code (stlTexturizer by Stefan
// Hermann, CNC Kitchen, AGPL-3.0, github.com/CNCKitchen/stlTexturizer) for
// tests/test_project.py and tests/test_projection_extras.py.
//
//   node tests/tools/bm_js.mjs BUMPMESH_DIR COMMAND SPEC.json   -> JSON on stdout
//
// js/main.js is a browser module (DOM at top level), so it cannot be
// imported. Instead the functions and constants named below are cut out of
// its source text, unchanged, and run together with stubs for the DOM they
// touch; the ES modules they call (paintTree.js, mapping.js, exclusion.js,
// fflate, three) are imported for real. 'three' and 'fflate' resolve from
// BumpMesh's own node_modules, $BM_NODE_ROOTS (path list of directories
// holding node_modules), and last from the shims in js_shims/ (for machines
// with no npm registry; see their headers).
//
// Commands:
//   keys     -> {PERSISTED_KEYS, LAYER_KEYS, GLOBAL_KEYS, PROJECT_VERSION, settings}
//   export   SPEC {settings, layers: [{id, settings, mapName, png (base64) | null,
//            includeOnly, visible, blendAdd, paint: [faces]}], activeLayer,
//            positions (flat, original pose), poseRotation, includeModel,
//            includeTexture, out} -> runs the Save Project click handler and
//            writes the .bumpmesh it downloads to `out`
//   import   SPEC {file, mode: 'all' | 'settings', current: {layers, activeLayer,
//            positions, paint}} -> the layers, maps and paint importProject
//            restores (applySettingsSnapshot's DOM part is replaced by
//            assigning the snapshot's PERSISTED_KEYS to `settings`)
//   autofit  SPEC [{positions}] -> autoFitCylinderAxis's result per case
//   stamp    SPEC [{positions, settings, point, normal, pickAxis, texSize}]
//            -> _placeStampAt's stamp fields per case
import { readFileSync, writeFileSync } from 'node:fs';
import { register } from 'node:module';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { join, delimiter, dirname } from 'node:path';

const [src, cmd, specPath] = process.argv.slice(2);
const roots = [src, join(src, 'mcp'), ...(process.env.BM_NODE_ROOTS || '').split(delimiter).filter(Boolean),
  join(dirname(fileURLToPath(import.meta.url)), 'js_shims')];   // last: the test shims
register('./bm_hooks.mjs', import.meta.url, { data: { roots } });

const imp = (p) => import(pathToFileURL(join(src, p)).href);
const THREE = await import('three');
const { STLLoader } = await import('three/addons/loaders/STLLoader.js');
const fflate = await import('fflate');
const { PaintTree } = await imp('js/paintTree.js');
const { buildAdjacency } = await imp('js/exclusion.js');
const mapping = await imp('js/mapping.js');
// previewMaterial.js builds shaders at load; only its MAX_LAYERS is needed.
const MAX_LAYERS = Number(/export const MAX_LAYERS = (\d+);/.exec(readFileSync(join(src, 'js', 'previewMaterial.js'), 'utf8'))[1]);

const MAIN = readFileSync(join(src, 'js', 'main.js'), 'utf8');

// ── cutting main.js ───────────────────────────────────────────────────────────
// A small skipper for strings, template literals, comments and regex literals,
// enough to match brackets in main.js.
const REGEX_PREV = new Set(['(', ',', '=', ':', '[', '!', '&', '|', '?', '{', '}', ';', '+', '-', '*', '%', '<', '>', '~', '^', '']);
function skip(s, i) {
  const c = s[i];
  if (c === '/' && s[i + 1] === '/') { const j = s.indexOf('\n', i); return j < 0 ? s.length : j; }
  if (c === '/' && s[i + 1] === '*') return s.indexOf('*/', i + 2) + 2;
  if (c === '"' || c === "'") {
    for (let j = i + 1; j < s.length; j++) { if (s[j] === '\\') { j++; continue; } if (s[j] === c) return j + 1; }
  }
  if (c === '`') {
    for (let j = i + 1; j < s.length; j++) {
      if (s[j] === '\\') { j++; continue; }
      if (s[j] === '`') return j + 1;
      if (s[j] === '$' && s[j + 1] === '{') j = match(s, j + 1, '{', '}') - 1;
    }
  }
  if (c === '/') {
    let k = i - 1;
    while (k >= 0 && /\s/.test(s[k])) k--;
    const prev = k >= 0 ? s[k] : '';
    const word = /[A-Za-z_$]/.test(prev) ? s.slice(0, k + 1).match(/[A-Za-z_$]+$/)[0] : null;
    if (REGEX_PREV.has(prev) || word === 'return' || word === 'typeof') {
      let cls = false;
      for (let j = i + 1; j < s.length; j++) {
        if (s[j] === '\\') { j++; continue; }
        if (s[j] === '[') cls = true; else if (s[j] === ']') cls = false;
        else if (s[j] === '/' && !cls) { j++; while (/[a-z]/.test(s[j])) j++; return j; }
      }
    }
  }
  return -1;
}
/** Index after the bracket matching the one at i. */
function match(s, i, open, close) {
  let depth = 0;
  for (let j = i; j < s.length;) {
    const k = skip(s, j);
    if (k >= 0) { j = k; continue; }
    if (s[j] === open) depth++;
    else if (s[j] === close && --depth === 0) return j + 1;
    j++;
  }
  throw new Error('unbalanced ' + open);
}
function cutFunction(name) {
  const m = new RegExp(`^(async )?function ${name}\\(`, 'm').exec(MAIN);
  if (!m) throw new Error('main.js has no function ' + name);
  const p = match(MAIN, m.index + m[0].length - 1, '(', ')');
  const b = MAIN.indexOf('{', p);
  return MAIN.slice(m.index, match(MAIN, b, '{', '}'));
}
function cutConst(name) {
  const m = new RegExp(`^(const|let) ${name}\\s*=`, 'm').exec(MAIN);
  if (!m) throw new Error('main.js has no const ' + name);
  let j = m.index + m[0].length, depth = 0;
  for (;;) {
    const k = skip(MAIN, j);
    if (k >= 0) { j = k; continue; }
    const c = MAIN[j];
    if ('([{'.includes(c)) depth++;
    else if (')]}'.includes(c)) depth--;
    else if (c === ';' && depth === 0) return MAIN.slice(m.index, j + 1);
    j++;
  }
}
/** The body of an `addEventListener('click', async () => {...})` handler. */
function cutHandler(head) {
  const i = MAIN.indexOf(head);
  if (i < 0) throw new Error('main.js has no ' + head);
  const b = MAIN.indexOf('{', i + head.length - 1);
  return MAIN.slice(b, match(MAIN, b, '{', '}'));
}

const CONSTS = ['PROJECT_VERSION', 'PROJECT_MAX_IMPORT', 'LAYER_KEYS', 'PERSISTED_KEYS', 'GLOBAL_KEYS', 'settings',
  'STAMP_MODES', 'STAMP_NORMAL_RADIUS'];
const FUNCS = ['_globalSettingsSnapshot', 'getSettingsSnapshot', '_layerSettingsSnapshot', '_layerSettingsFrom',
  '_layersFromSnapshot', '_newLayer', '_migrateSnapshotScaleToMm', '_legacyMaskOf', '_geometryToBinarySTL',
  '_layerMapEntry', '_importLegacyMask', '_restoreLegacyProjectMask', '_importLayers', '_applyImportedTexture',
  '_syncTreeLayers', '_activeSlot', '_slotOf', 'autoFitCylinderAxis', 'getEffectiveCylinderCenter',
  '_placeStampAt', '_moveStampTo', '_moveNormalStamp', '_stampCursorSurface', '_stampSurfaceUnder',
  '_stampSurfaceAt', '_stampNormalRadius', '_stampFrameFor'];
const VARS = ['layers', 'activeLayer', '_layerSeq', 'activeMapEntry', 'selectionMode', 'paintTree', 'currentGeometry',
  'currentBounds', 'triangleFaceNormals', 'currentPoseRot', 'currentPoseTrans', '_lastCustomMap', 'currentStlName',
  'maskModeChosen', '_stampGrab', '_stampDragging', '_stampLastSurface'];
const STUBS = ['alert', 't', '_downloadBlob', 'exportDialog', 'exportModelChk', 'exportTextureChk', 'mappingSelect',
  'sessionStorage', 'loadCustomTexture', '_loadMapEntry', '_materialiseActiveLayer', '_renderLayerStrip',
  'updatePreview', 'setSelectionMode', 'updateMaskModeButtons', 'refreshExclusionOverlay', '_useCustomMap',
  '_selectPresetByName', '_stampRaycast', '_stampCursorRay', 'getEffectiveMapEntry', '_effectiveMapFor'];
const DEPS = { THREE, PaintTree, MAX_LAYERS, ...fflate, ...mapping };

function sandbox(stubs) {
  const code = [
    `const { ${STUBS.join(', ')} } = __stubs;`,
    ...CONSTS.map(cutConst),
    `let ${VARS.map(v => v + ' = null').join(', ')};`,
    'layers = []; activeLayer = 0; _layerSeq = 0; selectionMode = false; maskModeChosen = false; _stampDragging = false;',
    ...FUNCS.map(cutFunction),
    `async function __exportGo() ${cutHandler("exportGoBtn.addEventListener('click', async () => {")}`,
    `return { ${[...FUNCS, '__exportGo'].join(', ')}, ${CONSTS.join(', ')},`,
    `  get: (k) => ({ ${VARS.join(', ')} })[k],`,
    `  set: (o) => { ${VARS.map(v => `if ('${v}' in o) ${v} = o.${v};`).join(' ')} } };`,
  ].join('\n');
  const names = Object.keys(DEPS).filter(k => /^[A-Za-z_$][\w$]*$/.test(k) && !STUBS.includes(k));
  return new Function('__stubs', ...names, code)(stubs, ...names.map(k => DEPS[k]));
}

// ── helpers ───────────────────────────────────────────────────────────────────
const out = (o) => process.stdout.write(JSON.stringify(o));
const spec = specPath ? JSON.parse(readFileSync(specPath, 'utf8')) : null;
const v3 = (a) => new THREE.Vector3(a[0], a[1], a[2]);
function geometry(flat) {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(Float32Array.from(flat), 3));
  g.computeVertexNormals();
  return g;
}
function bounds(g) {
  g.computeBoundingBox();
  const b = g.boundingBox, size = new THREE.Vector3(), center = new THREE.Vector3();
  b.getSize(size); b.getCenter(center);
  return { min: b.min.clone(), max: b.max.clone(), size, center };
}
function treeFor(g) {
  const a = buildAdjacency(g);
  return { tree: new PaintTree({ positions: g.attributes.position.array, vertId: a.vertId, vertCount: a.vertCount, adjacency: a.adjacency, faceNormals: a.faceNormals }), adj: a };
}
const canvasOf = (bytes) => ({ width: 1, height: 1, toBlob: (cb) => cb(new Blob([bytes], { type: 'image/png' })) });
const painted = (tree, slot) => {
  const r = [];
  if (!tree || slot < 0) return r;
  const st = tree.layers[slot].state;
  for (let f = 0; f < tree.baseTriCount; f++) if (st[f]) r.push(f);
  return r;
};
const alerts = [];
const baseStubs = {
  alert: (m) => alerts.push(String(m)), t: (k, o) => k + (o ? ' ' + JSON.stringify(o) : ''),
  _downloadBlob: null, exportDialog: { classList: { add() {}, toggle() {} } },
  exportModelChk: { checked: true }, exportTextureChk: { checked: true },
  mappingSelect: null, sessionStorage: { getItem: () => null },
  loadCustomTexture: async (file) => {
    const b = new Uint8Array(await file.arrayBuffer());
    if (!(b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47)) throw new Error('not a PNG');
    return { name: file.name, bytes: b.length, width: 1, height: 1 };
  },
  _loadMapEntry: async (name) => (name ? { name } : null),
  _materialiseActiveLayer() {}, _renderLayerStrip() {}, updatePreview() {}, updateMaskModeButtons() {},
  refreshExclusionOverlay() {}, _stampRaycast: null, _stampCursorRay: null,
  getEffectiveMapEntry: () => null, _effectiveMapFor: () => null,
};

// ── commands ──────────────────────────────────────────────────────────────────
if (cmd === 'keys') {
  const S = sandbox(baseStubs);
  out({ PERSISTED_KEYS: S.PERSISTED_KEYS, LAYER_KEYS: S.LAYER_KEYS, GLOBAL_KEYS: S.GLOBAL_KEYS,
        PROJECT_VERSION: S.PROJECT_VERSION, settings: S.settings });
} else if (cmd === 'export') {
  let blob = null, fname = null;
  const S = sandbox({ ...baseStubs, _downloadBlob: (b, n) => { blob = b; fname = n; } ,
    exportModelChk: { checked: !!spec.includeModel }, exportTextureChk: { checked: !!spec.includeTexture } });
  const entries = spec.layers.map((L) => L.png
    ? { name: L.mapName, isCustom: true, fullCanvas: canvasOf(Buffer.from(L.png, 'base64')) }
    : (L.mapName ? { name: L.mapName } : null));
  const recs = spec.layers.map((L, i) => ({ id: L.id, settings: { ...L.settings }, mapName: L.mapName, customId: null,
    mapEntry: entries[i], includeOnly: !!L.includeOnly, maskModeChosen: !!L.includeOnly,
    visible: L.visible !== false, blendAdd: !!L.blendAdd }));
  const a = spec.activeLayer || 0;
  Object.assign(S.settings, spec.settings || {}, recs[a].settings);   // the live settings are the active layer's
  const q = spec.poseRotation ? new THREE.Quaternion().fromArray(spec.poseRotation) : new THREE.Quaternion();
  let geo = null, tree = null;
  if (spec.positions) {
    geo = geometry(spec.positions);
    geo.applyQuaternion(q);   // the live geometry is in the rotated pose; the STL is written in the original one
    tree = treeFor(geo).tree;
    for (const L of spec.layers) tree.paintFaces(tree.addLayer(L.id), L.paint || []);
  }
  S.set({ layers: recs, activeLayer: a, activeMapEntry: entries[a], selectionMode: recs[a].includeOnly,
    paintTree: tree, currentGeometry: geo, currentPoseRot: q, currentPoseTrans: new THREE.Vector3(),
    _lastCustomMap: entries.find(e => e && e.isCustom) || null, currentStlName: 'part' });
  await S.__exportGo();
  if (!blob) { out({ error: alerts.join('; ') || 'nothing exported' }); process.exit(0); }
  writeFileSync(spec.out, new Uint8Array(await blob.arrayBuffer()));
  out({ file: fname, alerts });
} else if (cmd === 'import') {
  const sel = [];
  const S = sandbox({ ...baseStubs,
    setSelectionMode: (on) => S.set({ selectionMode: on }),
    _useCustomMap: (entry) => sel.push({ custom: true, name: entry.name }),
    _selectPresetByName: (name) => { sel.push({ custom: false, name }); return true; } });
  const bytes = new Uint8Array(readFileSync(spec.file));
  const unzipped = fflate.unzipSync(bytes);
  const data = unzipped['settings.json'] ? JSON.parse(fflate.strFromU8(unzipped['settings.json'])) : null;
  const loadMode = unzipped['model.stl'] ? (spec.mode || 'all') : 'settings';
  const cur = spec.current || {};
  let tree = null;
  if (cur.positions) {   // the model already loaded (Settings only keeps it and its paint)
    const g = geometry(cur.positions);
    tree = treeFor(g).tree;
    const recs = (cur.layers || [{}]).map((L) => S._newLayer(L.id != null ? { id: L.id } : {}));
    S.set({ layers: recs, activeLayer: cur.activeLayer || 0, paintTree: tree, currentGeometry: g, currentBounds: bounds(g) });
    S._syncTreeLayers();
    (cur.layers || []).forEach((L, i) => { if (L.paint) tree.paintFaces(S._slotOf(i), L.paint); });
  }
  let triCount = null;
  if (loadMode === 'all') {   // handleModelFile: a fresh model, one layer, an empty tree
    const g = new STLLoader().parse(unzipped['model.stl'].buffer.slice(unzipped['model.stl'].byteOffset,
      unzipped['model.stl'].byteOffset + unzipped['model.stl'].byteLength));
    g.computeVertexNormals();
    tree = treeFor(g).tree;
    triCount = tree.baseTriCount;
    S.set({ layers: [S._newLayer()], activeLayer: 0, paintTree: tree, currentGeometry: g, currentBounds: bounds(g) });
    S._syncTreeLayers();
  }
  if (data) {   // applySettingsSnapshot, without its DOM
    const snap = S._migrateSnapshotScaleToMm(data);
    for (const k of S.PERSISTED_KEYS) if (k in snap) S.settings[k] = snap[k];
  }
  if (data && Array.isArray(data.layers) && data.layers.length) {
    await S._importLayers(unzipped, data, loadMode === 'all');
  } else {
    const L0 = S.get('layers'), a0 = S.get('activeLayer');
    const activeId = L0[a0]?.id;
    S.set({ layers: [S._newLayer(loadMode === 'settings' && activeId != null ? { id: activeId } : {})], activeLayer: 0 });
    S._syncTreeLayers();
    if (loadMode === 'all') S._restoreLegacyProjectMask(unzipped);
    await S._applyImportedTexture(unzipped, data);
  }
  const layers = S.get('layers'), active = S.get('activeLayer');
  out({
    loadMode, triCount, alerts, selected: sel, activeLayer: active,
    poseRotation: data && data.poseRotation || null,
    settings: Object.fromEntries(S.GLOBAL_KEYS.map(k => [k, S.settings[k]])),
    layers: layers.map((L, i) => ({
      id: L.id, settings: L.settings, mapName: L.mapName, mapEntry: L.mapEntry ? { name: L.mapEntry.name, custom: !!L.mapEntry.isCustom } : null,
      includeOnly: i === active ? (L.includeOnly || S.get('selectionMode')) : L.includeOnly,
      visible: L.visible, blendAdd: L.blendAdd, painted: painted(tree, S._slotOf(i)) })),
  });
} else if (cmd === 'autofit') {
  const S = sandbox(baseStubs);
  out(spec.map((c) => {
    const g = geometry(c.positions);
    S.set({ currentGeometry: g, currentBounds: bounds(g), triangleFaceNormals: buildAdjacency(g).faceNormals });
    Object.assign(S.settings, { cylinderCenterX: null, cylinderCenterY: null, cylinderRadius: null });
    const ok = S.autoFitCylinderAxis();
    return ok ? { cylinderCenterX: S.settings.cylinderCenterX, cylinderCenterY: S.settings.cylinderCenterY, cylinderRadius: S.settings.cylinderRadius } : null;
  }));
} else if (cmd === 'stamp') {
  let hit = null, ray = null, tex = null;
  const sel = { value: '0', dispatchEvent() { S2.settings.mappingMode = parseInt(this.value, 10); } };
  const S2 = sandbox({ ...baseStubs, _stampRaycast: () => hit, _stampCursorRay: () => ray,
    getEffectiveMapEntry: () => tex, mappingSelect: sel });
  const KEYS = ['mappingMode', 'stampCenterX', 'stampCenterY', 'stampCenterZ', 'stampSide', 'stampNormalX', 'stampNormalY', 'stampNormalZ'];
  out(spec.map((c) => {
    const g = geometry(c.positions);
    for (const k of KEYS) S2.settings[k] = null;
    Object.assign(S2.settings, c.settings);
    S2.set({ currentGeometry: g, currentBounds: bounds(g), layers: [S2._newLayer()], activeLayer: 0,
      _stampGrab: null, _stampDragging: false, _stampLastSurface: null });
    tex = c.texSize ? { width: c.texSize[0], height: c.texSize[1] } : null;
    const p = v3(c.point), n = v3(c.normal).normalize();
    hit = { p, n, onBase: p.clone() };
    ray = { o: p.clone().addScaledVector(n, 10), d: n.clone().negate() };
    const ok = S2._placeStampAt({}, c.pickAxis !== false);
    return ok ? Object.fromEntries(KEYS.map(k => [k, S2.settings[k]])) : null;
  }));
} else {
  throw new Error('unknown command ' + cmd);
}
