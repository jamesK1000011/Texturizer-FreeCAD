# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The sidebar's control table against BumpMesh's own sources, the draft,
and the sidebar widgets (real PySide under QT_QPA_PLATFORM=offscreen when it
is importable; else the stand-in in _fakeqt, so the code still runs)."""
import importlib
import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gen_controls  # noqa: E402
from texturizer import controls, draft, qt, title_case  # noqa: E402

SRC = os.environ.get("BUMPMESH_SRC", gen_controls.DEFAULT_SRC)
HAVE_SRC = os.path.exists(os.path.join(SRC, "js", "i18n", "en.js"))
REAL_QT = qt.available()


def load_gui():
    """gallery and sidebar on real Qt, or on the stand-in."""
    from texturizer import gallery, sidebar

    if not REAL_QT:
        import _fakeqt

        qt.QtCore, qt.QtGui, qt.QtWidgets = _fakeqt.modules()
        gallery, sidebar = importlib.reload(gallery), importlib.reload(sidebar)
    qt.app()
    return gallery, sidebar


def unload_gui():
    if not REAL_QT:
        from texturizer import gallery, sidebar

        qt.QtCore = qt.QtGui = qt.QtWidgets = None
        importlib.reload(gallery)
        importlib.reload(sidebar)


def fake_library():
    return [
        {"id": "hexagons", "name": "Hexagons", "category": "geometric", "thumb": None, "image": None, "credit": None,
         "licence": "AGPL-3.0", "source_url": None, "defaults": {"scaleU": 20}},
        {"id": "knurling", "name": "Knurling", "category": "grip", "thumb": None, "image": None, "credit": None,
         "licence": "AGPL-3.0", "source_url": None, "defaults": {}},
        {"id": "aztec", "name": "Aztec", "category": "patterns", "thumb": None, "image": None, "credit": "hero",
         "licence": "CC BY 4.0", "source_url": "https://heropatterns.com/", "defaults": {}},
        {"id": "bark", "name": "Bark", "category": "natural", "thumb": None, "image": None, "credit": "cc0",
         "licence": "CC0", "source_url": "https://ambientcg.com/view?id=Bark001", "defaults": {}},
        {"id": "basket", "name": "Basket", "category": "fabric", "thumb": None, "image": None, "credit": "ff",
         "licence": "AGPL-3.0", "source_url": None, "defaults": {}},
        {"id": "tread", "name": "Tread", "category": "tires", "thumb": None, "image": None, "credit": None,
         "licence": "AGPL-3.0", "source_url": None, "defaults": {}},
        {"id": "custom:logo", "name": "My logo", "category": "custom", "thumb": None, "image": None, "credit": "custom",
         "licence": "yours", "source_url": None, "defaults": {}},
    ]


# -- controls.py -----------------------------------------------------------------
class ControlsTable(unittest.TestCase):
    @unittest.skipUnless(HAVE_SRC, "BumpMesh sources not here (set BUMPMESH_SRC)")
    def test_matches_fresh_parse(self):
        fresh = gen_controls.render(gen_controls.build(SRC))
        with open(gen_controls.OUT, encoding="utf-8") as f:
            self.assertEqual(f.read(), fresh, "rerun tests/tools/gen_controls.py")

    @unittest.skipUnless(HAVE_SRC, "BumpMesh sources not here (set BUMPMESH_SRC)")
    def test_labels_ranges_defaults_from_bumpmesh(self):
        with open(os.path.join(SRC, "js", "i18n", "en.js"), encoding="utf-8") as f:
            en = gen_controls.parse_en(f.read())
        with open(os.path.join(SRC, "index.html"), encoding="utf-8") as f:
            page = f.read()
        for c in controls.CONTROLS:
            if c.get("label_key"):
                self.assertEqual(c["label"], en[c["label_key"]], c["key"])
            if c["origin"] == "bumpmesh" and c.get("tooltip"):
                self.assertIn(c["tooltip"], en.values(), c["key"])
        # A few straight from the page, read here without the generator.
        def attrs(i):
            tag = re.search(r'<input[^>]*id="%s"[^>]*>' % re.escape(i), page).group(0)
            return dict(re.findall(r'(\w+)="([^"]*)"', tag))
        a, b = attrs("amplitude"), attrs("amplitude-val")
        c = controls.BY_KEY["amplitude"]
        self.assertEqual((c["slider"]["min"], c["slider"]["max"]), (float(a["min"]), float(a["max"])))
        self.assertEqual((c["box"]["min"], c["box"]["max"], c["default"]), (0, 100, 0.5))
        self.assertEqual(float(b["max"]), 100)
        u = controls.BY_KEY["scaleU"]
        self.assertEqual((u["type"], u["box"]["min"], u["box"]["max"], u["default"]), ("log slider", 0.01, 10000, 25))
        self.assertEqual(controls.BY_KEY["maxTriangles"]["default"], 750000)
        self.assertEqual(controls.BY_KEY["boundaryFalloffCurve"]["default"], "ease")
        self.assertEqual([s["title"] for s in controls.SECTIONS[2:]],
                         [en[k] for k in ("sections.displacementMap", "sections.projection", "sections.displacement",
                                          "sections.transform", "sections.masking", "sections.advancedFeatures",
                                          "sections.export")])

    @unittest.skipUnless(HAVE_SRC, "BumpMesh sources not here (set BUMPMESH_SRC)")
    def test_layer_scope_is_bumpmesh_layer_keys(self):
        with open(os.path.join(SRC, "js", "main.js"), encoding="utf-8") as f:
            main = f.read()
        body = main.split("const LAYER_KEYS = [", 1)[1].split("];", 1)[0]
        layer_keys = set(re.findall(r"'(\w+)'", body))
        # Every per-layer setting is a layer row (textureHeight is |amplitude|).
        self.assertEqual(layer_keys - {"textureHeight"} - set(draft.layer_defaults()), set())
        for c in controls.CONTROLS:
            if c["scope"] == "layer" and c["origin"] == "bumpmesh" and c["key"] != "includeOnly":
                self.assertIn(c["key"], layer_keys, c["key"])
            if c["scope"] == "global" and c["origin"] == "bumpmesh" and c["key"] != "bakeMask":
                self.assertNotIn(c["key"], layer_keys, c["key"])

    def test_table_is_whole(self):
        self.assertEqual([s["id"] for s in controls.SECTIONS],
                         ["header", "load", "displacementMap", "projection", "textureDepth", "transform", "masking",
                          "advanced", "export"])
        self.assertTrue(controls.SECTIONS[7]["collapsed"])
        self.assertEqual(controls.MAX_LAYERS, 4)
        for c in controls.CONTROLS:
            for f in ("section", "key", "type", "scope", "origin"):
                self.assertIn(f, c)
            if c["type"] in ("slider", "log slider"):
                rng = c.get("box") or c["slider"]
                self.assertTrue(rng["min"] <= c["default"] <= rng["max"], c["key"])
        mode = controls.BY_KEY["mappingMode"]
        self.assertEqual([o["label"] for o in mode["options"] if not o["hidden"]],
                         ["Triplanar", "Cubic (Box)", "Cylindrical", "Outline wrap", "Spherical", "Planar XY",
                          "Planar XZ", "Planar YZ"])
        self.assertEqual(mode["default"], 5)


# -- draft.py ----------------------------------------------------------------------
class DraftModel(unittest.TestCase):
    def test_defaults_and_clamp(self):
        d = draft.Draft()
        self.assertEqual(d.get("amplitude"), 0.5)
        self.assertEqual(d.get("mappingMode"), 5)
        self.assertEqual(d.get("refineLength"), 1)
        self.assertTrue(d.get("preserveUntextured"))
        self.assertEqual(d.set("amplitude", 500), 100.0)  # typed range 0–100
        self.assertEqual(d.set("capAngle", 0), 1)
        with self.assertRaises(ValueError):
            d.set("mappingMode", 42)
        with self.assertRaises(KeyError):
            d.set("nope", 1)

    def test_lock_scale_moves_both(self):
        d = draft.Draft()
        d.set("scaleU", 10)
        self.assertEqual((d.get("scaleU"), d.get("scaleV")), (10, 10))
        d.set("lockScale", False)
        d.set("scaleU", 7)
        self.assertEqual((d.get("scaleU"), d.get("scaleV")), (7, 10))

    def test_undo_redo_coalesce_batch(self):
        d = draft.Draft()
        d.set("amplitude", 1.0)
        d.set("rotation", 45)
        self.assertTrue(d.undo())
        self.assertEqual(d.get("rotation"), 0)
        self.assertEqual(d.get("amplitude"), 1.0)
        self.assertTrue(d.redo())
        self.assertEqual(d.get("rotation"), 45)
        # One slider drag = one step.
        for v in (0.1, 0.2, 0.3):
            d.set("amplitude", v, coalesce=True)
        d.end_coalesce()
        d.undo()
        self.assertEqual(d.get("amplitude"), 1.0)
        d.update({"offsetU": 0.5, "offsetV": -0.5})
        d.undo()
        self.assertEqual((d.get("offsetU"), d.get("offsetV")), (0, 0))
        d.reset()
        self.assertEqual(d.get("rotation"), 0)
        d.undo()
        self.assertEqual(d.get("rotation"), 45)
        while d.undo():
            pass
        self.assertFalse(d.can_undo())
        self.assertTrue(d.can_redo())

    def test_layers_and_masks(self):
        d = draft.Draft()
        self.assertFalse(d.layer()["includeOnly"])
        d.set("amplitude", 1.2)
        for n in range(3):
            self.assertEqual(d.add_layer(), n + 1)
        self.assertIsNone(d.add_layer())  # MAX_LAYERS
        self.assertTrue(d.layer()["includeOnly"])  # BumpMesh: a new layer starts Include Only
        self.assertEqual(d.get("amplitude"), 1.2)
        d.set("amplitude", 0.2)
        self.assertEqual(d.get("amplitude", layer=0), 1.2)
        d.mask_add(faces=["Face1", "Face2"], triangles=[3, 1])
        self.assertEqual(d.mask_count(), 4)
        d.set("includeOnly", False)  # switching clears, as BumpMesh
        self.assertEqual(d.mask_count(), 0)
        d.move_layer(3, -1)
        self.assertEqual(d.active, 2)
        d.remove_layer(0)
        self.assertEqual(len(d.layers), 3)
        d.set_layer(0, visible=False, blendAdd=True, solo=True)
        self.assertEqual((d.layers[0]["visible"], d.layers[0]["blendAdd"]), (False, True))
        d.set_texture("aztec", defaults={"scaleU": 12, "refineLength": 9})
        self.assertEqual(d.get("scaleU"), 12)
        self.assertEqual(d.get("refineLength"), 1)  # global keys are not a texture's

    def test_json_round_trip(self):
        d = draft.Draft(part="Body")
        d.set("amplitude", 0.8)
        d.set("bottomAngleLimit", 12)
        d.set_frame(rotation=[90, 0, 0], face="Face6")
        d.add_layer()
        d.mask_add(faces=["Face3"])
        d.set_texture("bark")
        text = d.to_json()
        e = draft.Draft.from_json(text)
        self.assertEqual(e.to_dict(), d.to_dict())
        self.assertEqual(json.loads(text)["format"], "texturizer-draft")
        self.assertFalse(e.can_undo())
        # Unknown keys dropped, missing ones defaulted.
        f = draft.Draft.from_dict({"settings": {"refineLength": 0.4, "bogus": 1}, "layers": [{"settings": {"rotation": 30}}]})
        self.assertEqual((f.get("refineLength"), f.get("rotation"), f.get("amplitude")), (0.4, 30, 0.5))
        self.assertNotIn("bogus", f.settings)

    def test_signals(self):
        d = draft.Draft()
        seen = []
        unsub = d.subscribe(seen.append)
        d.set("rotation", 10)
        d.add_layer()
        d.set_active(0)
        d.mask_add(faces=["Face1"])
        d.undo()
        self.assertEqual([e["kind"] for e in seen], ["set", "layers", "active", "mask", "undo"])
        self.assertEqual(seen[0]["key"], "rotation")
        unsub()
        d.set("rotation", 20)
        self.assertEqual(len(seen), 5)

    def test_no_gui_imports(self):
        import ast

        for name in ("draft.py", "controls.py"):
            with open(os.path.join(ROOT, "texturizer", name), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
            mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            for m in mods:
                self.assertFalse(re.match(r"(PySide|FreeCAD|agentlink|.*qt$)", m), (name, m))


# -- sidebar.py ----------------------------------------------------------------------
class SidebarWidget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gallery, cls.sidebar = load_gui()
        cls.home = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        unload_gui()

    def make(self, d=None):
        self.calls = []

        def runner(tool, args):
            self.calls.append((tool, args))
            return True, {"message": "ok"}

        d = d or draft.Draft()
        fav = self.gallery.Favourites(path=os.path.join(self.home, "fav.json"), entries=fake_library())
        sb = self.sidebar.Sidebar(d, runner=runner, list_textures=fake_library, favourites=fav, confirm=lambda: True,
                                  selection=lambda: ["Face4"])
        return d, sb

    def test_gallery_slides_out_inside_the_sidebar(self):
        """The Texture Gallery is a page of the sidebar, not a dock of its
        own beside it (which widened the window on the Mac); Done slides it
        back to the settings."""
        _d, sb = self.make()
        self.assertIs(sb.pages.currentWidget(), sb.scroll)
        g = sb.press("textureGallery")
        self.assertTrue(sb.gallery_open())
        self.assertIsNone(sb._gallery.parentWidget())  # never put in the main window
        sb._gallery.done.click()
        self.assertFalse(sb.gallery_open())
        self.assertIs(sb.pages.currentWidget(), sb.scroll)
        self.assertTrue(g)

    def test_fits_a_narrow_panel(self):
        """The sidebar never asks more than layout.CONTENT_MIN wide (it was
        clipped in the 300 px slide-out), and a long check box label wraps
        in a label of its own."""
        from texturizer import layout

        _d, sb = self.make()
        self.assertLessEqual(sb.fit_width(), layout.CONTENT_MIN)
        long_ = [k for k, b in sb.binds.items() if getattr(b, "wrapped", None) is not None]
        self.assertTrue(long_)
        for k in long_:
            self.assertGreater(len(sb.binds[k].label()), self.sidebar.LONG_CHECK)
            self.assertEqual(sb.binds[k].widgets[0].text(), "")

    def test_sections_open_as_left_only_the_first_at_first(self):
        """Only Displacement Map open the first time; after that the
        sections open as the person left them. Dense spacing."""
        self.assertEqual(self.sidebar.open_ids(None, controls.SECTIONS), {"displacementMap"})
        self.assertEqual(self.sidebar.open_ids("projection,export,nonsense", controls.SECTIONS), {"projection", "export"})
        self.assertEqual(self.sidebar.open_ids("", controls.SECTIONS), set())
        self.assertLessEqual(self.sidebar.SPACING, 6)
        self.assertLessEqual(self.sidebar.SECTION_GAP, 6)
        store = self.sidebar.OpenStore()
        store.load = lambda: None
        d = draft.Draft()
        fav = self.gallery.Favourites(path=os.path.join(self.home, "fav2.json"), entries=fake_library())
        sb = self.sidebar.Sidebar(d, list_textures=fake_library, favourites=fav, open_store=store)
        shut = [x.sid for x in sb.sections if x.head is not None and x.collapsed()]
        self.assertNotIn("displacementMap", shut)
        self.assertIn("projection", shut)
        sb.sections[3].head.click()  # Projection opened
        self.assertEqual(store.mem, "displacementMap,projection")

    def test_sections_in_bumpmesh_order(self):
        d, sb = self.make()
        self.assertEqual(sb.windowTitle(), "Texturizer")
        self.assertEqual(sb.section_titles(), [None, None, "Displacement Map", "Projection", "Texture Depth", "Transform",
                                               "Masking", "Advanced / Beta Features ⓘ", "Export ⓘ"])
        self.assertEqual(sb.section_titles(), [s["title"] for s in controls.SECTIONS])
        adv = sb.sections[7]
        self.assertTrue(adv.collapsed())
        adv.head.click()
        self.assertFalse(adv.collapsed())
        self.assertIn("Stefan Hermann (CNC Kitchen)", sb.credit.text())

    def test_every_label_is_bumpmeshs(self):
        d, sb = self.make()
        got = sb.labels()
        for c in controls.CONTROLS:
            if c["type"] == "hidden":   # stored on the layer, no widget
                self.assertNotIn(c["key"], got)
                continue
            self.assertIn(c["key"], got)
            if c.get("label") is not None:
                # Buttons in Title Case, as FreeCAD's design guide asks; the
                # rest (labels, checkboxes) in BumpMesh's own words.
                want = title_case(c["label"]) if c["type"] in ("button", "toggle") else c["label"]
                self.assertEqual(got[c["key"]], want, c["key"])
        self.assertEqual(got["resetSettings"], "Reset to Defaults")
        self.assertEqual(got["addToDocument"], "Add to Document")
        self.assertEqual(got["addLayer"], "Add Layer")
        mode = sb.binds["mappingMode"]
        self.assertEqual(mode.items()[:3], ["Triplanar", "Cubic (Box)", "Cylindrical"])
        self.assertEqual(sb.binds["boundaryFalloffCurve"].option_labels(), ["Linear", "S-Curve", "Ease-In"])
        self.assertEqual(sb.binds["includeOnly"].option_labels(), ["Exclude", "Include Only"])

    def test_slider_moves_draft_and_draft_moves_widgets(self):
        d, sb = self.make()
        b = sb.binds["amplitude"]
        self.assertEqual(b.box.value(), 0.5)
        self.assertEqual(b.slider.value(), self.sidebar.value_to_pos(b.c, 0.5))
        b.slider.setValue(b.slider.maximum())
        self.assertAlmostEqual(d.get("amplitude"), 2.0)
        self.assertAlmostEqual(b.box.value(), 2.0)
        b.box.setValue(12.5)  # the typed box goes past the slider, as BumpMesh
        self.assertAlmostEqual(d.get("amplitude"), 12.5)
        self.assertEqual(b.slider.value(), b.slider.maximum())
        # Draft -> widgets (an agent's texture_set).
        d.update({"rotation": 90, "invertTexture": True, "mappingMode": 3, "boundaryFalloffCurve": "linear"})
        self.assertEqual(sb.binds["rotation"].box.value(), 90)
        self.assertTrue(sb.binds["invertTexture"].widgets[0].isChecked())
        self.assertEqual(sb.binds["mappingMode"].combo.currentText(), "Cylindrical")
        self.assertTrue(dict(sb.binds["boundaryFalloffCurve"].buttons)["linear"].isChecked())
        self.assertFalse(sb.binds["capAngle"].row.isHidden())  # cylindrical shows Cap Angle and the axis
        self.assertFalse(sb.binds["cylinderAutofit"].row.isHidden())
        self.assertTrue(sb.binds["rotateAroundZ"].row.isHidden())
        self.assertTrue(sb.binds["mappingBlend"].row.isHidden())  # hidden in BumpMesh
        # Log slider: Size U.
        u = sb.binds["scaleU"]
        u.slider.setValue(1000)
        self.assertEqual(d.get("scaleU"), 500.0)  # 10 x the 50 mm anchor
        self.assertEqual(d.get("scaleV"), 500.0)  # lock
        # Undo comes back through the widgets.
        d.undo()
        self.assertEqual(u.box.value(), 25)

    def test_layer_strip_edits_active_layer(self):
        d, sb = self.make()
        sb.binds["amplitude"].box.setValue(1.5)
        sb.layers.add.click()
        self.assertEqual(len(d.layers), 2)
        self.assertEqual(len(sb.layers.rows), 2)
        sb.binds["amplitude"].box.setValue(0.25)
        self.assertEqual(d.get("amplitude", 0), 1.5)
        sb.layers.rows[0]["name"].click()
        self.assertEqual(d.active, 0)
        self.assertEqual(sb.binds["amplitude"].box.value(), 1.5)
        self.assertTrue(dict(sb.binds["includeOnly"].buttons)[False].isChecked())
        sb.layers.rows[1]["eye"].click()
        self.assertFalse(d.layers[1]["visible"])
        for _ in range(2):
            sb.layers.add.click()
        self.assertFalse(sb.layers.add.isEnabled())

    def test_buttons_call_runner_or_draft(self):
        d, sb = self.make()
        sb.binds["exportStl"].widgets[0].click()
        self.assertEqual(self.calls[-1], ("texture_export", {"format": "stl", "layer": 0}))
        sb.binds["addToDocument"].widgets[0].click()
        self.assertEqual(self.calls[-1][0], "texture_apply")
        sb.binds["loadModel"].widgets[0].click()
        self.assertEqual(self.calls[-1][0], "texture_draft")
        n = len(self.calls)
        sb.binds["rotation"].box.setValue(30)
        sb.binds["undo"].widgets[0].click()
        self.assertEqual(d.get("rotation"), 0)
        self.assertEqual(len(self.calls), n)  # undo is the draft's own
        sb.binds["exclusionTool"].buttons[0][1].click()  # Faces tool
        self.assertEqual(d.get("exclusionTool"), "faces")
        self.assertFalse(sb.binds["maskFaces"].row.isHidden())
        sb.add_selected_faces()
        self.assertEqual(d.mask()["faces"], ["Face4"])
        self.assertEqual(sb.binds["maskFaces"].list.count(), 1)
        self.assertEqual(sb.mask_count.text(), "1 face masked")
        sb.binds["clearAll"].widgets[0].click()
        self.assertEqual(d.mask_count(), 0)
        brush = dict(sb.binds["exclusionTool"].buttons)["brush"]
        self.assertTrue(brush.isEnabled())  # the brush (P2, P3)
        brush.click()
        self.assertEqual(d.get("exclusionTool"), "brush")
        for k in ("brushIsRadius", "brushPrecision", "brushSize", "brushHardness"):
            self.assertTrue(sb.binds[k].row.isEnabled(), k)
        modes = dict(sb.binds["brushPrecision"].buttons)
        self.assertTrue(modes[False].isEnabled() and modes[True].isEnabled())  # Standard and Precision (P4)
        self.assertEqual((d.get("brushPrecision"), d.get("brushSize"), d.get("brushHardness")), (True, 10, 50))
        modes[False].click()   # Standard: whole triangles, no Hardness (BumpMesh's setBrushPrecision)
        self.assertTrue(sb.binds["brushHardness"].row.isHidden())
        modes[True].click()
        self.assertFalse(sb.binds["brushHardness"].row.isHidden())
        self.assertEqual(sb.binds["shiftHint"].label(), "Hold Shift to erase")
        d.set("exclusionTool", None)
        sb.binds["rotate"].widgets[0].click()
        self.assertFalse(sb.binds["rotateX"].row.isHidden())
        sb.binds["rotateX"].box.setValue(90)
        self.assertEqual(d.frame["rotation"], [0, 0, 0])  # waits for Apply
        sb.binds["rotateApply"].widgets[0].click()
        self.assertEqual(d.frame["rotation"], [90, 0, 0])

    def test_favourites_grid_and_texture_pick(self):
        d, sb = self.make()
        ids = [t.entry["id"] for t in sb.favgrid.tiles]
        self.assertEqual(ids[:2], ["knurling", "hexagons"])  # BumpMesh's default favourites, by name
        sb.favgrid.tiles[0].click()
        self.assertEqual(d.layer()["texture"], "knurling")
        self.assertIn("Knurling", sb.credit_line.text())
        self.assertEqual(sb.favgrid.flow.items, sb.favgrid.tiles)  # the reflowing grid holds the tiles
        self.assertLessEqual(sb.favgrid.flow.cols, self.gallery.COLUMNS)

    def test_dock_fits_its_content(self):
        d, sb = self.make()
        self.assertEqual(self.sidebar.min_dock_width(300, 15, 1), 317)
        self.assertGreater(sb.fit_width(), 0)
        self.assertTrue(sb.credit.text().startswith(self.gallery.BUMPMESH_CREDIT))

    def _flows(self, sb):
        """Every FlowLayout in the sidebar, the layer rows' included."""
        out, seen = [], set()

        def walk(lay):
            if lay is None or id(lay) in seen:
                return
            seen.add(id(lay))
            if isinstance(lay, self.sidebar.FlowLayout):
                out.append(lay)
            for it in lay.items if hasattr(lay, "items") else []:
                walk(it._l)
                if it._w is not None:
                    walk(it._w._layout)
            for i in range(lay.count()) if isinstance(lay, self.sidebar.FlowLayout) else []:
                walk(lay.itemAt(i).widget()._layout)

        walk(sb.body._layout)
        return out

    @unittest.skipIf(REAL_QT, "reads the stand-in Qt's own layout records (_layout, children_widgets)")
    def test_rows_wrap_inside_the_viewport(self):
        # Found live: Load Project, a layer row's last button, the Mode
        # combo's arrow and Add Layer ran under the vertical scrollbar. A
        # dock 280 px wide with a 15 px scrollbar and a 1 px frame leaves a
        # 263 px viewport: every row of buttons lays out inside it, the
        # widest single button (Suggest Values (Change to Taste), 240 px
        # here) included, where one row of the header alone needs 447 px.
        d, sb = self.make()
        d.add_layer()
        cw = self.sidebar.content_width(280, 15, 1)
        self.assertEqual(cw, 263)
        self.assertEqual(self.sidebar.min_dock_width(cw, 15, 1), 280)
        flows = self._flows(sb)
        self.assertGreaterEqual(len(flows), 6)  # header, rotate, layer rows, option rows, mask footer
        for flow in flows:
            self.assertLessEqual(flow.minimumSize().width(), cw)
            flow.arrange(0, 0, cw)
            for w in flow.widgets():
                if not w.isHidden():
                    self.assertGreaterEqual(w.geom.x(), 0)
                    self.assertLessEqual(w.geom.right(), cw - 1, getattr(w, "text", lambda: w)())
            self.assertGreater(flow.heightForWidth(cw), 0)
        load, undo = sb.binds["loadProject"].widgets[0], sb.binds["undo"].widgets[0]
        self.assertGreater(load.geom.y(), undo.geom.y())  # the header row wrapped
        rm = sb.layers.rows[-1]["remove"]
        self.assertLessEqual(rm.geom.right(), cw - 1)  # a layer row's last button
        # The Mode combo can shrink: its minimum is a few characters, not its longest option.
        self.assertTrue(hasattr(self.sidebar.QtWidgets.QComboBox, "AdjustToMinimumContentsLengthWithIcon"))

    def test_flow_rows(self):
        f = self.sidebar.flow_rows
        self.assertEqual(f([50, 50, 50], 200, 6), [[0, 1, 2]])
        self.assertEqual(f([50, 50, 50], 150, 6), [[0, 1], [2]])
        self.assertEqual(f([300, 20], 100, 6), [[0], [1]])  # too wide: a row of its own
        self.assertEqual(f([], 100), [])


