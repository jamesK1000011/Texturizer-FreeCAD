# SPDX-License-Identifier: AGPL-3.0-only
# Texturizer's GUI layer with FreeCAD absent: the handlers match the tool
# data (and the family binds them, Agent Link's face queries lent), the local
# runner answers as Agent Link does, the workbench registers under a stub
# FreeCADGui, every Pixmap resolves, the echo declarations name real
# commands, the theme reads a parameter fixture, and no plain module imports
# Agent Link.
import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG = os.path.join(ROOT, "texturizer")
sys.path.insert(0, ROOT)

import texturizer  # noqa: E402
from texturizer import commands, handlers, panel, theme  # noqa: E402

try:
    import agentlink.tooldata  # noqa: F401

    AGENTLINK = os.path.dirname(os.path.dirname(agentlink.tooldata.__file__))
except ImportError:
    AGENTLINK = None

# FreeCAD's own icons the commands name (resolved by FreeCAD at runtime).
FREECAD_ICONS = {"internet-web-browser"}


def tools_json():
    with open(os.path.join(PKG, "data", "tools.json"), encoding="utf-8") as f:
        return json.load(f)["entries"]


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def fam_attrs():
    tree = ast.parse(read(os.path.join(PKG, "fam_texture.py")))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return out


def fresh(code, path=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in [ROOT, path] if p)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    if r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout.strip()


class FakeShape:
    def __init__(self, n):
        self.Faces = [object()] * n


class FakeBody:
    Label = "Grip"
    Name = "Body"

    def __init__(self, n=6):
        self.Shape = FakeShape(n)


class FakeCtx:
    def __init__(self):
        self.out_dir = tempfile.mkdtemp()
        self.doc = None

    def body(self, ref):
        if ref not in (None, "Grip"):
            raise handlers.ToolError("not_found", f"body {ref!r}: none.")
        return FakeBody()


