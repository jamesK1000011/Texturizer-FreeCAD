# SPDX-License-Identifier: AGPL-3.0-only
"""Texturizer for FreeCAD: surface textures for printed parts, based on
BumpMesh by Stefan Hermann (CNC Kitchen), github.com/CNCKitchen/stlTexturizer.

- engine: BumpMesh's texturing engine ported to Python and numpy (no FreeCAD,
  no browser): the library, estimate, texture, export, staged preview.
- mesh: a body's faces as one closed mesh, face by face; STL read and
  written; the mask of faces to texture; a textured mesh checked against the
  body it came from, face by face.

Plain Python on FreeCAD and numpy; it needs no other add-on and imports no
agent. Tools for agents come
through Agent Link for FreeCAD when it is installed (agentlink_families).
"""

import re

VERSION = "0.3.0"
CREDIT = "Based on BumpMesh by Stefan Hermann (CNC Kitchen), github.com/CNCKitchen/stlTexturizer (AGPL-3.0)"
LICENSE = "AGPL-3.0-only"


# Words FreeCAD's design guide (Title Case) keeps lower inside a title.
_SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into", "nor", "of", "on", "or", "per",
          "the", "to", "vs", "via", "with"}


def title_case(text):
    """text in Title Case, as FreeCAD's design guide asks of command names,
    buttons, menu items and group titles: every word capitalised (after a
    hyphen too) but the small words inside; words already capitalised
    (STL, 3MF, BumpMesh) stay as they are. "Reset to defaults" -> "Reset to
    Defaults", "Suggest values (change to taste)" -> "Suggest Values (Change
    to Taste)"."""
    if not text:
        return text
    words = re.split(r"(\s+)", text)
    idx = [i for i, w in enumerate(words) if w.strip()]

    def cap(part):
        m = re.match(r"^([^\w]*)(\w)(.*)$", part, re.S)
        return f"{m.group(1)}{m.group(2).upper()}{m.group(3)}" if m else part

    for n, i in enumerate(idx):
        w = words[i]
        core = w.strip("()[]\"'…:,.").lower()
        inner = 0 < n < len(idx) - 1 and not w.startswith("(") and not words[idx[n - 1]].endswith(":")
        if core in _SMALL and inner:
            words[i] = w.lower()
            continue
        words[i] = "-".join(cap(p) for p in w.split("-"))
    return "".join(words)


BASED_ON = "Based on BumpMesh by Stefan Hermann (CNC Kitchen), github.com/CNCKitchen/stlTexturizer"
URL = "https://github.com/CNCKitchen/stlTexturizer"
_SUMMARY = ("Surface textures for printed parts: BumpMesh's engine, settings sidebar and Texture Gallery running in "
            "FreeCAD, faces prepared for a texture, and the textured mesh checked against the body.")
_ICONS = ("Icons adapted from Tabler Icons (MIT) and Pictogrammers Material Design Icons (Apache-2.0); "
          "FreeCAD's own icons are used by name.")


def about():
    """The About text: name, version, licence, credit (plain)."""
    return (f"Texturizer for FreeCAD\nVersion {VERSION}\nLicence: {LICENSE}\n\n{BASED_ON}\n\n{_SUMMARY}\n\n{_ICONS}")


def about_html():
    """The About box's text: about() with the credit's link."""
    link = f'<a href="{URL}">github.com/CNCKitchen/stlTexturizer</a>'
    return (f"<p><b>Texturizer for FreeCAD</b><br>Version {VERSION}<br>Licence: {LICENSE}</p>"
            f"<p>Based on BumpMesh by Stefan Hermann (CNC Kitchen), {link}</p><p>{_SUMMARY}</p><p>{_ICONS}</p>")
