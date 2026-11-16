# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""Where FreeCAD already owns a BumpMesh control, FreeCAD's is the one used.
controls.py stays BumpMesh's table as generated; this says which of its
rows the sidebar and the toolbar leave out or name for FreeCAD:

    OWNED    not built: FreeCAD has it (Undo / Redo: Edit > Undo undoes texture
             edits, the draft being in the document; Export STL: File > Export
             of the object Add to Document makes)
    LABELS   the button's name here (the draft is saved with the document, so
             BumpMesh's project file is for sharing with the BumpMesh web app;
             models come from the document, never a file dialog)
    TIPS     their tooltips
"""
OWNED = frozenset({"undo", "redo", "exportStl"})
LABELS = {
    "saveProject": "Export BumpMesh Project…",
    "loadProject": "Import BumpMesh Project…",
    "loadModel": "Use Selected Part",
}
TIPS = {
    "saveProject": "Write the texture settings and masks (and the model) as a BumpMesh project, "
                   "to open in the BumpMesh web app. The draft itself is saved with the FreeCAD document.",
    "loadProject": "Read a BumpMesh project's texture settings and masks (and its model) into this document.",
    "loadModel": "Texture the selected solid (else this document's last textured part, else the one solid shown). "
                 "Import an STL with File > Import first.",
}


def label(c):
    """The control's label as shown here (None: BumpMesh's own)."""
    return LABELS.get(c["key"])


def tooltip(c):
    return TIPS.get(c["key"], c.get("tooltip"))
