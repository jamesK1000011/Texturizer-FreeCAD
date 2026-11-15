# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""Qt for the sidebar and gallery: FreeCAD's PySide shim when present, else
PySide6, else PySide2. QtCore, QtGui, QtWidgets are None with no Qt at all,
so the plain modules (controls, draft) and the tests still import."""
QtCore = QtGui = QtWidgets = None
BINDING = None

try:
    from PySide import QtCore, QtGui  # FreeCAD's shim

    QtWidgets = QtGui
    try:
        from PySide import QtWidgets  # noqa: F811  (Qt 6 builds)
    except ImportError:
        pass
    BINDING = "PySide"
except ImportError:
    for _name in ("PySide6", "PySide2"):
        try:
            _m = __import__(_name, fromlist=["QtCore", "QtGui", "QtWidgets"])
            QtCore, QtGui, QtWidgets = _m.QtCore, _m.QtGui, _m.QtWidgets
            BINDING = _name
            break
        except ImportError:
            continue


def available():
    return QtWidgets is not None


def app():
    """The running QApplication, or a new one (tests, offscreen)."""
    a = QtWidgets.QApplication.instance()
    return a or QtWidgets.QApplication([])
