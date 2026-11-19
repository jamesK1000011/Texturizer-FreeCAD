# SPDX-License-Identifier: AGPL-3.0-only
# Red team 3 (19 Nov 2026), the fixes in Texturizer: ZIP, zlib and PNG caps
# (R3-6), nested path fields checked (R3-10), names from files cleaned
# (R3-11), paths under home as ~/... (R3-12). Plain Python; the tests of
# Agent Link's own checks need it importable (PYTHONPATH) and are skipped
# otherwise.
import io
import json
import os
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from texturizer import draft, guard, handlers, project  # noqa: E402
from texturizer.engine import imageio, limits  # noqa: E402

try:
    import texturizer.fam_texture  # noqa: F401  (Agent Link and FreeCAD beside it)

    HAVE_HUB = True
except ImportError:
    HAVE_HUB = False

HIDDEN = "Grip​‮\U000E0041\x07 layer"


def _zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in members.items():
            z.writestr(n, b)
    return buf.getvalue()


def _png(w, h, idat):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


class Caps(unittest.TestCase):
    """R3-6."""

    def test_a_project_that_is_a_zip_bomb_is_refused(self):
        data = _zip({"settings.json": b"{}", "model.stl": b"\0" * (3 * 1024 * 1024)})
        with self.assertRaises(limits.TooLarge) as c:
            project.read(data)
        self.assertIn("ZIP bomb", str(c.exception))

    def test_too_many_members_or_too_much_unpacked(self):
        data = _zip({f"t{i}.png": b"x" * 100 for i in range(5)})
        with mock.patch.object(limits, "MAX_MEMBERS", 4), self.assertRaises(limits.TooLarge):
            project.read(data)
        with mock.patch.object(limits, "MAX_TOTAL", 300), self.assertRaises(limits.TooLarge):
            project.read(data)
        self.assertEqual(len(project.read(data).files), 5)

    def test_a_member_is_read_to_a_byte_limit(self):
        with zipfile.ZipFile(io.BytesIO(_zip({"a": b"y" * 5000}))) as z:
            self.assertEqual(len(limits.zip_read(z, "a", limit=5000)), 5000)
            with self.assertRaises(limits.TooLarge):
                limits.zip_read(z, "a", limit=4999)

    def test_a_png_header_with_too_many_pixels_is_refused_before_the_decode(self):
        with self.assertRaises(limits.TooLarge):
            imageio.decode_png(_png(100000, 100000, zlib.compress(b"\0" * 10)))

    def test_png_pixel_data_is_inflated_to_the_size_the_header_says(self):
        good = _png(4, 2, zlib.compress(b"\0" + b"\x80" * 4 + b"\0" + b"\x40" * 4))
        self.assertEqual(imageio.decode_png(good).data.shape, (2, 4, 4))
        bomb = _png(4, 2, zlib.compress(b"\0" * (10 * 1024 * 1024)))
        with self.assertRaises(limits.TooLarge):
            imageio.decode_png(bomb)

    def test_read_image_keeps_the_refusal(self):
        p = os.path.join(tempfile.mkdtemp(), "big.png")
        with open(p, "wb") as f:
            f.write(_png(100000, 100000, zlib.compress(b"\0")))
        with self.assertRaises(limits.TooLarge):
            imageio.read_image(p)


