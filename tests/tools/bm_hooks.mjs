// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2026 the Texturizer for FreeCAD contributors.
// Module resolve hook for tests/tools/bm_js.mjs: BumpMesh (stlTexturizer by
// Stefan Hermann, CNC Kitchen, AGPL-3.0) imports 'three' and 'fflate' as bare
// names, which the browser gets from an import map. In Node they resolve from
// the given roots (directories holding a node_modules folder) when the normal
// lookup beside the importing file fails.
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';

let roots = [];

export function initialize(data) {
  roots = (data && data.roots) || [];
}

export async function resolve(spec, ctx, next) {
  try {
    return await next(spec, ctx);
  } catch (err) {
    if (/^(\.|\/|node:|file:|data:)/.test(spec)) throw err;
    for (const r of roots) {
      try {
        return await next(spec, { ...ctx, parentURL: pathToFileURL(join(r, '_resolve.mjs')).href });
      } catch { /* next root */ }
    }
    throw err;
  }
}
