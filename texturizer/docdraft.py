# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The draft lives in the FreeCAD document, so FreeCAD's Edit > Undo
(Std_Undo / Std_Redo) undoes texture edits too: one Undo for the model and
its texture.

Each textured part has one hidden App::FeaturePython (Name TextureDraft,
not in the tree) holding its draft:

    TexturePart  the part's Name
    Draft        the draft as JSON (draft.to_dict), masks left out
    Masks        the layers' masks as a JSON file (App::PropertyFileIncluded):
                 the brush's soft coverage and split triangles can be large

DocSync keeps live.draft() and that object together:

  - an edit is written inside doc.openTransaction("Texture: <verb>") and
    commitTransaction; a coalesced run (a slider, ring, stamp or wheel drag)
    keeps its transaction open from the first move to the release, so one
    drag is one Undo; a batch (one texture_* tool) is one write. When a
    transaction is already open (Agent Link's or the local runner's, one per
    tool), the write joins it rather than splitting it.
  - Undo / Redo of the document reloads the draft from the object
    (draft.apply), so the sidebar, the rings and the preview follow; the
    preview reruns from the stage the step changed (draft.changes).
  - a part the draft takes for the first time in this document gets its
    object made outside any transaction (its starting state is not a step);
    a part with a stored draft (the document reopened) loads it when the
    session starts on it.

The draft's own undo stack is off while this runs (draft.history = False);
without FreeCAD (the tests, a plain Python) the draft keeps its own.

No Qt import here; FreeCAD is passed in (doc_fn), so the tests drive it with
a stand-in document.
"""
import json
import os
import tempfile
import time

from . import controls, timing

NAME = "TextureDraft"
GROUP = "Texturizer"
PROPS = (("App::PropertyString", "TexturePart", "The part this draft textures (its Name)"),
         ("App::PropertyString", "Draft", "The Texturizer draft (JSON), masks apart"),
         ("App::PropertyFileIncluded", "Masks", "The layers' masks (JSON file): faces, triangles and brush paint"))

_CURRENT = [None]   # the DocSync the one document observer forwards to
_OBSERVER = [None]


def verb(ev):
    """The Undo menu's name for a draft event: "Texture: <verb>"."""
    kind, key = ev.get("kind"), ev.get("key")
    if kind == "set" and key:
        c = controls.BY_KEY.get(key) or {}
        label = (c.get("label") or key).replace(" ⓘ", "").strip()
        return f"Change {label}"
    if kind == "mask":
        return {"set": "Paint", "add": "Add Faces", "remove": "Remove Faces", "clear": "Clear Mask"}.get(key, "Mask")
    return {"frame": "Rotate", "part": "Use Part", "layers": "Layers", "layer": "Layer",
            "texture": "Pick Texture", "reset": "Reset to Defaults", "load": "Load"}.get(kind, "Edit")


def split(data):
    """(draft JSON with masks left out, masks JSON) from draft.to_dict()."""
    data = dict(data)
    layers, masks = [], []
    for L in data.get("layers") or []:
        L = dict(L)
        masks.append(L.pop("mask", None) or {"faces": [], "triangles": []})
        layers.append(L)
    data["layers"] = layers
    return json.dumps(data, sort_keys=True), json.dumps(masks, sort_keys=True)


def join(draft_json, masks_json):
    data = json.loads(draft_json)
    masks = json.loads(masks_json) if masks_json else []
    for i, L in enumerate(data.get("layers") or []):
        L["mask"] = masks[i] if i < len(masks) and masks[i] else {"faces": [], "triangles": []}
    return data


def is_draft_object(o):
    try:
        return "TexturePart" in o.PropertiesList and "Draft" in o.PropertiesList
    except Exception:  # noqa: BLE001 — a deleted object
        return False


def find(doc, part):
    for o in getattr(doc, "Objects", None) or []:
        if is_draft_object(o) and o.TexturePart == part:
            return o
    return None