# -- gallery.py ----------------------------------------------------------------------
class GalleryFilter(unittest.TestCase):
    def setUp(self):
        from texturizer import gallery

        self.g = gallery

    def test_all_shows_customs_then_categories(self):
        secs = self.g.filter_entries(fake_library())
        self.assertEqual([t for t, _ in secs], ["Your custom textures", "Geometric", "Patterns", "Fabric", "Natural",
                                                "Grip", "Tires"])

    def test_search_and_filters(self):
        lib = fake_library()
        flat = lambda secs: [e["id"] for _, es in secs for e in es]  # noqa: E731
        self.assertEqual(flat(self.g.filter_entries(lib, "all", "bar")), ["bark"])
        self.assertEqual(flat(self.g.filter_entries(lib, "all", "GRIP")), ["knurling"])  # category label
        self.assertEqual(flat(self.g.filter_entries(lib, "patterns")), ["aztec"])
        self.assertEqual(flat(self.g.filter_entries(lib, "custom")), ["custom:logo"])
        self.assertEqual(flat(self.g.filter_entries(lib, "favourites", "", ["bark", "nope", "aztec"])), ["bark", "aztec"])
        self.assertEqual(self.g.filter_entries(lib, "all", "zzz"), [])
        self.assertEqual(self.g.empty_note("favourites", ""), controls.STRINGS["ui.noFavourites"])
        self.assertEqual(self.g.empty_note("all", "zzz"), "No textures match your search.")
        self.assertEqual([f for f, _ in self.g.FILTERS][:3], ["all", "favourites", "custom"])

    def test_badges(self):
        b = {e["id"]: self.g.badge(e)[0] for e in fake_library()}
        self.assertEqual(b, {"hexagons": "CNC Kitchen", "knurling": "CNC Kitchen", "aztec": "HP", "bark": "CC0",
                             "basket": "FF", "tread": "CNC Kitchen", "custom:logo": "Yours"})

    def test_favourites_store(self):
        path = os.path.join(tempfile.mkdtemp(), "f.json")
        f = self.g.Favourites(path=path, entries=fake_library())
        self.assertEqual(f.shown(fake_library()), ["knurling", "hexagons"])
        self.assertTrue(f.toggle("bark"))
        self.assertEqual(self.g.Favourites(path=path).ids[-1], "bark")
        f.reset(fake_library())
        self.assertNotIn("bark", self.g.Favourites(path=path).ids)

    def test_columns_follow_width(self):
        c = self.g.columns_for
        self.assertEqual(c(0, 80), 1)  # never none, even before the dock is shown
        self.assertEqual(c(79, 80), 1)
        self.assertEqual(c(80 * 2 + self.g.SPACING, 80), 2)
        self.assertEqual(c(80 * 2 + self.g.SPACING - 1, 80), 1)
        self.assertEqual(c(1000, 80), 11)
        self.assertEqual(c(1000, 80, most=4), 4)  # the sidebar's favourites stay BumpMesh's 4 at most
        self.assertEqual([self.g.rows_for(n, 3) for n in (0, 1, 3, 4, 7)], [0, 1, 1, 2, 3])


