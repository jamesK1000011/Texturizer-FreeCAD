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
        self.assertTrue(t["output"].endswith("-textured.stl"))

    def test_1b_relative_path_and_refusals(self):
        """Hardening 16 Nov: a relative path is written in the output folder
        (where Agent Link's path check judged it), not FreeCAD's working
        folder; a triangle index off the tessellation, and an export of an
        object that is no mesh, are refused with what to do."""
        import os

        import Part

        from _fc import add_body

        if self.session.doc.getObject("Grip") is None and not [o for o in self.session.doc.Objects if o.Label == "Grip"]:
            add_body(self.session, "Grip", Part.makeBox(30, 20, 10))
        err, t = self.call("texture_prepare", {"body": "Grip", "faces": ["Face1"], "path": "sub/grip-for-texture.stl"})
        self.assertFalse(err, t)
        want = os.path.join(self.session.out_dir, "sub", "grip-for-texture.stl")
        self.assertEqual(os.path.realpath(t["stl"]), os.path.realpath(want))
        self.assertTrue(os.path.isfile(want))
        err, t = self.call("texture_draft", {"part": "Grip", "reset": True})
        self.assertFalse(err, t)
        n = t["mesh"]["triangles"]
        err, t = self.call("texture_mask", {"triangles": [n]})
        self.assertTrue(err, t)
        self.assertIn(f"0 to {n - 1}", json.dumps(t))
        err, t = self.call("texture_mask", {"triangles": [0, n - 1]})
        self.assertFalse(err, t)
        self.assertEqual(t["triangles"], 2)
        err, t = self.call("texture_mask", {"clear": True})
        self.assertFalse(err, t)
        err, t = self.call("texture_export", {"format": "stl", "object": "Grip", "path": "no.stl"})
        self.assertTrue(err, t)
        self.assertIn("texture_apply", json.dumps(t))

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

    def test_3_cylindrical_on_a_turned_knob(self):
        """Live (Mac): a knob along Y (its body turned 90°) textured in one
        call with cylindrical mapping came out crumpled, mapped about the
        document's Z. Now the axis is fitted to the included cylindrical
        face and said in the answer, and the result is the upright knob's,
        turned: the same spread of radii about the axis."""
        import numpy as np

        import FreeCAD
        import Part

        from _fc import add_body

        up = add_body(self.session, "KnobZ", Part.makeCylinder(14, 12))
        turned = add_body(self.session, "KnobY", Part.makeCylinder(14, 12))
        turned.Placement = FreeCAD.Placement(FreeCAD.Vector(40, 0, 0), FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), -90))
        self.session.doc.recompute()
        args = {"texture": "knurling", "faces": {"type": "cylinder", "radius": 14}, "mode": "include",
                "settings": {"mappingMode": 3, "textureHeight": 0.6, "scaleU": 4, "refineLength": 0.8}}
        radii = {}
        for body, axis, centre in ((up, 2, (0, 0)), (turned, 1, (40, 0))):
            err, r = self.call("texture_apply", dict(args, body=body.Label))
            self.assertFalse(err, r)
            self.assertEqual(r["cylinder"]["radius"], 14.0)
            self.assertAlmostEqual(abs(r["cylinder"]["axis"][axis]), 1.0, places=6)
            self.assertTrue(r["cylinder"]["from"])
            m = self.session.doc.getObject(r["object"]).Mesh
            P = np.array([[p.x, p.y, p.z] for p in m.Points])
            other = [k for k in range(3) if k != axis]
            rr = np.hypot(P[:, other[0]] - centre[0], P[:, other[1]] - centre[1])
            mid = (P[:, axis] - P[:, axis].min() > 1) & (P[:, axis].max() - P[:, axis] > 1)
            radii[body.Label] = np.percentile(rr[mid & (rr > 10)], [10, 50, 90])
        print("\nradii (10/50/90 %):", {k: v.round(3).tolist() for k, v in radii.items()})
        np.testing.assert_allclose(radii["KnobY"], radii["KnobZ"], atol=0.08)


if __name__ == "__main__":
    unittest.main()