class DocSync:
    def __init__(self, draft, doc_fn, post=None):
        self.draft = draft
        self.doc_fn = doc_fn
        self._post = post
        self.bound = None        # (document Name, part) the draft is written to
        self.edited = False      # the draft changed before it had a part
        self._mine = False       # a transaction this opened is still open
        self._verb = None        # the step being gathered (a batch)
        self._dirty = False
        self._writing = self._applying = False
        self._touched = []       # draft objects an undo / redo changed
        self._pending = False    # a reload is posted
        self._kind = None        # "undo" / "redo" as the document said
        self._cache = {}         # object Name -> (Masks path, masks JSON)
        self._tmp = None
        draft.history = False
        draft.sync = self
        self._unsub = draft.subscribe(self._on_draft)
        self._unclose = draft.on_close(self._on_close)
        _CURRENT[0] = self

    def detach(self):
        self._unsub()
        self._unclose()
        self.draft.history = True
        self.draft.sync = None
        if _CURRENT[0] is self:
            _CURRENT[0] = None

    def post(self, fn):
        (self._post or POST[0] or (lambda f: f()))(fn)

    # -- the document ------------------------------------------------------------
    def doc(self):
        try:
            return self.doc_fn()
        except Exception:  # noqa: BLE001
            return None

    def obj(self, doc=None):
        doc = doc or self.doc()
        if doc is None or self.bound is None or self.bound[0] != doc.Name:
            return None
        return find(doc, self.bound[1])

    def _make(self, doc, part):
        o = doc.addObject("App::FeaturePython", NAME)
        for kind, prop, tip in PROPS:
            o.addProperty(kind, prop, GROUP, tip)
            try:
                o.setEditorMode(prop, 2)   # not in the property editor
            except Exception:  # noqa: BLE001
                pass
        o.TexturePart = part
        src = None
        try:
            src = doc.getObject(part)
        except Exception:  # noqa: BLE001
            pass
        o.Label = f"Texture Draft {getattr(src, 'Label', part)}"
        for target, attr, val in ((o, "Visibility", False), (getattr(o, "ViewObject", None), "ShowInTree", False)):
            try:
                if target is not None:
                    setattr(target, attr, val)
            except Exception:  # noqa: BLE001
                pass
        return o

    def read(self, o):
        """The draft dict stored on o (None when empty)."""
        text = getattr(o, "Draft", "") or ""
        if not text:
            return None
        path = getattr(o, "Masks", "") or ""
        hit = self._cache.get(o.Name)
        if hit and hit[0] == path:
            masks = hit[1]
        else:
            masks = ""
            if path and os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    masks = f.read()
            self._cache[o.Name] = (path, masks)
        return join(text, masks)

    def _store(self, o, data):
        """Write data to o; False when it already held it (nothing written)."""
        text, masks = split(data)
        path = getattr(o, "Masks", "") or ""
        hit = self._cache.get(o.Name)
        if hit is None or hit[0] != path:
            self.read(o)
            hit = self._cache.get(o.Name)
        same_masks = hit is not None and hit[1] == masks and bool(path)
        if (getattr(o, "Draft", "") or "") == text and same_masks:
            return False
        self._writing = True
        try:
            if o.TexturePart != data.get("part"):
                o.TexturePart = data.get("part") or ""
            if (getattr(o, "Draft", "") or "") != text:
                o.Draft = text
            if not same_masks:
                if self._tmp is None:
                    self._tmp = tempfile.mkdtemp(prefix="texturizer-")
                f = os.path.join(self._tmp, "TextureMasks.json")
                with open(f, "w", encoding="utf-8") as fh:
                    fh.write(masks)
                o.Masks = f   # FreeCAD copies it into the document
                self._cache[o.Name] = (getattr(o, "Masks", "") or "", masks)
                if os.path.exists(f):
                    os.remove(f)
            try:
                o.purgeTouched()   # data, nothing to recompute
            except Exception:  # noqa: BLE001
                pass
        finally:
            self._writing = False
        return True

    # -- binding a part -------------------------------------------------------------
    def _bind(self, doc, event_kind):
        """The draft's part has an object to write to. A session starting on
        a part with a stored draft loads it; else the draft is written there
        as it stands, outside any transaction (a starting state, not a step)."""
        part = self.draft.part
        fresh = self.bound is None or self.bound[0] != doc.Name
        self.bound = (doc.Name, part)
        o = find(doc, part)
        if o is not None:
            stored = self.read(o)
            if stored is not None and fresh and not self.edited and event_kind == "part":
                stored["part"] = part
                self._apply(stored, "load")
            return
        if getattr(doc, "HasPendingTransaction", False):
            o = self._make(doc, part)   # inside the open tool's step
        else:
            self._writing = True
            try:
                o = self._make(doc, part)
            finally:
                self._writing = False
        self._store(o, self.draft.to_dict())
        self.edited = False

    # -- writing ----------------------------------------------------------------------
    def _on_draft(self, ev):
        kind = ev.get("kind")
        if self._applying or kind in ("undo", "redo", "active"):
            return
        if kind == "set" and (controls.BY_KEY.get(ev.get("key")) or {}).get("scope") == "ui":
            return   # panel state (the brush, the tool) is not saved
        doc = self.doc()
        if doc is None or not self.draft.part:
            self.edited = True
            return
        if self.bound != (doc.Name, self.draft.part):
            self._bind(doc, kind)
            if kind == "part":
                return
        if self._verb is None:
            self._verb = verb(ev)
        self._dirty = True
        if self.draft.batching() and not self.draft.coalescing():
            return   # the batch's close writes once
        self.flush(keep_open=self.draft.coalescing())

    def _on_close(self):
        """A coalesced run released, or a batch done: its one step."""
        self.flush(keep_open=False)

    def flush(self, keep_open=False):
        doc = self.doc()
        if doc is None:
            self._dirty, self._verb = False, None
            return
        if self._dirty:
            o = self.obj(doc)
            if o is None and self.draft.part:
                self.bound = None
                self._bind(doc, "edit")
                o = self.obj(doc)
            if o is not None:
                data = self.draft.to_dict()
                if self.read(o) != json.loads(json.dumps(data)) or not (getattr(o, "Masks", "") or ""):
                    if not getattr(doc, "HasPendingTransaction", False):
                        doc.openTransaction(f"Texture: {self._verb or 'Edit'}")
                        self._mine = True
                    with timing.stage("docdraft write"):
                        self._store(o, data)
            self._dirty = False
        if not keep_open:
            self._verb = None
            if self._mine:
                self._mine = False
                if getattr(doc, "HasPendingTransaction", False):
                    doc.commitTransaction()

    # -- undo / redo --------------------------------------------------------------------
    def _apply(self, data, kind):
        keep = self.draft.active
        if len(data.get("layers") or []) == len(self.draft.layers):
            data["active"] = keep   # the layer being edited stays the one shown
        self._applying = True
        try:
            with timing.stage(f"docdraft apply ({kind})"):
                self.draft.apply(data, kind)
        finally:
            self._applying = False

    def changed(self, obj, prop):
        """slotChangedObject: an undo or redo (or a script) set a draft object.
        It does not wait for slotUndoDocument / slotRedoDocument (nor read
        doc.Transacting): one reload is posted for the step, after it."""
        if self._writing or prop not in ("Draft", "Masks", "TexturePart") or not is_draft_object(obj):
            return
        timing.report(f"docdraft changed {obj.Name}.{prop}", 0.0)
        if obj.Name not in self._touched:
            self._touched.append(obj.Name)
        self._schedule(None)

    def undone(self, doc, kind):
        """slotUndoDocument / slotRedoDocument: says which it was; the reload
        is the one changed() posted (or one now, for this document)."""
        mine = self.doc()
        if doc is not None and mine is not None and getattr(doc, "Name", None) != getattr(mine, "Name", None):
            return   # another document's step
        self._schedule(kind)

    def _schedule(self, kind):
        if kind is not None:
            self._kind = kind
        if self._pending:
            return
        self._pending = time.perf_counter()
        self.post(self._run_reload)

    def _run_reload(self):
        kind, self._kind = self._kind, None
        since, self._pending = self._pending, False
        if since:
            timing.report("docdraft reload waited (signal to reload)", (time.perf_counter() - since) * 1000)
        with timing.stage(f"docdraft reload ({kind or 'change'})"):
            self.reload(kind or "undo")

    def reload(self, kind="undo"):
        """The draft from the document after a step: once, as an undo or
        redo event (draft.changes says what moved, so the preview reruns
        from that stage only and the sidebar syncs only that)."""
        doc = self.doc()
        touched, self._touched = self._touched, []
        self._mine, self._dirty, self._verb = False, False, None
        if doc is None:
            return
        cur = self.obj(doc)
        pick = None
        if cur is not None and (cur.Name in touched or not touched):
            pick = cur
        else:
            for name in reversed(touched):
                o = doc.getObject(name)
                if o is not None and is_draft_object(o):
                    pick = o
                    break
        if cur is None and pick is None:
            self.bound = None   # its object went with the step: the next edit makes it again
            return
        if pick is None:
            return
        with timing.stage("docdraft read"):
            data = self.read(pick)
        if data is None:
            return
        data["part"] = pick.TexturePart or data.get("part")
        self.bound = (doc.Name, data["part"])
        if json.loads(json.dumps(self.draft.to_dict())) == data:
            return
        self._apply(data, kind)

    # -- for the agent's summary ------------------------------------------------------------
    def can_undo(self):
        return bool(getattr(self.doc(), "UndoCount", 0))

    def can_redo(self):
        return bool(getattr(self.doc(), "RedoCount", 0))

    def undo(self):
        doc = self.doc()
        if doc is None or not getattr(doc, "UndoCount", 0):
            return False
        doc.undo()
        return True

    def redo(self):
        doc = self.doc()
        if doc is None or not getattr(doc, "RedoCount", 0):
            return False
        doc.redo()
        return True


