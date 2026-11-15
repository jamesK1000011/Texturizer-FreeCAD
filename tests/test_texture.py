# SPDX-License-Identifier: AGPL-3.0-only
# The texture tools through Agent Link, on a real FreeCAD (moved from Agent
# Link's test_draft.py with the tools).
import json
import unittest

from _fc import AGENT, caller, server


@unittest.skipUnless(AGENT, "FreeCAD and Agent Link are not both importable here")
class Texture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCAD

        cls.session, srv = server()
        cls.call = staticmethod(caller(srv))
        FreeCAD.newDocument("Textures")
        cls.session.switch(FreeCAD.ActiveDocument)

    def test_1_texture_prepare(self):
        """A block ready for BumpMesh: the top's triangles as the mask, the
        STL closed and outward, the mask's count the STL's."""
        import struct

        import Part

        from _fc import add_body

        add_body(self.session, "Grip", Part.makeBox(30, 20, 10))
        err, t = self.call("texture_prepare", {"body": "Grip", "faces": [{"normal": [0, 0, 1]}], "mode": "include"})
        print("\ntexture:", json.dumps({k: t.get(k) for k in ("triangles", "maskTriangles", "faces", "mode")}))
        self.assertFalse(err, t)
        with open(t["mask"]) as f:
            m = json.load(f)
        self.assertEqual(m["stlTriangles"], t["triangles"])
        with open(t["stl"], "rb") as f:
            data = f.read()
        n = struct.unpack("<I", data[80:84])[0]
        self.assertEqual(n, t["triangles"])
        up = 0
        for k in m["triangles"]:
            nx, ny, nz = struct.unpack("<3f", data[84 + 50 * k:96 + 50 * k])
            up += nz > 0.99
        self.assertEqual(up, len(m["triangles"]))
        # Every triangle's normal points out of the block.
        for k in range(n):
            vals = struct.unpack("<12f", data[84 + 50 * k:132 + 50 * k])
            c = [sum(vals[3 + i::3][:3]) / 3 for i in range(3)]
            nrm = vals[:3]
            out = sum((c[i] - (15, 10, 5)[i]) * nrm[i] for i in range(3))
            self.assertGreater(out, 0, k)
        self.assertEqual(t["next"]["tool"], "bumpmesh_texture_file")

    def test_2_texture_check(self):
        """BumpMesh stood in for: the prepared STL split finely and a pattern
        raised on the masked faces only. The check reads the top textured,
        the rest plain and the mesh closed; a texture that also reaches a
        side is caught as leaked."""
        import numpy as np
        import Part

        from _fc import add_body
        from texturizer.mesh import read_stl

        add_body(self.session, "Knurl", Part.makeBox(30, 20, 10))
        err, t = self.call("texture_prepare", {"body": "Knurl", "faces": [{"normal": [0, 0, 1]}], "mode": "include"})
        self.assertFalse(err, t)
        with open(t["mask"]) as f:
            m = json.load(f)
        T = read_stl(t["stl"])
        masked = np.zeros(len(T), bool)
        masked[m["triangles"]] = True
        for _ in range(4):
            a, b, c = T[:, 0], T[:, 1], T[:, 2]
            ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
            T = np.concatenate([np.stack(x, 1) for x in ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca))])
            masked = np.tile(masked, 4)

        def write(path, raise_on):
            P, inv = np.unique(np.round(T.reshape(-1, 3), 6), axis=0, return_inverse=True)
            inv = inv.reshape(-1, 3)
            ok = np.ones(len(P), bool)
            np.logical_and.at(ok, inv[~raise_on].ravel(), False)
            h = 0.3 * (1 + np.sin(P[:, 0]) * np.sin(P[:, 1])) / 2
            Q = P.copy()
            n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
            n /= np.linalg.norm(n, axis=1)[:, None]
            N = np.zeros_like(P)
            for k in range(3):
                np.add.at(N, inv[:, k], n)
            N /= np.linalg.norm(N, axis=1)[:, None]
            Q[ok] += (h[ok])[:, None] * N[ok]
            import struct

            with open(path, "wb") as f:
                f.write(b" " * 80 + struct.pack("<I", len(inv)))
                for tri in inv:
                    f.write(struct.pack("<12fH", 0, 0, 0, *Q[tri[0]], *Q[tri[1]], *Q[tri[2]], 0))

        good = t["stl"].replace("-for-texture", "-textured")
        write(good, masked)
        err, o = self.call("texture_check", {"body": "Knurl", "path": good})
        print("\ntexture_check:", json.dumps({k: o.get(k) for k in ("verdict", "depth", "texturedFaces", "leaked", "mesh", "note")}))
        self.assertFalse(err, o)
        self.assertEqual(o["verdict"], "ok", o)
        top = next(iter(o["texturedFaces"].values()))
        self.assertGreater(top["moved"], 0.5)
        self.assertLessEqual(top["out"], 0.31)
        side = T[:, :, 1].max(1) < 0.001  # the face at y = 0
        bad = t["stl"].replace("-for-texture", "-leaky")
        write(bad, masked | side)
        err, o = self.call("texture_check", {"body": "Knurl", "path": bad, "mask": t["mask"]})
        self.assertFalse(err, o)
        self.assertEqual(o["verdict"], "check")
        self.assertEqual(len(o["leaked"]["faces"]), 1, o["leaked"])


if __name__ == "__main__":
    unittest.main()