class Handlers(unittest.TestCase):
    def test_parity_with_tool_data(self):
        self.assertEqual(sorted(handlers.HANDLERS), sorted(tools_json()))
        self.assertEqual(sorted(handlers.bound(FakeCtx())), sorted(tools_json()))

    def test_refusal_texts(self):
        with self.assertRaises(handlers.Refusal) as e:
            handlers.num("x", "tolerance")
        self.assertEqual((e.exception.code, e.exception.message), ("invalid_input", "tolerance: a number."))
        with self.assertRaises(handlers.Refusal) as e:
            handlers.face_names(FakeShape(3), ["Face1", "Face7"])
        self.assertEqual(e.exception.code, "not_found")
        with self.assertRaises(handlers.Refusal):
            handlers.face_names(FakeShape(3), {"normal": "+z"})

    def test_face_names(self):
        self.assertEqual(handlers.face_names(FakeShape(4), "Face2"), {"Face2"})
        self.assertEqual(handlers.face_names(FakeShape(4), ["Face1", {"faces": ["Face4"]}]), {"Face1", "Face4"})

    def test_local_runner(self):
        run = handlers.local_runner(FakeCtx())
        ok, out = run("texture_check", {"path": "/nonexistent-textured.stl"})
        self.assertFalse(ok)
        self.assertEqual(out["error"]["code"], "not_found")
        ok, out = run("texture_check", {"body": "Nope", "path": "/x.stl"})
        self.assertEqual((ok, out["error"]["code"]), (False, "not_found"))
        ok, out = run("texture_bogus", {})
        self.assertFalse(ok)
        ok, out = run("texture_check", {"path": "/x.stl", "tolerance": "fine"})
        self.assertEqual(out["error"]["message"], "tolerance: a number.")

    def test_remember(self):
        saved = dict(handlers.LAST)
        try:
            handlers.LAST.update(prepared=[], check=None)
            for i in range(7):
                handlers.remember("texture_prepare", {"stl": f"/t/{i}.stl", "mask": f"/t/{i}-mask.json", "body": "Grip"})
            handlers.remember("texture_prepare", {"stl": "/t/6.stl", "body": "Grip"})
            self.assertEqual([p["stl"] for p in handlers.LAST["prepared"]], ["/t/6.stl", "/t/5.stl", "/t/4.stl", "/t/3.stl", "/t/2.stl"])
            self.assertEqual(commands.default_check_path(), "/t/6-textured.stl")
            handlers.remember("texture_check", {"verdict": "ok"})
            self.assertEqual(handlers.LAST["check"], {"verdict": "ok"})
        finally:
            handlers.LAST.update(saved)

    def test_summary(self):
        self.assertIn("No check yet", panel.summary(None)[0])
        lines = panel.summary({"verdict": "check", "file": "/a/Grip-textured.stl", "body": "Grip", "depth": 0.48,
                               "texturedFaces": {"Face3": {"moved": 0.91, "p95": 0.45}}, "leaked": {"faces": ["Face1"], "points": 4, "worst": 0.2},
                               "bare": ["Face5"], "mesh": {"openEdges": 2, "overSharedEdges": 0}})
        self.assertTrue(lines[0].startswith("CHECK — Grip-textured.stl on Grip"))
        self.assertIn("  Face3: 91% moved, p95 0.45 mm", lines)
        self.assertTrue(any("Leaked onto Face1" in x for x in lines) and any("Bare: Face5" in x for x in lines))

    def test_faces_from_selection(self):
        class O:
            Label = "Grip"

        class S:
            def __init__(self, subs):
                self.Object, self.SubElementNames = O(), subs

        self.assertEqual(commands.faces_from_selection([S(["Edge1"]), S(["Face10", "Face2", "Edge3", "Face2"])]), ("Grip", ["Face2", "Face10"]))
        self.assertEqual(commands.faces_from_selection([]), (None, []))

    @unittest.skipUnless(AGENTLINK, "Agent Link is not beside it")
    def test_family_binds_the_same_tools(self):
        # fam_texture with Agent Link's FreeCAD-bound modules stubbed: the
        # tools it builds are the data's, and the handlers got its face
        # queries, ToolError and num.
        code = r"""
import json, sys, types, tempfile
for n in ("agentlink.facepick", "agentlink.families", "agentlink.families.picks"):
    sys.modules[n] = types.ModuleType(n)
sys.modules["agentlink.facepick"].pick = lambda shape, item, what: [(f, None) for f in item["faces"]]
sys.modules["agentlink.families.picks"].num = lambda v, name: float(v)
import agentlink
agentlink.facepick = sys.modules["agentlink.facepick"]
from texturizer import fam_texture, handlers
from agentlink.protocol import ToolError
class S: out_dir = tempfile.mkdtemp()
tools = fam_texture.tools(S())
print(json.dumps({"tools": [[t.name, t.description, t.schema, t.changes] for t in tools],
                  "lent": [handlers.ToolError is ToolError, handlers.pick_faces is fam_texture.pick_faces,
                           sorted(handlers.pick_faces(None, ["Face1", "Face3"]))]}))
"""
        out = json.loads(fresh(code, AGENTLINK))
        data = tools_json()
        self.assertEqual([t[0] for t in out["tools"]], list(data))
        for name, desc, schema, changes in out["tools"]:
            self.assertEqual((desc, schema, changes), (data[name]["description"], data[name]["inputSchema"], data[name]["changes"]))
        self.assertEqual(out["lent"], [True, True, ["Face1", "Face3"]])


class Echo(unittest.TestCase):
    def test_declarations(self):
        a = fam_attrs()
        self.assertEqual(a["WORKBENCHES"], {"texture_": "TexturizerWorkbench"})
        self.assertEqual(a["AREAS"], {"texture_": "Texture"})
        self.assertEqual(a["COMMANDS"], {"texture_prepare": commands.PREPARE, "texture_check": commands.CHECK})
        for tool in tools_json():
            self.assertIn(tool, a["COMMANDS"])
        self.assertTrue(set(a["COMMANDS"].values()) <= set(commands.NAMES))


STUB = r"""
import json, os, sys, types
calls = {"icons": [], "benches": [], "commands": {}, "toolbars": [], "menus": []}
gui = types.ModuleType("FreeCADGui")
class Workbench:
    def appendToolbar(self, name, cmds): calls["toolbars"].append([name, cmds])
    def appendMenu(self, name, cmds): calls["menus"].append([name, cmds])
gui.Workbench = Workbench
gui.addIconPath = lambda p: calls["icons"].append(p)
gui.addWorkbench = lambda wb: calls["benches"].append(wb)
gui.addCommand = lambda n, c: calls["commands"].__setitem__(n, c.GetResources())
fc = types.ModuleType("FreeCAD")
sys.modules["FreeCADGui"], sys.modules["FreeCAD"] = gui, fc
src = open(os.path.join(sys.argv[1], "InitGui.py")).read()
exec(compile(src, "InitGui.py", "exec"), {"__name__": "__main__"})
wb = calls["benches"][0]
wb.Initialize()
print(json.dumps({"class": type(wb).__name__, "menu": wb.MenuText, "icon": wb.Icon, "tip": wb.ToolTip, "kind": wb.GetClassName(),
                  "icons": calls["icons"], "commands": calls["commands"], "toolbars": calls["toolbars"], "menus": calls["menus"],
                  "agentlink": sorted(m for m in sys.modules if m.startswith("agentlink"))}))
"""


