# SPDX-License-Identifier: AGPL-3.0-only
# Checks that Texturizer shares (red team 3, 19 Nov 2026):
#  - a file a tool reads by a field the hub does not check (texture.file,
#    mask, project, export.path): with Agent Link, its paths.in_file (lent
#    by the family layer as IN_FILE; paths.check for a write as CHECK);
#    alone, the fallback here refuses the credential and shell-settings
#    folders, ~/Library (but FreeCAD's and the slicers' own folders in
#    Application Support) and Agent Link's own state;
#  - text from a file to the agent: no Unicode format characters (Cf: zero
#    width, direction marks), no tag characters, no control characters,
#    capped;
#  - paths under the home folder shown as ~/... in answers and refusals.
# Plain Python: no FreeCAD, no Agent Link import.

import os
import re
import unicodedata

TEXT_CAP = 200
# The folders Agent Link's paths.SECRET_PARTS names: credentials and shell
# settings. Compared without case (macOS disks ignore case).
SECRET_PARTS = {".ssh", ".aws", ".gnupg", ".kube", ".docker", ".config", ".netrc", ".git", ".password-store", "keychains",
                ".zshrc", ".bashrc", ".profile", ".bash_profile", ".zprofile", ".env"}
# In ~/Library a tool reads only these folders (FreeCAD's user data, with
# Texturizer's own textures, and the slicers' presets).
LIBRARY_READ = ("library/application support/freecad", "library/application support/orcaslicer",
                "library/application support/bambustudio", "library/application support/prusaslicer")
# Agent Link's own state in its home (~/AgentLink or AGENTLINK_HOME): these
# folders, and files of these kinds at its top.
STATE_EXT = {".json", ".jsonl", ".log", ".lock", ".cfg", ".tmp", ".pid"}
STATE_DIRS = {"cookbooks", "cookbook-scans", "usage", "crash", "addons-previous", "stopped", "unsaved", "tests", "install",
              "addonmanager", "extensionrepos", "settings"}
NEVER_READ = {"printers.json", ".agentlink-printers.json", "memory-jobs.json"}

# Agent Link's paths.in_file(session, tool, path, key) and paths.check(tool,
# key, path, out_dir, write), lent by fam_texture; None alone.
IN_FILE = None
CHECK = None


def _real(p):
    return os.path.realpath(os.path.expanduser(p))


def _parts(p):
    return [x for x in str(p).replace("\\", "/").split("/") if x]


def _under(p, root):
    return p == root or p.startswith(root.rstrip("/") + "/")


def refusal(path, write=False):
    """Why the fallback refuses this path, or None."""
    real = _real(path)
    bad = next((x for x in _parts(real) if x.casefold() in SECRET_PARTS), None)
    if bad:
        return f"it is in {bad}. Credentials and shell settings are off limits"
    if os.path.basename(real).casefold() in NEVER_READ:
        return "it holds Agent Link's or a printer's keys. No tool reads it"
    home = _real("~")
    if home != "/" and _under(real, home):
        rel = os.path.relpath(real, home).replace(os.sep, "/").casefold()
        if (rel == "library" or rel.startswith("library/")) and (write or not any(rel == r or rel.startswith(r + "/") for r in LIBRARY_READ)):
            return "it is in ~/Library, where apps keep their settings"
    for h in dict.fromkeys(_real(x) for x in (os.environ.get("AGENTLINK_HOME"), "~/AgentLink") if x):
        if h == "/" or not _under(real, h):
            continue
        rel = [x.casefold() for x in _parts(os.path.relpath(real, h))]
        if rel and rel != ["."] and (rel[0] in STATE_DIRS or (len(rel) == 1 and os.path.splitext(rel[0])[1] in STATE_EXT)):
            return "it is Agent Link's own state"
    if write:
        hidden = next((x for x in _parts(os.path.relpath(real, home)) if x.startswith(".") and x != ".."), None) if _under(real, home) else None
        if hidden:
            return f"it is hidden ({hidden})"
    return None


def read_ok(session, tool, path, key, error):
    """The path a tool reads, checked: Agent Link's paths.in_file when it is
    lent, else the fallback here. Refuses with error(code, message)."""
    if not isinstance(path, str) or not path.strip():
        return path
    if IN_FILE is not None:
        return IN_FILE(session, tool, path, key)
    why = refusal(path)
    if why:
        raise error("invalid_input", f"{key}: {tilde(path)}: {why}.")
    return path


def write_ok(session, tool, path, key, error):
    """The path a tool writes, checked: Agent Link's paths.check as a write
    when it is lent, else the fallback here."""
    if not isinstance(path, str) or not path.strip():
        return path
    if CHECK is not None:
        CHECK(tool, key, path, getattr(session, "out_dir", None), True)
        return path
    why = refusal(path, write=True)
    if why:
        raise error("invalid_input", f"{key}: {tilde(path)}: {why}.")
    return path


def text(s, cap=TEXT_CAP):
    """Text from a file for the agent: Unicode format characters (category
    Cf), the tag block (U+E0000 to U+E007F) and control characters removed
    (a control character becomes a space), capped at cap characters."""
    if s is None:
        return None
    out = []
    for c in str(s):
        cat = unicodedata.category(c)
        if cat == "Cf" or 0xE0000 <= ord(c) <= 0xE007F:
            continue
        out.append(" " if cat == "Cc" else c)
    t = " ".join("".join(out).split())
    return t[:cap] + ("…" if len(t) > cap else "")


def tilde(obj):
    """Paths under the home folder as ~/... in every string of obj (dicts,
    lists and tuples walked; other values as they are)."""
    h = os.path.expanduser("~").rstrip("/")
    homes = [x for x in dict.fromkeys((h, os.path.realpath(h))) if x and x != "/"]
    if not homes:
        return obj
    pat = re.compile("(?:" + "|".join(re.escape(x) for x in homes) + r")(?![\w.-])")

    def walk(o):
        if isinstance(o, str):
            return pat.sub("~", o) if any(x in o for x in homes) else o
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        if isinstance(o, tuple):
            return tuple(walk(v) for v in o)
        return o

    return walk(obj)