class GalleryDock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gallery, cls.sidebar = load_gui()

    @classmethod
    def tearDownClass(cls):
        unload_gui()

    def test_dock_filters_searches_and_picks(self):
        d = draft.Draft()
        fav = self.gallery.Favourites(path=os.path.join(tempfile.mkdtemp(), "f.json"), entries=fake_library())
        g = self.gallery.GalleryDock(fake_library, fav, draft=d)
        self.assertEqual(g.windowTitle(), "Texture Gallery")
        self.assertEqual(g.search.placeholderText(), "Search textures")
        self.assertEqual(len(g.tiles), 7)
        self.assertEqual(g.fav_count.text(), "Favorites: 2")
        g.search.setText("aztec")
        self.assertEqual(list(g.tiles), ["aztec"])
        self.assertEqual(g.tiles["aztec"].badge, "HP")
        g.tiles["aztec"].click()
        self.assertEqual(d.layer()["texture"], "aztec")
        self.assertTrue(g.tiles["aztec"].isChecked())
        g.stars["aztec"].click()
        self.assertEqual(g.fav_count.text(), "Favorites: 3")
        g.search.setText("")
        g.chips["favourites"].click()
        self.assertEqual(sorted(g.tiles), ["aztec", "hexagons", "knurling"])
        g.chips["tires"].click()
        self.assertEqual(list(g.tiles), ["tread"])
        g.chips["custom"].click()
        self.assertEqual(list(g.tiles), ["custom:logo"])
        self.assertIn("Hero Patterns", g.credits.text())
        self.assertIn("Stefan Hermann (CNC Kitchen)", g.credits.text())
        g.done.click()
        self.assertTrue(g.isHidden())

    @unittest.skipIf(REAL_QT, "reads the stand-in Qt's own layout records (_layout, children_widgets)")
    def test_layout_and_turntable(self):
        spins = []
        ok = [True]

        def spin(on):
            spins.append(on)
            return ok[0]

        fav = self.gallery.Favourites(path=os.path.join(tempfile.mkdtemp(), "f.json"), entries=fake_library())
        g = self.gallery.GalleryDock(fake_library, fav, draft=draft.Draft(), spin=spin)
        body = g.widget().children_widgets()
        # The dock's title bar names it; the body has no second Texture Gallery.
        self.assertNotIn("Texture Gallery", [w.text() for w in body if isinstance(w, self.gallery.QtWidgets.QLabel)])
        self.assertIn(g.hint, body)  # the hint has its own row, not squeezed beside the count
        self.assertIsInstance(g.fav_reset, self.gallery.QtWidgets.QPushButton)
        self.assertTrue(g.turntable.isEnabled())
        g.turntable.click()
        self.assertEqual(spins, [True])
        self.assertTrue(g.turntable.isChecked())
        g.close()  # closing the gallery stops the spin
        self.assertEqual(spins, [True, False])
        ok[0] = False  # no 3D view: the button does not stay pressed
        g.turntable.click()
        self.assertFalse(g.turntable.isChecked())

    @staticmethod
    def _geometric_library():
        lib = fake_library()
        lib += [dict(lib[0], id=f"geo{i}", name=f"Geo {i}") for i in range(8)]  # 9 geometric tiles: several rows
        return lambda: lib

    def _grids(self, g):
        body = g.scroll.widget()
        if REAL_QT:
            return body.findChildren(self.gallery.ReflowGrid)
        return [w for w in body.children_widgets() if isinstance(w, self.gallery.ReflowGrid)]

    @unittest.skipIf(REAL_QT, "models Qt's QWidgetItem on the stand-in")
    def test_grids_get_their_height_in_a_narrow_sidebar(self):
        # Found live in a 350 px Combo View tab: the gallery showed its group
        # headers stacked with no tiles, and a category chip an empty list.
        # Qt's QWidgetItem asks a widget that has a layout for that layout's
        # height-for-width (a grid of fixed tiles has none: 0) and not the
        # widget's own heightForWidth, so a ReflowGrid holding a QGridLayout
        # got zero height in the gallery's column.
        def item_hfw(w, width):  # QWidgetItem::heightForWidth
            return 0 if w._layout is not None else w.heightForWidth(width)

        fav = self.gallery.Favourites(path=os.path.join(tempfile.mkdtemp(), "f.json"), entries=fake_library())
        g = self.gallery.GalleryDock(self._geometric_library(), fav, draft=draft.Draft())
        for flt in ("all", "geometric"):
            g.set_filter(flt)
            grids = self._grids(g)
            self.assertTrue(grids)
            for grid in grids:
                cw, ch = grid.cell.width(), grid.cell.height()
                cols = self.gallery.columns_for(330, cw)
                rows = self.gallery.rows_for(len(grid.items), cols)
                self.assertEqual(item_hfw(grid, 330), rows * ch + (rows - 1) * self.gallery.SPACING)
                grid._place(330)  # what its resizeEvent does
                self.assertEqual(grid.cols, cols)
                for k, cell in enumerate(grid.items):
                    self.assertEqual((cell.geom.x(), cell.geom.y()),
                                     ((k % cols) * (cw + self.gallery.SPACING), (k // cols) * (ch + self.gallery.SPACING)))
                    self.assertLessEqual(cell.geom.right(), 329)
        self.assertEqual(len(g.tiles), 9)

    @unittest.skipUnless(REAL_QT, "lays out real widgets")
    def test_tiles_show_in_a_narrow_sidebar(self):
        QtWidgets = self.gallery.QtWidgets
        fav = self.gallery.Favourites(path=os.path.join(tempfile.mkdtemp(), "f.json"), entries=fake_library())
        g = self.gallery.GalleryDock(self._geometric_library(), fav, draft=draft.Draft())
        tab = QtWidgets.QWidget()
        QtWidgets.QVBoxLayout(tab).addWidget(g.widget())
        tab.resize(350, 700)
        tab.show()
        for flt in ("all", "geometric"):
            g.set_filter(flt)
            for _ in range(5):
                QtWidgets.QApplication.processEvents()
            for grid in self._grids(g):
                self.assertGreaterEqual(grid.height(), grid.heightForWidth(grid.width()))
                self.assertGreater(grid.height(), 0)
                self.assertLessEqual(grid.width(), 350)
            for t in g.tiles.values():
                self.assertTrue(t.isVisible())
        self.assertGreater(self._grids(g)[0].cols, 1)  # as many columns as fit, not one
        tab.close()


if __name__ == "__main__":
    unittest.main()
