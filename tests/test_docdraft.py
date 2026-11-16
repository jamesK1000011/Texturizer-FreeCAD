# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
"""The draft in the FreeCAD document (docdraft), on a stand-in document that
keeps FreeCAD's transaction rules: a change is recorded only inside an open
transaction, openTransaction commits one already open, undo commits an open
one first, and Undo / Redo set the properties back (slotChangedObject while
Transacting) then signal slotUndoDocument / slotRedoDocument. So Edit >
Undo (Std_Undo) undoes texture edits, one drag is one Undo, and the sidebar
follows the document."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from texturizer import docdraft, draft  # noqa: E402

_MISSING = object()


class Obj:
    def __init__(self, doc, name):
        object.__setattr__(self, "_p", {})
        object.__setattr__(self, "_kinds", {})
        object.__setattr__(self, "Document", doc)
        object.__setattr__(self, "Name", name)
        object.__setattr__(self, "Label", name)

    @property
    def PropertiesList(self):  # noqa: N802 (FreeCAD)
        return list(self._p)

    def addProperty(self, kind, prop, group, tip):  # noqa: N802 (FreeCAD)
        self._p[prop] = ""
        self._kinds[prop] = kind

    def setEditorMode(self, prop, mode):  # noqa: N802 (FreeCAD)
        pass

    def __getattr__(self, k):
        p = object.__getattribute__(self, "_p")
        if k in p:
            return p[k]
        raise AttributeError(k)

    def __setattr__(self, k, v):
        if k not in self._p:
            object.__setattr__(self, k, v)
            return
        if self._kinds[k] == "App::PropertyFileIncluded" and v:
            v = self.Document.include(v)
        self.Document.record(self, k)
        self._p[k] = v
        self.Document.changed(self, k)


class Doc:
    Name = "Unnamed"

    def __init__(self):
        self.Objects, self.observers = [], []
        self.undos, self.redos = [], []
        self.open = None   # [name, [(obj, prop, old) or (obj, "created")]]
        self._tx = False
        self.dir = tempfile.mkdtemp(prefix="fakedoc-")
        self._n = 0

    def include(self, path):   # PropertyFileIncluded: a copy of its own
        self._n += 1
        dst = os.path.join(self.dir, f"{self._n}-{os.path.basename(path)}")
        shutil.copy(path, dst)
        return dst

    def addObject(self, kind, name):  # noqa: N802 (FreeCAD)
        o = Obj(self, name if not any(x.Name == name for x in self.Objects) else f"{name}{len(self.Objects):03d}")
        self.Objects.append(o)
        if self.open:
            self.open[1].append((o, "created"))
        return o

    def getObject(self, name):  # noqa: N802 (FreeCAD)
        return next((o for o in self.Objects if o.Name == name), None)

    def record(self, o, prop):
        if self.open and not self._tx and not any(r[0] is o and r[1] == prop for r in self.open[1]):
            self.open[1].append((o, prop, o._p[prop]))

    def changed(self, o, prop):
        for ob in self.observers:
            ob.slotChangedObject(o, prop)

    @property
    def Transacting(self):  # noqa: N802 (FreeCAD)
        return self._tx

    @property
    def HasPendingTransaction(self):  # noqa: N802 (FreeCAD)
        return self.open is not None

    @property
    def UndoCount(self):  # noqa: N802 (FreeCAD)
        return len(self.undos)

    @property
    def RedoCount(self):  # noqa: N802 (FreeCAD)
        return len(self.redos)

    @property
    def UndoNames(self):  # noqa: N802 (FreeCAD)
        return [u[0] for u in reversed(self.undos)]

    def openTransaction(self, name):  # noqa: N802 (FreeCAD)
        if self.open:
            self.commitTransaction()
        self.open = [name, []]

    def commitTransaction(self):  # noqa: N802 (FreeCAD)
        t, self.open = self.open, None
        if t and t[1]:
            self.undos.append(t)
            self.redos.clear()

    def abortTransaction(self):  # noqa: N802 (FreeCAD)
        t, self.open = self.open, None
        if t:
            self._roll(t)

    def _roll(self, t):
        back = [t[0], []]
        self._tx = True
        for r in reversed(t[1]):
            if r[1] == "created":
                self.Objects.remove(r[0])
                back[1].append((r[0], "deleted"))
            elif r[1] == "deleted":
                self.Objects.append(r[0])
                back[1].append((r[0], "created"))
            else:
                o, prop, old = r
                back[1].append((o, prop, o._p[prop]))
                o._p[prop] = old
                self.changed(o, prop)
        self._tx = False
        return back

    def undo(self):
        if self.open:
            self.commitTransaction()
        if self.undos:
            self.redos.append(self._roll(self.undos.pop()))
            for ob in self.observers:
                ob.slotUndoDocument(self)

    def redo(self):
        if self.redos:
            self.undos.append(self._roll(self.redos.pop()))
            for ob in self.observers:
                ob.slotRedoDocument(self)


class BareDoc(Doc):
    """No doc.Transacting to read."""

    @property
    def Transacting(self):  # noqa: N802
        raise AttributeError("Transacting")


class DraftInDocument(unittest.TestCase):
    DOC = Doc

    def setUp(self):
        self.doc = self.DOC()
        self.d = draft.Draft()
        self.queue = []   # as the window's QTimer.singleShot(0): after the step
        self.sync = docdraft.DocSync(self.d, lambda: self.doc, post=self.queue.append)
        self.doc.observers.append(docdraft.Observer())
        real_undo, real_redo = self.doc.undo, self.doc.redo
        self.doc.undo = lambda: (real_undo(), self.idle())
        self.doc.redo = lambda: (real_redo(), self.idle())
        self.events = []
        self.d.subscribe(self.events.append)
        self.d.set_part("Body")

    def idle(self):
        while self.queue:
            self.queue.pop(0)()

    def tearDown(self):
        self.sync.detach()
        shutil.rmtree(self.doc.dir, ignore_errors=True)

    def stored(self):
        return self.sync.read(docdraft.find(self.doc, "Body"))

    def test_the_part_gets_a_hidden_object_and_no_undo_step(self):
        o = docdraft.find(self.doc, "Body")
        self.assertIsNotNone(o)
        self.assertEqual(o.Visibility, False)
        self.assertEqual(self.stored(), self.d.to_dict())
        self.assertEqual(self.doc.UndoCount, 0)   # its starting state is not a step
        self.assertFalse(self.d.history)

    def test_std_undo_undoes_a_texture_edit_and_redo_brings_it_back(self):
        self.d.set("amplitude", 1.25)
        self.assertEqual(self.doc.UndoNames[0], "Texture: Change " + docdraft.controls.BY_KEY["amplitude"]["label"].replace(" ⓘ", "").strip())
        self.assertEqual(self.stored()["layers"][0]["settings"]["amplitude"], 1.25)
        before = self.d.get("amplitude")
        self.doc.undo()                     # what Std_Undo runs
        self.assertNotEqual(self.d.get("amplitude"), before)
        self.assertEqual(self.d.get("amplitude"), draft.layer_defaults()["amplitude"])
        ev = self.events[-1]
        self.assertEqual(ev["kind"], "undo")
        self.assertEqual(ev["value"]["keys"], ["amplitude"])   # the preview reruns from that stage
        self.doc.redo()
        self.assertEqual(self.d.get("amplitude"), 1.25)
        self.assertEqual(self.events[-1]["kind"], "redo")

    def test_a_drag_is_one_transaction(self):
        for v in (0.3, 0.6, 0.9, 1.2):
            self.d.set("amplitude", v, coalesce=True)
            self.assertTrue(self.doc.HasPendingTransaction)   # open from the first move
        self.d.end_coalesce()                                # the release
        self.assertFalse(self.doc.HasPendingTransaction)
        self.assertEqual(self.doc.UndoCount, 1)
        self.doc.undo()
        self.assertEqual(self.d.get("amplitude"), draft.layer_defaults()["amplitude"])

    def test_a_ring_drag_and_a_wheel_run_are_one_step_each(self):
        for deg in (5, 10, 15):
            self.d.set_frame(rotation=[deg, 0, 0], coalesce=("ring", 1))
        self.d.update({"scaleU": 3.0}, coalesce=("wheel", 1))   # a new run ends the ring's
        self.d.update({"scaleU": 4.0}, coalesce=("wheel", 1))
        self.d.end_coalesce()
        self.assertEqual(self.doc.UndoCount, 2)
        self.doc.undo()
        self.assertEqual(self.d.frame["rotation"], [15.0, 0, 0])
        self.doc.undo()
        self.assertEqual(self.d.frame["rotation"], [0.0, 0, 0])

    def test_a_tool_is_one_step_and_joins_an_open_transaction(self):
        self.d.update({"amplitude": 0.8, "scaleU": 2.0})   # a batch: one write
        self.assertEqual(self.doc.UndoCount, 1)
        self.doc.openTransaction("Texturizer: texture_set")   # the runner's
        self.d.update({"amplitude": 0.4})
        self.d.set_layer(0, name="Grip")
        self.assertTrue(self.doc.HasPendingTransaction)       # not split, not closed under it
        self.doc.commitTransaction()
        self.assertEqual(self.doc.UndoNames[0], "Texturizer: texture_set")
        self.doc.undo()
        self.assertEqual((self.d.get("amplitude"), self.d.layers[0]["name"]), (0.8, self.d.layers[0]["name"]))
        self.assertNotEqual(self.d.layers[0]["name"], "Grip")

    def test_masks_go_to_a_file_and_undo(self):
        self.d.mask_set({"faces": ["Face2"], "triangles": [3, 4], "soft": {"corners": [9, 10], "values": [0.5, 0.25]}})
        o = docdraft.find(self.doc, "Body")
        self.assertNotIn("Face2", o.Draft)              # the masks are in the file
        self.assertTrue(os.path.isfile(o.Masks))
        self.assertEqual(self.stored()["layers"][0]["mask"]["soft"]["values"], [0.5, 0.25])
        self.assertEqual(self.doc.UndoNames[0], "Texture: Paint")
        self.doc.undo()
        self.assertEqual(self.d.mask_count(), 0)
        self.doc.redo()
        self.assertEqual(self.d.mask()["faces"], ["Face2"])

    def test_layers_reset_and_draft_undo_go_through_the_document(self):
        self.d.add_layer()
        self.d.reset()
        self.assertEqual(self.doc.UndoNames[:2], ["Texture: Reset to Defaults", "Texture: Layers"])
        self.assertTrue(self.d.can_undo())
        self.assertTrue(self.d.undo())        # the agent's or a key's undo: the document's
        self.assertEqual(len(self.d.layers), 2)
        self.assertTrue(self.d.can_redo())
        self.assertTrue(self.d.redo())
        self.assertEqual(len(self.d.layers), 1)

    def test_panel_state_and_layer_choice_make_no_step(self):
        n = self.doc.UndoCount
        self.d.add_layer()
        self.d.set_active(0)
        self.d.set("exclusionTool", "brush")
        self.assertEqual(self.doc.UndoCount, n + 1)

    def test_a_refused_batch_leaves_nothing(self):
        with self.assertRaises(KeyError):
            with self.d.batch():
                self.d.set("amplitude", 0.9)
                raise KeyError("refused")
        self.assertEqual(self.doc.UndoCount, 0)
        self.assertEqual(self.stored(), self.d.to_dict())

    def test_a_new_session_reads_the_draft_back(self):
        self.d.set("amplitude", 1.5)
        self.d.mask_add(faces=["Face3"])
        d2 = draft.Draft()
        self.sync.detach()
        self.sync = docdraft.DocSync(d2, lambda: self.doc, post=self.queue.append)
        d2.set_part("Body")                     # the reopened document's sidebar
        self.assertEqual(d2.get("amplitude"), 1.5)
        self.assertEqual(d2.mask()["faces"], ["Face3"])
        self.assertEqual(len(docdraft.find(self.doc, "Body").Document.Objects), 1)

    def test_a_scripted_change_reloads(self):
        o = docdraft.find(self.doc, "Body")
        data = self.d.to_dict()
        data["layers"][0]["settings"]["amplitude"] = 0.7
        o.Draft = docdraft.split(data)[0]
        self.idle()
        self.assertEqual(self.d.get("amplitude"), 0.7)

    def test_without_freecad_the_draft_keeps_its_own_undo(self):
        self.assertIsNone(docdraft.attach(draft.Draft()))
        d = draft.Draft()
        d.set("amplitude", 1.1)
        self.assertTrue(d.undo())


class LikeFreeCAD(DraftInDocument):
    """FreeCAD 1.1.3's signals as they may come: a fresh Python wrapper of
    the document per signal, no doc.Transacting, and no slotRedoDocument
    reaching Python: the property changes alone bring the draft back."""
    DOC = BareDoc

    def setUp(self):
        super().setUp()
        doc = self.doc

        class Wrapper:   # the same document, another object
            Name = doc.Name

        class Obs(docdraft.Observer):
            def slotUndoDocument(self, d):  # noqa: N802
                super().slotUndoDocument(Wrapper())

            def slotRedoDocument(self, d):  # noqa: N802
                pass

        doc.observers[:] = [Obs()]

    def test_std_undo_undoes_a_texture_edit_and_redo_brings_it_back(self):
        self.d.set("amplitude", 1.25)
        self.doc.undo()
        self.assertEqual(self.d.get("amplitude"), draft.layer_defaults()["amplitude"])
        self.doc.redo()
        self.assertEqual(self.d.get("amplitude"), 1.25)


class GalleryPick(DraftInDocument):
    def test_a_tile_then_undo_then_redo(self):
        """The Hexagons tile (texture_set through the runner, in its own
        transaction), Std_Undo back to Knurling, Std_Redo to Hexagons again;
        one reload each, as undo / redo events (no full reload)."""
        self.d.set_texture("Knurling")
        self.doc.openTransaction("Texturizer: texture_set")
        self.d.set_texture("Hexagons", defaults={"scaleU": 6})
        self.doc.commitTransaction()
        self.assertEqual(self.doc.UndoNames[0], "Texturizer: texture_set")
        n = len(self.events)
        self.doc.undo()
        self.assertEqual(self.d.layer()["texture"], "Knurling")
        self.assertEqual([e["kind"] for e in self.events[n:]], ["undo"])
        self.assertEqual(self.events[-1]["value"]["keys"], ["scaleU", "texture"])
        self.doc.redo()
        self.assertEqual(self.d.layer()["texture"], "Hexagons")
        self.assertEqual(self.d.get("scaleU"), 6)
        self.assertEqual([e["kind"] for e in self.events[n:]], ["undo", "redo"])
        self.assertFalse(self.d.can_redo())
        self.assertTrue(self.d.can_undo())

    def test_an_echo_of_the_reload_writes_no_step(self):
        """A widget set back to the value the undo gave writes nothing, so
        FreeCAD's Redo stays."""
        self.d.set("amplitude", 1.25)
        self.doc.undo()
        self.d.set("amplitude", self.d.get("amplitude"))
        self.d.set_texture(self.d.layer()["texture"])
        self.assertTrue(self.d.can_redo())


if __name__ == "__main__":
    unittest.main()
