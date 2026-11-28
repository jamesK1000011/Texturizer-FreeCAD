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
FREECAD_ICONS = set()


def card_text(e):
    """The words of a data entry: its card's lines joined, as Agent Link
    reads them (agentlink.tooldata.card_text); the same reading here when
    Agent Link is not importable."""
    try:
        from agentlink.tooldata import card_text as hub
        return hub(e)
    except ImportError:
        pass
    c = e.get("card")
    if not isinstance(c, dict):
        return e.get("description", "")
    out = [c.get("does", "")]
    for k, n in (c.get("inputNotes") or {}).items():
        out += [f"{k}: {n.get('means', '')}"] + [f"{k}: {f}" for f in n.get("forms") or []]
    out += [x.get("line", "") for x in c.get("links") or []] + list(c.get("effects") or [])
    out += [f"{k}: {r.get('means', '')}" for k, r in (c.get("returns") or {}).items()]
    out += list(c.get("refusalNotes") or []) + list(c.get("hints") or [])
    return " ".join(x for x in out if x)


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

    def test_local_paths(self):
        """A relative path is in the output folder, ~ is the home folder, an
        absolute one stays (hardening 16 Nov)."""
        ctx = FakeCtx()
        self.assertEqual(handlers._local(ctx, "a/b.stl"), os.path.join(ctx.out_dir, "a", "b.stl"))
        self.assertEqual(handlers._local(ctx, "~/b.stl"), os.path.expanduser("~/b.stl"))
        self.assertEqual(handlers._local(ctx, os.path.join(ctx.out_dir, "c.stl")), os.path.join(ctx.out_dir, "c.stl"))
        self.assertIsNone(handlers._local(ctx, None))

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
            self.assertEqual((desc, schema, changes), (card_text(data[name]), data[name]["inputSchema"], data[name]["changes"]))
        self.assertEqual(out["lent"], [True, True, ["Face1", "Face3"]])


