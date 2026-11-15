# SPDX-License-Identifier: AGPL-3.0-only
# The Texture panel: the last prepared files and the last check, with buttons
# for Prepare and Check. It is written against a runner,
# runner(tool, args) -> (ok, result): under Agent Link the tab is hosted in
# Agent Link's panel and runs tools through it (shown and logged as the
# agent's are); alone it is Texturizer's own dock with the local runner
# (handlers.local_runner). No Agent Link import here.
import os

from . import CREDIT, handlers, theme

try:
    from PySide import QtCore, QtGui

    QtWidgets = QtGui
    try:
        from PySide import QtWidgets  # noqa: F811  (Qt 6 builds)
    except ImportError:
        pass
except ImportError:  # pragma: no cover — no GUI here
    QtCore = QtGui = QtWidgets = None

TITLE = "Texture"
NAME = "TexturizerTexture"
DOCK = "TexturizerDock"

# Set by the hosted tab (agentlink_families): Agent Link's panel._call.
HOST_CALL = None
_LOCAL = None
_WIDGETS = []


def hosted():
    import texturizer

    return bool(getattr(texturizer, "_hosted", False))


def runner():
    """The runner for this window: Agent Link's when the tab is hosted and
    built, else the local one."""
    global _LOCAL
    if HOST_CALL is not None:
        return HOST_CALL
    if _LOCAL is None:
        _LOCAL = handlers.local_runner()
    return _LOCAL


def run(tool, args):
    """One tool through the runner; every open Texture panel refreshed."""
    ok, out = runner()(tool, args)
    refresh()
    return ok, out


def refresh():
    for w in list(_WIDGETS):
        try:
            w.fill()
        except RuntimeError:  # deleted by Qt
            _WIDGETS.remove(w)


def summary(check):
    """A check answer as short lines for the panel (pure: tested)."""
    if not check:
        return ["No check yet: texture the part (texture_apply), export an STL, then Check textured mesh."]
    lines = [f"{check.get('verdict', '?').upper()} — {os.path.basename(check.get('file') or '')} on {check.get('body', '')}, {check.get('depth', 0)} mm deep at most"]
    for face, row in sorted((check.get("texturedFaces") or {}).items()):
        lines.append(f"  {face}: {int(round(row.get('moved', 0) * 100))}% moved" + (f", p95 {row['p95']} mm" if "p95" in row else ""))
    if check.get("leaked"):
        lines.append(f"  Leaked onto {', '.join(check['leaked']['faces'])} (worst {check['leaked']['worst']} mm)")
    if check.get("bare"):
        lines.append(f"  Bare: {', '.join(check['bare'])}")
    if check.get("mesh"):
        lines.append(f"  Not closed: {check['mesh']['openEdges']} open edges, {check['mesh']['overSharedEdges']} over-shared")
    if check.get("far"):
        lines.append(f"  {check['far']['points']} points farther than reach {check['far']['reach']} mm")
    return lines


if QtWidgets is not None:

    class TexturePanel(QtWidgets.QWidget):
        def __init__(self, call=None, parent=None):
            super().__init__(parent)
            self.setObjectName(NAME)
            self._call = call
            lay = QtWidgets.QVBoxLayout(self)
            head = QtWidgets.QLabel("Prepared for texture")
            head.setProperty("role", "title")
            lay.addWidget(head)
            self.prepared = QtWidgets.QTreeWidget()
            self.prepared.setHeaderLabels(["Body", "Faces", "STL"])
            self.prepared.setRootIsDecorated(False)
            self.prepared.itemDoubleClicked.connect(self._reveal)
            lay.addWidget(self.prepared)
            head2 = QtWidgets.QLabel("Last check")
            head2.setProperty("role", "title")
            lay.addWidget(head2)
            self.check = QtWidgets.QLabel()
            self.check.setWordWrap(True)
            self.check.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            lay.addWidget(self.check)
            row = QtWidgets.QHBoxLayout()
            for text, tip, slot in (("Prepare…", "Prepare the selected faces for a texture. " + CREDIT, self._prepare),
                                    ("Check…", "Check a textured STL against its body. " + CREDIT, self._check)):
                b = QtWidgets.QPushButton(text)
                b.setToolTip(tip)
                b.clicked.connect(slot)
                row.addWidget(b)
            lay.addLayout(row)
            lay.addStretch(1)
            foot = QtWidgets.QLabel(CREDIT)
            foot.setWordWrap(True)
            foot.setProperty("role", "muted")
            lay.addWidget(foot)
            _WIDGETS.append(self)
            theme.apply(self)
            self.fill()

        def changeEvent(self, ev):
            if ev.type() == QtCore.QEvent.PaletteChange:
                theme.apply(self)
            super().changeEvent(ev)

        def fill(self):
            self.prepared.clear()
            for p in handlers.LAST["prepared"]:
                it = QtWidgets.QTreeWidgetItem([p.get("body", ""), f"{p.get('mode')}: {', '.join(p.get('faces') or [])}", os.path.basename(p.get("stl", ""))])
                it.setToolTip(2, f"{p.get('stl')}\nmask: {p.get('mask')}")
                it.setData(0, QtCore.Qt.UserRole, p.get("stl"))
                self.prepared.addTopLevelItem(it)
            c = handlers.LAST["check"]
            colour = {"ok": theme.OK, "check": theme.WARN}.get((c or {}).get("verdict"), "")
            text = "<br>".join(_esc(x).replace("  ", "&nbsp;&nbsp;") for x in summary(c))
            self.check.setText(f"<span style='color:{colour}'>{text}</span>" if colour else text)

        def _reveal(self, item, _col):
            from . import commands

            commands.reveal(item.data(0, QtCore.Qt.UserRole))

        def _prepare(self):
            from . import commands

            commands.show_prepare()

        def _check(self):
            from . import commands

            commands.show_check()


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def hosted_factory(panel):
    """For Agent Link's register_tab: the tab, its tools run by the panel."""
    global HOST_CALL
    HOST_CALL = panel._call
    return TexturePanel(panel._call)


def show_dock():
    """Alone: the panel as Texturizer's own dock, tabbed with the Combo View.
    Hosted: raise the Texture tab in Agent Link's panel."""
    import FreeCADGui

    mw = FreeCADGui.getMainWindow()
    if hosted():
        w = mw.findChild(QtWidgets.QWidget, NAME)
        p = w.parentWidget() if w is not None else None
        while p is not None and not isinstance(p, QtWidgets.QTabWidget):
            p = p.parentWidget()
        if p is not None:
            for i in range(p.count()):
                if p.widget(i).isAncestorOf(w) or p.widget(i) is w:
                    p.setCurrentIndex(i)
            dock = p.parentWidget()
            while dock is not None and not isinstance(dock, QtWidgets.QDockWidget):
                dock = dock.parentWidget()
            if dock is not None:
                dock.show()
                dock.raise_()
            return
    dock = mw.findChild(QtWidgets.QDockWidget, DOCK)
    if dock is None:
        dock = QtWidgets.QDockWidget(TITLE, mw)
        dock.setObjectName(DOCK)
        dock.setWidget(TexturePanel(runner()))
        mw.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)
        combo = mw.findChild(QtWidgets.QDockWidget, "Combo View")
        if combo is not None:
            mw.tabifyDockWidget(combo, dock)
    dock.show()
    dock.raise_()