class Paths(unittest.TestCase):
    """R3-10: nested path fields, checked by Agent Link's in_file when it is
    there, else by guard's fallback."""

    def setUp(self):
        self.ctx = handlers.Context(out_dir=tempfile.mkdtemp())
        self._lent = (guard.IN_FILE, guard.CHECK)
        guard.IN_FILE = guard.CHECK = None

    def tearDown(self):
        guard.IN_FILE, guard.CHECK = self._lent

    def test_the_fallback(self):
        h = os.path.expanduser("~")
        for p in ("~/.ssh/id_rsa.png", "~/.gnupg/x", "~/.aws/credentials", "~/Library/Keychains/login.keychain-db",
                  "~/Library/Preferences/x.plist", "~/AgentLink/settings.json", "~/AgentLink/usage/x.png", "~/printers.json"):
            self.assertIsNotNone(guard.refusal(p), p)
        for p in ("~/Library/Application Support/FreeCAD/Texturizer/textures/a.png", os.path.join(h, "Desktop", "a.png"),
                  "~/AgentLink/grip.png", "/tmp/a.png"):
            self.assertIsNone(guard.refusal(p), p)
        self.assertIsNotNone(guard.refusal("~/Library/Application Support/FreeCAD/a.stl", write=True))
        self.assertIsNotNone(guard.refusal("~/.hidden/a.stl", write=True))

    def test_texture_file_mask_and_project_are_refused_in_secret_folders(self):
        for fn, a, key in ((handlers.texture_set, {"texture": {"file": "~/.ssh/id_rsa.png"}}, "texture.file"),
                           (handlers.texture_check, {"path": "x.stl", "mask": "~/.aws/m.json"}, "mask"),
                           (handlers.texture_draft, {"project": "~/.gnupg/p.bumpmesh"}, "project")):
            with self.assertRaises(handlers.Refusal, msg=key) as c:
                fn(self.ctx, a)
            self.assertEqual(c.exception.code, "invalid_input")
            self.assertTrue(c.exception.message.startswith(key + ": ~/"), c.exception.message)

    def test_a_nested_export_path_is_checked_as_a_write(self):
        with self.assertRaises(handlers.Refusal) as c:
            handlers._export_with(self.ctx, {"export": {"path": "~/.ssh/a.stl"}}, None, None, None, {})
        self.assertIn("export.path", c.exception.message)

    def test_the_hubs_check_is_used_when_lent(self):
        seen = []
        guard.IN_FILE = lambda session, tool, p, key: seen.append((tool, key)) or p
        with self.assertRaises(Exception):
            handlers.texture_check(self.ctx, {"path": "x.stl", "mask": "m.json"})
        self.assertEqual(seen, [("texture_check", "mask")])

    @unittest.skipUnless(HAVE_HUB, "Agent Link is not importable here")
    def test_with_agent_link(self):
        from agentlink import paths
        from agentlink.protocol import ToolError

        import texturizer.fam_texture as F

        F._lend_paths()
        self.assertIs(guard.IN_FILE, paths.in_file)
        sess = type("S", (), {"out_dir": self.ctx.out_dir})()
        with self.assertRaises(ToolError):
            guard.read_ok(sess, "texture_set", os.path.expanduser("~/.ssh/a.png"), "texture.file", handlers.Refusal)


class Names(unittest.TestCase):
    """R3-11: names from files cleaned before the agent reads them."""

    def test_text(self):
        self.assertEqual(guard.text(HIDDEN), "Grip layer")
        self.assertEqual(len(guard.text("x" * 500, 50)), 51)

    def test_project_layer_and_texture_names(self):
        settings = {"version": 1, "activeLayer": 0, "layers": [{"id": 1, "settings": {}, "activeMapName": "Knurl​ing", "texture": None}],
                    "texturizer": {"version": 1, "layers": [{"name": HIDDEN}]}}
        e = draft.Draft()
        project.load(_zip({"settings.json": json.dumps(settings).encode()}), e, "settings")
        self.assertEqual(e.layers[0]["name"], "Grip layer")
        self.assertEqual(e.layers[0]["texture"], "Knurling")

    def test_a_custom_texture_file_with_hidden_characters_is_refused(self):
        p = os.path.join(tempfile.mkdtemp(), "a‮gnp.png")
        with open(p, "wb") as f:
            f.write(b"x")
        with self.assertRaises(handlers.Refusal) as c:
            handlers._custom_texture(p)
        self.assertIn("Rename the file", c.exception.message)


class HomeShort(unittest.TestCase):
    """R3-12."""

    def test_tilde(self):
        h = os.path.expanduser("~")
        got = guard.tilde({"export": {"path": h + "/AgentLink/a-textured.stl"}, "l": [h + "/x", "/tmp/y", h + "er/z"], "n": 2})
        self.assertEqual(got, {"export": {"path": "~/AgentLink/a-textured.stl"}, "l": ["~/x", "/tmp/y", h + "er/z"], "n": 2})

    def test_a_short_path_is_read_as_the_same_file(self):
        ctx = handlers.Context(out_dir=tempfile.mkdtemp())
        self.assertEqual(handlers._local(ctx, "~/a.png"), os.path.join(os.path.expanduser("~"), "a.png"))

    @unittest.skipUnless(HAVE_HUB, "Agent Link is not importable here")
    def test_answers_and_refusals_at_the_boundary(self):
        from agentlink.protocol import ToolError

        import texturizer.fam_texture as F

        h = os.path.expanduser("~")
        self.assertEqual(F.home_short(lambda a: {"path": h + "/a.stl"})({}), {"path": "~/a.stl"})

        def bad(a):
            raise handlers.Refusal("not_found", f"texture file {h}/t.png: not found")

        with self.assertRaises(ToolError) as c:
            F.home_short(bad)({})
        self.assertEqual((c.exception.code, c.exception.message), ("not_found", "texture file ~/t.png: not found"))


if __name__ == "__main__":
    unittest.main()