class Echo(unittest.TestCase):
    def test_declarations(self):
        a = fam_attrs()
        self.assertEqual(a["WORKBENCHES"], {"texture_": "TexturizerWorkbench"})
        self.assertEqual(a["AREAS"], {"texture_": "Texture"})
        draft_tools = ("texture_draft", "texture_set", "texture_mask", "texture_layer", "texture_preview")
        self.assertEqual({t: a["COMMANDS"][t] for t in draft_tools}, {t: commands.TEXTURE for t in draft_tools})
        # export: the toolbar's Export 3MF (command_for picks Save Project
        # for bumpmesh and FreeCAD's File > Export for an STL)
        self.assertEqual((a["COMMANDS"]["texture_apply"], a["COMMANDS"]["texture_export"]), (commands.ADD, "Texturizer_Export3MF"))
        self.assertEqual((a["COMMANDS"]["texture_prepare"], a["COMMANDS"]["texture_check"]), (commands.PREPARE, commands.CHECK))
        self.assertEqual(set(a["SHOWN"]), {"texture_preview", "texture_check"})
        self.assertEqual(set(a["NO_BUTTON"]), {"texture_list"})
        # every tool has a button or says it has none
        self.assertEqual(set(a["COMMANDS"]) | set(a["NO_BUTTON"]) | {"texture_settings"}, set(tools_json()))
        self.assertTrue(set(a["COMMANDS"].values()) <= set(commands.NAMES))
        # the reads that open the sidebar bring up the bench (and so light a
        # button: the echo lights one only for a call that switches it)
        # (and the bench's own buttons: export, prepare, check)
        self.assertEqual(set(a["OPENS"]), set(draft_tools) | {"texture_export", "texture_prepare", "texture_check"})
        self.assertEqual(set(a["IN_PLACE"]), set(draft_tools))
        self.assertTrue(set(a["PHRASES"]) >= set(draft_tools) | {"texture_export", "texture_prepare", "texture_apply"})
        self.assertEqual(set(a["SELECTS"]) - set(tools_json()), set())
        self.assertTrue(set(draft_tools) <= set(a["SELECTS"]))
        self.assertEqual(a["OUT_MORE"], {"texture_apply": ("object",)})

    def test_command_for(self):
        code = r"""
import json, sys, types
for n in ("agentlink", "agentlink.facepick", "agentlink.hooks", "agentlink.families", "agentlink.families.picks", "agentlink.protocol"):
    sys.modules[n] = types.ModuleType(n)
sys.modules["agentlink.protocol"].ToolError = Exception
from texturizer import fam_texture as f
calls = [("texture_export", {"format": "3mf"}), ("texture_export", {"format": "bumpmesh"}), ("texture_export", {"format": "stl"}),
         ("texture_draft", {"reset": True}), ("texture_draft", {"project": "a.bumpmesh"}), ("texture_draft", {"part": "Grip"}),
         ("texture_set", {"texture": "hexagon"}), ("texture_set", {"texture": "hexagon", "settings": {"amplitude": 1}}),
         ("texture_preview", {"turntable": True}), ("texture_preview", {}), ("texture_check", {})]
print(json.dumps([f.command_for(n, a) for n, a in calls]))
"""
        got = json.loads(fresh(code))
        self.assertEqual(got, ["Texturizer_Export3MF", "Texturizer_SaveProject", "Std_Export", "Texturizer_Reset",
                               "Texturizer_LoadProject", "Texturizer_Texture", "Texturizer_Gallery", None,
                               "Texturizer_Turntable", None, None])
        names = set(commands.NAMES) | {"Std_Export"}
        self.assertTrue(set(x for x in got if x) <= names)

    @unittest.skipUnless(AGENTLINK, "Agent Link is not beside it")
    def test_phrases_fill_from_the_answers(self):
        # The words Agent Link's Activity tab and caption give each call,
        # from fam_texture.PHRASES with its own template filler.
        code = r"""
import json, sys, types
for n in ("agentlink.facepick", "agentlink.families", "agentlink.families.picks"):
    sys.modules[n] = types.ModuleType(n)
import agentlink
agentlink.facepick = sys.modules["agentlink.facepick"]
from agentlink import activity
from texturizer import fam_texture
activity.declare(fam_texture)
rows = [
 ("texture_preview", {"cancel": True}, {"cancelled": True}),
 ("texture_preview", {}, {"part": "Grip", "triangles": 120000, "ms": 900}),
 ("texture_preview", {"turntable": True}, {"turntable": {"spinning": True}}),
 ("texture_preview", {"turntable": 30}, {"turntable": {"turned": 30.0}}),
 ("texture_export", {"format": "stl"}, {"path": "/tmp/out/grip_textured.stl", "format": "stl", "triangles": 5000}),
 ("texture_export", {"format": "bumpmesh"}, {"path": "/tmp/out/grip.bumpmesh", "format": "bumpmesh"}),
 ("texture_prepare", {"body": "Grip"}, {"body": "Grip", "stl": "/tmp/out/Grip-for-texture.stl", "triangles": 812, "mode": "include", "faces": ["Face1", "Face2"]}),
 ("texture_export", {"format": "3mf"}, {"path": "/tmp/out/Grip_textured.3mf", "file": "Grip_textured.3mf", "format": "3mf", "triangles": 9000}),
 ("texture_export", {"format": "stl"}, {"job": "job-1", "tool": "texture_export", "status": "running"}),
 ("texture_prepare", {"body": "Grip"}, {"job": "job-2", "tool": "texture_prepare", "status": "running"}),
 ("texture_preview", {"quality": "fine"}, {"part": "Grip", "building": True, "verdict": "still building", "ms": 45012}),
 ("texture_draft", {"part": "Box"}, {"part": "Grip", "label": "Grip", "draft": {"part": "Box"}}),
 ("texture_draft", {"reset": True}, {"part": "Grip", "label": "Grip"}),
 ("texture_set", {"texture": "hexagon"}, {"layer": 0, "part": "Grip", "texture": "hexagon"}),
 ("texture_set", {"settings": {"amplitude": 0.6}}, {"layer": 0, "part": "Grip", "setNames": ["amplitude"]}),
 ("texture_mask", {"add": ["Face5"]}, {"layer": 0, "mode": "exclude", "faces": ["Face5"], "triangles": 0, "part": "Grip"}),
 ("texture_mask", {"clear": True}, {"layer": 0, "mode": "exclude", "faces": [], "triangles": 0, "part": "Grip"}),
 ("texture_layer", {"action": "add"}, {"layer": 1, "layerCount": 2, "part": "Grip"}),
 ("texture_layer", {"action": "solo", "layer": 1}, {"layer": 1, "layerCount": 2, "part": "Grip"}),
]
print(json.dumps([activity.did(n, a, b) for n, a, b in rows]))
"""
        got = json.loads(fresh(code, AGENTLINK))
        self.assertEqual(got, ["Stopped the texture preview", "Previewed the texture on Grip: 120000 triangles", "Turntable on",
                               "Turned the view 30 degrees", "Exported grip_textured.stl: 5000 triangles",
                               "Saved the BumpMesh project grip.bumpmesh",
                               "Wrote Grip-for-texture.stl from Grip for texturing: 812 triangles, include Face1 and Face2",
                               "Exported Grip_textured.3mf: 9000 triangles", "Exporting stl in the background",
                               "Preparing Grip for texturing in the background", "Still building the texture preview on Grip",
                               "Started texturing Grip", "Reset the draft of Grip to defaults", "Put hexagon on Grip, layer 0",
                               "Set amplitude on Grip, layer 0", "Kept Face5 of Grip plain on layer 0; brush: 0 triangles",
                               "Every face of Grip gets the texture on layer 0: 0 triangles masked",
                               "Added layer 1 to Grip", "Set solo on layer 1 of Grip"])