class Observer:
    """The one FreeCAD document observer for the draft (addDocumentObserver
    keeps it for the session): forwards to the current DocSync."""

    def slotChangedObject(self, obj, prop):  # noqa: N802 (FreeCAD)
        s = _CURRENT[0]
        if s is not None and prop in ("Draft", "Masks", "TexturePart"):
            try:
                s.changed(obj, prop)
            except Exception:  # noqa: BLE001 — never raise into FreeCAD
                pass

    def slotUndoDocument(self, doc):  # noqa: N802 (FreeCAD)
        self._step(doc, "undo")

    def slotRedoDocument(self, doc):  # noqa: N802 (FreeCAD)
        self._step(doc, "redo")

    def _step(self, doc, kind):
        s = _CURRENT[0]
        if s is not None:
            try:
                timing.report(f"docdraft signal {kind}", 0.0)
                s.undone(doc, kind)
            except Exception:  # noqa: BLE001
                pass


def attach(draft, doc_fn=None, post=None):
    """Keep draft in FreeCAD's document when FreeCAD runs (else None: the
    draft keeps its own undo)."""
    try:
        import FreeCAD
    except ImportError:
        return None
    if not hasattr(FreeCAD, "addDocumentObserver"):
        return None   # a stand-in module (the tests)
    if _OBSERVER[0] is None:
        _OBSERVER[0] = Observer()
        FreeCAD.addDocumentObserver(_OBSERVER[0])
    return DocSync(draft, doc_fn or (lambda: FreeCAD.ActiveDocument), post=post)


POST = [None]   # the window sets a Qt deferral: Undo's signal can come before its last property
