# SPDX-License-Identifier: AGPL-3.0-only
# Port of engine/writers.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# The engine's output as file bytes: binary STL (normals given or from the
# cross product) and 3MF (core 2015/02, millimetres, vertices welded on the
# 0.1 µm grid, numbers as toFixed(4) without trailing zeros), as js/exporter.js
# writes them. An optional credit goes into the STL header and 3MF metadata.
import io
import struct
import zipfile

import numpy as np

from ._js import F32, fixed_array
from .meshindex import weld


def stl_bytes(positions, normals=None, header=""):
    p = np.asarray(positions, F32).reshape(-1, 3, 3)
    n = len(p)
    if normals is not None:
        nr = np.asarray(normals, F32).reshape(-1, 3, 3)[:, 0]
    else:
        q = p.astype(np.float64)
        c = np.cross(q[:, 1] - q[:, 0], q[:, 2] - q[:, 0])
        ln = np.sqrt((c * c).sum(1))
        nr = (c / np.where(ln == 0, 1, ln)[:, None]).astype(F32)
    rec = np.zeros(n, dtype=[("n", "<f4", 3), ("v", "<f4", 9), ("a", "<u2")])
    rec["n"] = nr
    rec["v"] = p.reshape(-1, 9)
    head = header.encode("ascii", "replace")[:80].ljust(80, b"\0")
    return head + struct.pack("<I", n) + rec.tobytes()


def _fmt_all(vals):
    out = []
    for s in fixed_array(vals, 4).tolist():
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        out.append(s)
    return out


def threemf_bytes(positions, metadata=None):
    p = np.asarray(positions, F32).reshape(-1, 3)
    ids, _, firsts = weld(p.astype(np.float64), 1e4)
    V = p[firsts].astype(np.float64)
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" '
              'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n')
    for k, v in (metadata or {}).items():
        esc = str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        out.write(f'<metadata name="{k}">{esc}</metadata>\n')
    out.write('<resources>\n<object id="1" type="model">\n<mesh>\n<vertices>\n')
    f = _fmt_all(V.reshape(-1))
    out.writelines(f'<vertex x="{f[i]}" y="{f[i + 1]}" z="{f[i + 2]}"/>\n' for i in range(0, len(f), 3))
    out.write('</vertices>\n<triangles>\n')
    out.writelines(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>\n' for a, b, c in ids.reshape(-1, 3).tolist())
    out.write('</triangles>\n</mesh>\n</object>\n</resources>\n<build>\n<item objectid="1"/>\n</build>\n</model>\n')
    types = ('<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
             '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n</Types>\n')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            '<Relationship Id="rel-1" Target="/3D/3dmodel.model" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
            '</Relationships>\n')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", out.getvalue())
    return buf.getvalue()
