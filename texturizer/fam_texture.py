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
INFO = "Surface textures (BumpMesh by CNC Kitchen): a body written face by face with a mask of the faces to texture, and the textured mesh checked against the body."
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Watching live (agentlink/echo.py): the bench and the button for each tool,
# and its name in Activity. texture_check changes nothing but shows a result.
WORKBENCHES = {"texture_": "TexturizerWorkbench"}
COMMANDS = {"texture_prepare": "Texturizer_Prepare", "texture_check": "Texturizer_Check"}
AREAS = {"texture_": "Texture"}
SHOWN = ("texture_check",)


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
