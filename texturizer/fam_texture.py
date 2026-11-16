# SPDX-License-Identifier: AGPL-3.0-only
# The texture tools for agents: the handlers in handlers.py (no Agent Link
# import there) bound to the session, their definitions in data/tools.json
# (Agent Link's tool-data format). Agent Link lends its ToolError and its
# face queries (facepick), so a face can be named or asked for by normal,
# area, ... Loaded by Agent Link only.
import os

from agentlink import facepick, hooks
from agentlink.families import picks
from agentlink.protocol import ToolError

from . import handlers

GROUP = "texture"
INFO = ("Surface textures with BumpMesh's engine (Stefan Hermann, CNC Kitchen), ported and run inside FreeCAD: "
        "a draft the Texturizer sidebar shows live (texture, settings, face masks, layers), a preview, "
        "the Textured object in the document, STL / 3MF / .bumpmesh files, and the textured mesh checked against the body.")
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Watching live (agentlink/echo.py, design 5.2): the bench and the button for
# each tool, its name in Activity, the tools that show a result, and the one
# with no button. The draft tools press Texturizer_Texture, which opens the
# sidebar before the first draft call (the tools open it too, for an echo
# that does not read COMMANDS).
WORKBENCHES = {"texture_": "TexturizerWorkbench"}
COMMANDS = {"texture_draft": "Texturizer_Texture", "texture_set": "Texturizer_Texture",
            "texture_mask": "Texturizer_Texture", "texture_layer": "Texturizer_Texture",
            "texture_preview": "Texturizer_Texture", "texture_apply": "Texturizer_AddToDocument",
            "texture_export": "Texturizer_AddToDocument", "texture_prepare": "Texturizer_Prepare",
            "texture_check": "Texturizer_Check"}
AREAS = {"texture_": "Texture"}
SHOWN = ("texture_preview", "texture_check")
NO_BUTTON = ("texture_list",)


def pick_faces(shape, q):
    """Faces by name or query (Agent Link's facepick), as one set."""
    want = set()
    for k, item in enumerate(q if isinstance(q, list) else [q]):
        item = {"faces": [item]} if isinstance(item, str) else item
        want |= {n for n, _ in facepick.pick(shape, item, f"faces.{k}" if isinstance(q, list) else "faces")}
    return want


def tools(session):
    handlers.lend(ToolError=ToolError, pick_faces=pick_faces, num=picks.num)
    return hooks.tools_from_data(DATA, GROUP, handlers.bound(session), "Texturizer")