class OneRing(unittest.TestCase):
    """Two rings at once (round 4, 0014-0019): a Texturizer call keeps only
    the newest of the echo's lit buttons, on the GUI thread only."""

    def test_only_the_newest_ring_stays(self):
        code = r"""
import json, sys, threading, types
for n in ("agentlink", "agentlink.facepick", "agentlink.hooks", "agentlink.families", "agentlink.families.picks",
          "agentlink.protocol", "agentlink.overlay"):
    sys.modules[n] = types.ModuleType(n)
sys.modules["agentlink.protocol"].ToolError = Exception
class M:
    def __init__(self, h, owner="echo", style="flash"): self.handle, self.owner, self.style = h, owner, style
class Svc:
    def __init__(self): self.board = types.SimpleNamespace(marks=[M(1), M(2), M(3, "tutor", "ring"), M(4)])
    def clear(self, owner=None, handle=None):
        self.board.marks = [m for m in self.board.marks if not (m.owner == owner and m.handle == handle)]
svc = Svc()
sys.modules["agentlink.overlay"].service = lambda: svc
sys.modules["agentlink"].overlay = sys.modules["agentlink.overlay"]
from texturizer import fam_texture as f
calls = []
wrapped = f._ringed(lambda a: calls.append(a) or "ok")
off = []
t = threading.Thread(target=lambda: off.append(wrapped({"x": 0})))
t.start(); t.join()
left_after_worker = sorted(m.handle for m in svc.board.marks)
got = wrapped({"x": 1})
print(json.dumps({"worker": left_after_worker, "left": sorted((m.handle, m.owner) for m in svc.board.marks), "got": got, "calls": calls,
                  "again": f.one_ring()}))
"""
        out = json.loads(fresh(code))
        self.assertEqual(out["worker"], [1, 2, 3, 4])                  # a job's worker never touches the window
        self.assertEqual(out["left"], [[3, "tutor"], [4, "echo"]])    # the Tutor's own ring is not the echo's
        self.assertEqual((out["got"], out["calls"]), ("ok", [{"x": 0}, {"x": 1}]))
        self.assertFalse(out["again"])


