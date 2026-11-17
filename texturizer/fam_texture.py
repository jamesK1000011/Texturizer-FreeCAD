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
            "texture_export": "Texturizer_Export3MF", "texture_prepare": "Texturizer_Prepare",
            "texture_check": "Texturizer_Check"}
AREAS = {"texture_": "Texture"}
SHOWN = ("texture_preview", "texture_check")
NO_BUTTON = ("texture_list",)
# Reads that open the Texturizer sidebar (handlers._watch) and so bring up
# the bench and light their button, as a changing call does: the echo
# lights a button only for a call that switches the bench (echo.before).
OPENS = ("texture_draft", "texture_set", "texture_mask", "texture_layer", "texture_preview")
# What each call did and came to, in the Activity tab's and the caption's
# words (agentlink/activity.py templates: the first whose {fields} are all in
# the arguments or the answer wins; " if {f}" / " if {f=value}" at the end
# only when f is given, and is value). The answer's own figures go in, so a
# check or an export does not keep an earlier call's words.
PHRASES = {
    "texture_preview": ["Stopped the texture preview if {cancelled}",
                        "Turntable on if {turntable=true}", "Turntable off if {turntable=false}",
                        "Turned the view {turntable} degrees",
                        "Suggested a refine length and triangle budget for {part} if {suggest}",
                        "Suggested a refine length and triangle budget if {suggest}",
                        "Previewed the texture on {part}: {triangles} triangles"],
    "texture_export": ["Export cancelled if {cancelled}",
                       "Saved the BumpMesh project {path} if {format=bumpmesh}",
                       "Exported {path}: {triangles} triangles"],
    "texture_prepare": ["Wrote {stl} from {body} for texturing: {triangles} triangles, {mode} {faces}"],
    "texture_apply": ["Updated {label}: {triangles} triangles if {updated}",
                      "Textured {body} as {label}"],
}
# What to select in the window after a read, by the answer's key: the part
# the preview stands for, the object exported, the body prepared (Agent
# Link's echo selects only what a read's arguments name; an echo that reads
# SELECTS selects these as well).
SELECTS = {"texture_preview": "part", "texture_export": "object", "texture_prepare": "body",
           "texture_check": "object"}
# Reads that drive the window on the way (the sidebar, the preview's Coin
# nodes and its worker and timers, the turntable): never run as one of
# Agent Link's jobs, whose worker thread must leave Qt alone. FreeCAD 1.1.3
# stopped (SIGBUS, a timer fired on a deleted object) after
# texture_preview { cancel } ran on a job's thread.
IN_PLACE = {
    "texture_draft": "opens the Texturizer sidebar and loads the part into its draft",
    "texture_set": "changes the draft the Texturizer sidebar and its preview show",
    "texture_mask": "changes the draft the Texturizer sidebar and its preview show",
    "texture_layer": "changes the draft the Texturizer sidebar and its preview show",
    "texture_preview": "draws the preview in the 3D view and starts or stops the preview's worker and timers",
}


def command_for(name, args):
    """The button that does what this call asks: the header row's Reset,
    Save / Load Project and Texture Gallery, Export 3MF, the Turntable;
    FreeCAD's File > Export for an STL; None for any other tool (COMMANDS)."""
    a = args or {}
    if name == "texture_export":
        fmt = str(a.get("format") or "").lower()
        return {"3mf": "Texturizer_Export3MF", "bumpmesh": "Texturizer_SaveProject", "stl": "Std_Export"}.get(fmt)
    if name == "texture_draft":
        if a.get("reset"):
            return "Texturizer_Reset"
        if a.get("project"):
            return "Texturizer_LoadProject"
        return "Texturizer_Texture"
    if name == "texture_set" and a.get("texture") and not any(a.get(k) for k in ("settings", "frame", "cylinder", "face", "stamp")):
        return "Texturizer_Gallery"
    if name == "texture_preview" and "turntable" in a:
        return "Texturizer_Turntable"
    return None


def _declare_in_place():
    """IN_PLACE told to Agent Link's jobs (jobs.in_place); an Agent Link
    without it runs them in place only as plain calls."""
    try:
        from agentlink import jobs
    except ImportError:
        return False
    fn = getattr(jobs, "in_place", None)
    if fn is None:
        return False
    for k, why in IN_PLACE.items():
        fn(k, why)
    return True


_declare_in_place()

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
