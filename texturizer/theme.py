# SPDX-License-Identifier: AGPL-3.0-only
# Texturizer's colours for its own widgets: text and surfaces from the
# widget's palette (so they follow whatever stylesheet is on), the accent from
# FreeCAD's theme preference (User parameter:BaseApp/Preferences/Themes,
# ThemeAccentColor1..3, set by FreeCAD's theme and the Studio packs), else the
# palette's highlight. Its own copy, not a shared import. Styles only widgets
# it is given (by object name), never the application.

PARAM = "User parameter:BaseApp/Preferences/Themes"
# Fixed hues readable on light and dark surfaces.
OK, WARN, BAD = "#3fae5a", "#e0a020", "#d94a4a"


def _hex(v):
    """FreeCAD stores a colour as unsigned 0xRRGGBBAA; 0 means unset."""
    if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
        return None
    return "#%06x" % ((v >> 8) & 0xFFFFFF)


def accents(params=None):
    """[accent1, accent2, accent3] as #rrggbb, None where unset. params: a
    ParameterGrp (default FreeCAD's) or a dict {name: unsigned int}."""
    if params is None:
        try:
            import FreeCAD

            params = FreeCAD.ParamGet(PARAM)
        except ImportError:
            return [None, None, None]
    out = []
    for i in (1, 2, 3):
        name = f"ThemeAccentColor{i}"
        v = params.get(name) if isinstance(params, dict) else params.GetUnsigned(name, 0)
        out.append(_hex(v))
    return out


def _palette(widget, role):
    from PySide import QtGui

    return widget.palette().color(getattr(QtGui.QPalette, role)).name()


def muted(widget):
    """Text two thirds of the way to the background: quiet in light and dark."""
    from PySide import QtGui

    pal = widget.palette()
    t, b = pal.color(QtGui.QPalette.WindowText), pal.color(QtGui.QPalette.Window)
    return "#%02x%02x%02x" % (int(t.red() * 0.6 + b.red() * 0.4), int(t.green() * 0.6 + b.green() * 0.4), int(t.blue() * 0.6 + b.blue() * 0.4))


def roles(widget=None, params=None):
    """{accent, accent2, accent3, muted, ok, warn, bad} for one widget."""
    a = accents(params)
    hi = _palette(widget, "Highlight") if widget is not None else "#3d7fd6"
    return {"accent": a[0] or hi, "accent2": a[1] or a[0] or hi, "accent3": a[2] or a[0] or hi,
            "muted": muted(widget) if widget is not None else "#808080", "ok": OK, "warn": WARN, "bad": BAD}


def sheet(name, r):
    """The stylesheet for the widget named name (and only it)."""
    return (f"#{name} QLabel[role=\"muted\"] {{ color: {r['muted']}; }}\n"
            f"#{name} QLabel[role=\"title\"] {{ color: {r['accent']}; font-weight: bold; }}\n"
            f"#{name} QPushButton:default {{ border: 1px solid {r['accent']}; }}\n"
            f"#{name} QFrame[role=\"rule\"] {{ color: {r['accent2']}; }}\n")


def apply(widget):
    """Style widget (by its object name) from the roles; call again on
    QEvent.PaletteChange so applying a theme restyles it."""
    widget.setStyleSheet(sheet(widget.objectName(), roles(widget)))
