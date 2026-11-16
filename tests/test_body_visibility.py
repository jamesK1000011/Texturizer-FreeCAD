# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The source body is hidden while its textured result is shown (as
PartDesign hides the previous feature), on stand-ins whose view objects
carry Visibility and tell the document observer of each change, as
FreeCAD does: hiding or deleting the result shows the body again, Undo of
the apply shows it again, a preview open on the body is told rather than
overridden, and texture_apply opens no dialog."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from texturizer import linked, live  # noqa: E402
import test_linked as tl  # noqa: E402  (not imported by name: its tests would run twice)

LObj, Shape = tl.LObj, tl.Shape

OBSERVER = linked.Observer()


class VO:
    """A view object: Visibility set tells the observer, as FreeCAD does."""

    def __init__(self, obj):
        self.__dict__["obj"] = obj
        self.__dict__["Visibility"] = True

    def __setattr__(self, k, v):
        self.__dict__[k] = v
        if k == "Visibility":
            OBSERVER.slotChangedObject(self.obj, "Visibility")


class Vis(unittest.TestCase):
    setUp_linked = tl.Linked.setUp
    tearDown = tl.Linked.tearDown
    ok = tl.Linked.ok
    textured = tl.Linked.textured

    def setUp(self):
        self.setUp_linked()
        self.doc.Transacting = False
        self.body.ViewObject = VO(self.body)
        linked._HIDDEN.clear()

    def _add(self, kind, name):
        o = tl.Linked._add(self, kind, name)
        o.ViewObject = VO(o)
        return o

    def delete(self, obj):
        OBSERVER.slotDeletedObject(obj)
        self.doc.removeObject(obj.Name)

    def test_apply_hides_the_body_and_says_so(self):
        self.ok("texture_draft", {})
        self.ok("texture_set", {"texture": "Knurling", "settings": {"refineLength": 2.5, "bottomAngleLimit": 0}})
        out = self.ok("texture_apply", {})
        self.assertTrue(out["bodyHidden"])
        self.assertIn("the body is hidden while its textured result is shown", out["note"].lower())
        self.assertFalse(self.body.ViewObject.Visibility)
        self.assertTrue(live.find(self.doc, out["object"]).ViewObject.Visibility)

    def test_hiding_the_result_shows_the_body_and_showing_it_hides_it(self):
        t = self.textured()
        t.ViewObject.Visibility = False
        self.assertTrue(self.body.ViewObject.Visibility)
        t.ViewObject.Visibility = True
        self.assertFalse(self.body.ViewObject.Visibility)

    def test_deleting_the_result_shows_the_body(self):
        t = self.textured()
        self.delete(t)
        self.assertTrue(self.body.ViewObject.Visibility)

    def test_a_second_shown_result_keeps_the_body_hidden(self):
        t = self.textured()
        out = self.ok("texture_apply", {"new": True})
        t2 = live.find(self.doc, out["object"])
        self.delete(t)
        self.assertFalse(self.body.ViewObject.Visibility)
        t2.ViewObject.Visibility = False
        self.assertTrue(self.body.ViewObject.Visibility)

    def test_undo_of_the_apply_shows_the_body(self):
        t = self.textured()
        # Undo removes the object while the document is transacting: no
        # per-object step, one reconcile at the end.
        self.doc.Transacting = True
        self.delete(t)
        self.assertFalse(self.body.ViewObject.Visibility)
        self.doc.Transacting = False
        OBSERVER.slotUndoDocument(self.doc)
        self.assertTrue(self.body.ViewObject.Visibility)

    def test_redo_hides_it_again(self):
        t = self.textured()
        self.doc.Transacting = True
        self.delete(t)
        self.doc.Transacting = False
        OBSERVER.slotUndoDocument(self.doc)
        self.doc.Objects.append(t)   # Redo brings the result back
        OBSERVER.slotRedoDocument(self.doc)
        self.assertFalse(self.body.ViewObject.Visibility)

    def test_undo_leaves_a_body_texturizer_never_hid(self):
        other = LObj(self.doc, "Other", "Other", Shape())
        other.ViewObject = VO(other)
        other.ViewObject.Visibility = False
        self.doc.Objects.append(other)
        self.textured()
        OBSERVER.slotUndoDocument(self.doc)
        self.assertFalse(other.ViewObject.Visibility)

    def test_a_person_hiding_the_body_is_not_undone(self):
        t = self.textured()
        t.ViewObject.Visibility = False      # body back
        self.body.ViewObject.Visibility = False   # the person hides it too
        self.assertFalse(self.body.ViewObject.Visibility)

    def test_headless_object_without_a_view(self):
        del self.body.ViewObject
        self.body.Visibility = True
        self.textured()
        self.assertFalse(self.body.Visibility)

    def test_the_window_preview_is_told_not_overridden(self):
        class C:
            showing, _body, running = "cpu", True, None

        class G:
            _own_vis = 0
            controller = C()

        self.ok("texture_draft", {})
        self.ok("texture_set", {"texture": "Knurling", "settings": {"refineLength": 2.5, "bottomAngleLimit": 0}})
        g = live.GUI = G()
        self.body.ViewObject.__dict__["Visibility"] = False   # the preview stands in for it
        self.ok("texture_apply", {})
        self.assertFalse(G.controller._body)   # closing the preview leaves it hidden
        self.assertEqual(g._own_vis, 0)

    def test_texture_apply_opens_no_dialog(self):
        """No Qt window during a tool call: texture_apply's path (handlers,
        linked, the engine) imports no Qt and makes no progress dialog."""
        root = os.path.join(os.path.dirname(HERE), "texturizer")
        for dirpath, _d, files in os.walk(root):
            for f in files:
                if f.endswith(".py"):
                    src = open(os.path.join(dirpath, f), encoding="utf-8").read()
                    self.assertNotIn("QProgressDialog", src, f)
        for f in ["handlers.py", "linked.py"] + [os.path.join("engine", n) for n in os.listdir(os.path.join(root, "engine"))
                                                 if n.endswith(".py")]:
            src = open(os.path.join(root, f), encoding="utf-8").read()
            for qt in ("QtWidgets", "QtGui", "PySide", "QMessageBox", "QDialog"):
                self.assertNotIn(qt, src, f)


if __name__ == "__main__":
    unittest.main()