class Workbench(unittest.TestCase):
    def test_initgui_registers_the_bench(self):
        env = dict(os.environ, PYTHONPATH=ROOT)
        r = subprocess.run([sys.executable, "-c", STUB, ROOT], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual((out["class"], out["menu"], out["kind"]), ("TexturizerWorkbench", "Texturizer", "Gui::PythonWorkbench"))
        self.assertEqual(out["icons"], [os.path.join(PKG, "icons")])
        self.assertTrue(os.path.isfile(out["icon"]))
        self.assertIn("CNC Kitchen", out["tip"])
        self.assertEqual(out["toolbars"], [["Texturizer", commands.NAMES]])
        self.assertIn("Texturizer_About", out["menus"][0][1])
        self.assertEqual(sorted(out["commands"]), sorted(commands.NAMES + ["Texturizer_About"]))
        for name, res in out["commands"].items():
            pix = res["Pixmap"]
            self.assertTrue(os.path.isfile(os.path.join(PKG, "icons", pix + ".svg")) or pix in FREECAD_ICONS, pix)
            if name != commands.PANEL:
                self.assertIn("Stefan Hermann (CNC Kitchen)", res["ToolTip"], name)
        self.assertEqual(out["agentlink"], [])

    def test_about_credit(self):
        self.assertIn("Based on BumpMesh by Stefan Hermann (CNC Kitchen)", texturizer.about())
        self.assertIn("AGPL-3.0-only", texturizer.about())


class Theme(unittest.TestCase):
    def test_accents_from_fixture(self):
        self.assertEqual(theme.accents({"ThemeAccentColor1": 0x3D7FD6FF, "ThemeAccentColor2": 0, "ThemeAccentColor3": 0xD2691EFF}), ["#3d7fd6", None, "#d2691e"])

    def test_parameter_group(self):
        class Grp:
            def GetUnsigned(self, name, default):
                return {"ThemeAccentColor2": 0x112233FF}.get(name, default)

        self.assertEqual(theme.accents(Grp()), [None, "#112233", None])

    def test_roles_fall_back(self):
        r = theme.roles(None, {})
        self.assertEqual(r["accent"], r["accent2"])
        r = theme.roles(None, {"ThemeAccentColor1": 0xAABBCCFF})
        self.assertEqual((r["accent"], r["accent3"]), ("#aabbcc", "#aabbcc"))
        self.assertEqual((r["ok"], r["warn"], r["bad"]), (theme.OK, theme.WARN, theme.BAD))
        css = theme.sheet(panel.NAME, r)
        self.assertTrue(all(line.startswith("#" + panel.NAME + " ") for line in css.strip().splitlines()))


class Plain(unittest.TestCase):
    def test_plain_modules_import_no_agent(self):
        code = ("import sys, texturizer, texturizer.handlers, texturizer.commands, texturizer.panel, texturizer.theme, texturizer.mesh; "
                "print(sorted(m for m in sys.modules if m.startswith('agentlink')))")
        self.assertEqual(fresh(code, AGENTLINK), "[]")
        for name in ("handlers.py", "commands.py", "panel.py", "theme.py", "mesh.py", "__init__.py"):
            src = read(os.path.join(PKG, name))
            self.assertNotIn("import agentlink", src, name)
            self.assertNotIn("from agentlink", src, name)

    def test_not_hosted_alone(self):
        self.assertFalse(panel.hosted())

    @unittest.skipUnless(AGENTLINK, "Agent Link is not beside it")
    def test_hosted_tab(self):
        code = r"""
import sys, types
for n in ("agentlink.facepick", "agentlink.families", "agentlink.families.picks"):
    sys.modules[n] = types.ModuleType(n)
import texturizer, texturizer.agentlink_families
from agentlink import hooks
print([t["title"] for t in hooks.TABS if t["order"] == 60], texturizer._hosted)
"""
        self.assertEqual(fresh(code, AGENTLINK), "['Texture'] True")


if __name__ == "__main__":
    unittest.main()