STUB = r"""
import json, os, sys, types
calls = {"icons": [], "benches": [], "commands": {}, "toolbars": [], "menus": []}
gui = types.ModuleType("FreeCADGui")
class Workbench:
    def appendToolbar(self, name, cmds): calls["toolbars"].append([name, cmds])
    def appendMenu(self, name, cmds): calls["menus"].append([name, cmds])
    def Activated(self): pass
    def Deactivated(self): pass
gui.Workbench = Workbench
gui.addIconPath = lambda p: calls["icons"].append(p)
gui.addWorkbench = lambda wb: calls["benches"].append(wb)
gui.addCommand = lambda n, c: calls["commands"].__setitem__(n, c.GetResources())
fc = types.ModuleType("FreeCAD")
sys.modules["FreeCADGui"], sys.modules["FreeCAD"] = gui, fc
src = open(os.path.join(sys.argv[1], "InitGui.py")).read()
# As FreeCAD runs it: its globals hold only FreeCAD, FreeCADGui and the
# builtins, and InitGui's own names land in a separate locals dict, which a
# class or method body cannot see (real FreeCAD's "name '_ICONS' is not
# defined").
exec(compile(src, "InitGui.py", "exec"), {"__builtins__": __builtins__, "FreeCAD": fc, "FreeCADGui": gui}, {})
wb = calls["benches"][0]
wb.Initialize()
import texturizer.commands as _tc
_tc.open_sidebar = lambda load=False: calls.setdefault("sidebar", []).append(load)
wb.Activated()
calls["about"] = calls["commands"]["Texturizer_About"]["MenuText"]
print(json.dumps({"class": type(wb).__name__, "menu": wb.MenuText, "icon": wb.Icon, "tip": wb.ToolTip, "kind": wb.GetClassName(),
                  "icons": calls["icons"], "commands": calls["commands"], "toolbars": calls["toolbars"], "menus": calls["menus"],
                  "sidebar": calls.get("sidebar"), "about": calls["about"],
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
        # Undo, Redo, Export STL, Wireframe and Perspective are FreeCAD's own.
        header = ["Texturizer_Reset", "Texturizer_SaveProject", "Texturizer_LoadProject",
                  "Texturizer_Gallery", "Texturizer_Export3MF"]
        self.assertEqual(out["toolbars"], [["Texturizer", header], ["View", commands.VIEW_TOOLBAR],
                                           ["Check", [commands.PREPARE, commands.CHECK]],
                                           ["Texturizer Panel", [commands.TEXTURE, commands.ADD]]])
        self.assertEqual([out["commands"][n]["MenuText"] for n in header],
                         ["Reset to Defaults", "Export BumpMesh Project…", "Import BumpMesh Project…", "Texture Gallery", "Export 3MF"])
        self.assertEqual([out["commands"][n]["MenuText"] for n in commands.VIEW_TOOLBAR], ["Section View", "Turntable"])
        self.assertIn("Texturizer_About", out["menus"][0][1])
        self.assertEqual(out["menus"][0][1][0], commands.TEXTURE)
        self.assertEqual(sorted(out["commands"]), sorted(commands.NAMES + ["Texturizer_About"]))
        for name, res in out["commands"].items():  # every command's Pixmap resolves
            pix = res["Pixmap"]
            self.assertTrue(os.path.isfile(os.path.join(PKG, "icons", pix + ".svg")) or pix in FREECAD_ICONS, pix)
            # A tooltip says what the command does; the credit is in About,
            # the sidebar's footer and the workbench's tooltip (macOS cut the
            # long ones short).
            self.assertNotIn("Stefan Hermann", res["ToolTip"], name)
            self.assertLessEqual(len(res["ToolTip"]), 160, name)
            self.assertEqual(res["MenuText"], texturizer.title_case(res["MenuText"]), name)
        self.assertEqual(out["agentlink"], [])
        self.assertEqual((out["sidebar"], out["about"]), ([True], "About Texturizer"))
        self.assertEqual(out["tip"], "Surface textures for printed parts. " + texturizer.CREDIT)
        self.assertEqual(out["commands"][commands.ADD]["MenuText"], "Add to Document")

    def test_about_credit(self):
        text = texturizer.about()
        self.assertTrue(text.startswith("Texturizer for FreeCAD\nVersion " + texturizer.VERSION + "\nLicence: AGPL-3.0"))
        self.assertIn("Based on BumpMesh by Stefan Hermann (CNC Kitchen), github.com/CNCKitchen/stlTexturizer", text)
        html = texturizer.about_html()
        self.assertIn("Based on BumpMesh by Stefan Hermann (CNC Kitchen), <a href=\"https://github.com/CNCKitchen/stlTexturizer\">", html)
        self.assertIn(texturizer.VERSION, html)
        self.assertIn("AGPL-3.0", html)

    def test_about_and_sidebar_keep_their_menu_place(self):
        # macOS's text heuristic moved "About Texturizer" (and would move
        # "Settings Sidebar") into the application menu: both get no role.
        from texturizer import workbench

        class A:
            def __init__(self, name):
                self.name, self.role = name, None

            def objectName(self):  # noqa: N802
                return self.name

            def setMenuRole(self, r):  # noqa: N802
                self.role = r

        acts = [A("Texturizer_About"), A("Texturizer_Texture"), A("Std_About"), A("Std_DlgPreferences")]
        self.assertEqual(workbench.plain_menu_roles(acts, 0), 2)
        self.assertEqual([a.role for a in acts], [0, 0, None, None])

    def test_section_toggles_and_keeps_the_window_size(self):
        # Found live: Section View didn't close the Clipping dock when run
        # again, and opening it grew the main window from 2082 to 2256 px.
        class Size(tuple):
            pass

        class Dock:
            def __init__(self, name, area=1):
                self.name, self.area, self.visible, self.closed = name, area, True, False

            def objectName(self):  # noqa: N802
                return self.name

            def isVisible(self):  # noqa: N802
                return self.visible

            def isFloating(self):  # noqa: N802
                return False

            def widget(self):
                return self

            def reject(self):
                self.closed, self.visible = True, False

            def show(self):
                self.visible = True

            def raise_(self):
                pass

        class MW:
            def __init__(self):
                self.docks = [Dock("Combo View"), Dock("TexturizerSidebar", 2)]
                self.h, self.tabbed = 2082, []

            def size(self):
                return Size((1600, self.h))

            def resize(self, s):
                self.h = s[1]

            def isMaximized(self):  # noqa: N802
                return False

            def isFullScreen(self):  # noqa: N802
                return False

            def findChildren(self, _t):  # noqa: N802
                return list(self.docks)

            def dockWidgetArea(self, d):  # noqa: N802
                return d.area

            def tabifyDockWidget(self, a, b):  # noqa: N802
                self.tabbed.append((a.name, b.name))

        mw = MW()
        find = lambda m: next((d for d in m.docks if d.name == "Clipping"), None)  # noqa: E731
        later = []

        def run():  # Std_ToggleClipPlane: a Clipping dock on the left, the window taller
            mw.docks.append(Dock("Clipping"))
            mw.h = 2256

        self.assertTrue(commands.toggle_section(mw, run, find=find, later=later.append))
        self.assertEqual(mw.tabbed, [("Combo View", "Clipping")])  # tabbed in FreeCAD's area, not stacked
        self.assertEqual(mw.h, 2082)
        mw.h = 2256
        later[0]()  # once the layout settles, too
        self.assertEqual(mw.h, 2082)
        ran = []
        self.assertFalse(commands.toggle_section(mw, lambda: ran.append(1), find=find, later=later.append))
        self.assertTrue(find(mw).closed)  # its own Close
        self.assertEqual(ran, [])

    def test_panel_names_no_tools(self):
        self.assertNotIn("texture_apply", panel.summary(None)[0])
        self.assertIn("Add to Document", panel.summary(None)[0])
        self.assertIn("Add to Document", panel.applied_line(None))

    def test_title_case(self):
        t = texturizer.title_case
        self.assertEqual(t("Reset to defaults"), "Reset to Defaults")
        self.assertEqual(t("Add to document"), "Add to Document")
        self.assertEqual(t("Suggest values (change to taste)"), "Suggest Values (Change to Taste)")
        self.assertEqual(t("Auto-fit"), "Auto-Fit")
        self.assertEqual(t("Export 3MF"), "Export 3MF")
        self.assertEqual(t("Advanced / Beta Features ⓘ"), "Advanced / Beta Features ⓘ")
        self.assertEqual(t("Load Model…"), "Load Model…")


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

    @unittest.skipUnless(AGENTLINK, "Agent Link is not beside it")
    def test_hosted_tab_carries_its_own_credit(self):
        # The Texture page shows BumpMesh's credit (Stefan Hermann, CNC
        # Kitchen), so Agent Link hides its own line while it is in front;
        # an Agent Link whose register_tab has no own_credit still gets the tab.
        code = r"""
import sys, types
for n in ("agentlink.facepick", "agentlink.families", "agentlink.families.picks"):
    sys.modules[n] = types.ModuleType(n)
from agentlink import hooks
if OLD:
    real = hooks.register_tab
    def register_tab(title, factory, order=50, workbench=None):
        return real(title, factory, order, workbench)
    hooks.register_tab = register_tab
import texturizer, texturizer.agentlink_families
print([(t["title"], t.get("own_credit", False)) for t in hooks.TABS if t["order"] == 60])
"""
        self.assertEqual(fresh("OLD = False\n" + code, AGENTLINK), "[('Texture', True)]")
        self.assertEqual(fresh("OLD = True\n" + code, AGENTLINK), "[('Texture', False)]")
        import texturizer

        self.assertIn("Stefan Hermann", texturizer.CREDIT)


if __name__ == "__main__":
    unittest.main()
