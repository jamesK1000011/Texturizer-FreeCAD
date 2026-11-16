# SPDX-License-Identifier: AGPL-3.0-only
# Texturizer for FreeCAD, at start: old documents' Textured objects (a
# Mesh::Feature with a string SourceBody) become linked features when they
# open (texturizer.linked.Observer), with or without the GUI. Its commands
# come with the GUI build.
try:
    import FreeCAD as _fc
    from texturizer import linked as _linked

    _fc.addDocumentObserver(_linked.Observer())
except Exception:  # noqa: BLE001 — never stop FreeCAD's start
    pass
