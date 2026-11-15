# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""A small stand-in for PySide's QtCore / QtGui / QtWidgets, for running the
sidebar and gallery tests where no PySide is installed (this container).

It keeps the state the tests read (values, checks, texts, visibility,
layouts) and emits signals the way Qt does (only on a real change, not while
blocked). Only methods real Qt has are offered: an unknown name raises
AttributeError, so a typo in the widgets fails here too. The real-Qt run
(QT_QPA_PLATFORM=offscreen) is the one that counts when PySide is present.
"""
import types


class Signal:
    def __init__(self):
        self._slots = []

    def connect(self, fn):
        self._slots.append(fn)

    def emit(self, *a):
        for fn in list(self._slots):
            fn(*a)


# Real Qt methods the widgets call that change nothing the tests read.
NOOP = {
    "setContentsMargins", "setToolTip", "setObjectName", "setWordWrap", "setAlignment", "setAutoRaise",
    "setToolButtonStyle", "setIconSize", "setIcon", "setArrowType", "setAllowedAreas", "setWidgetResizable",
    "setTextFormat", "setOpenExternalLinks", "setDecimals", "setSingleStep", "setKeyboardTracking",
    "setValidator", "setClearButtonEnabled", "setSelectionMode", "setMaximumHeight",
    "setPopupMode", "setMenu", "setFlat", "setAccessibleName", "setProperty", "setFont", "setPixmap",
    "setExclusive", "raise_", "addStretch", "setParent", "setSpacing", "deleteLater", "splitDockWidget",
}


class _Base:
    SIGNALS = ()

    def __init__(self, *a, **k):
        for s in self.SIGNALS:
            object.__setattr__(self, s, Signal())
        self._blocked = False

    def __getattr__(self, name):
        if name in NOOP:
            return lambda *a, **k: None
        raise AttributeError(f"{type(self).__name__}.{name}")

    def blockSignals(self, on):  # noqa: N802
        old, self._blocked = self._blocked, bool(on)
        return old

    def _emit(self, sig, *a):
        if not self._blocked:
            getattr(self, sig).emit(*a)


class QFont(_Base):
    def __init__(self):
        super().__init__()
        self.bold = False

    def setBold(self, on):  # noqa: N802
        self.bold = bool(on)


class _Item:
    def __init__(self, w=None, layout=None):
        self._w, self._l = w, layout

    def widget(self):
        return self._w


class _Layout(_Base):
    def __init__(self, parent=None):
        super().__init__()
        self.items = []
        if isinstance(parent, QWidget):
            parent._layout = self

    def addWidget(self, w, *a, **k):  # noqa: N802
        self.items.append(_Item(w=w))

    def addLayout(self, lay, *a, **k):  # noqa: N802
        self.items.append(_Item(layout=lay))

    def count(self):
        return len(self.items)

    def takeAt(self, i):  # noqa: N802
        return self.items.pop(i)

    def widgets(self):
        out = []
        for it in self.items:
            if it._w is not None:
                out.append(it._w)
            elif it._l is not None:
                out.extend(it._l.widgets())
        return out


class QVBoxLayout(_Layout):
    pass


class QHBoxLayout(_Layout):
    pass


class QGridLayout(_Layout):
    pass


class QWidget(_Base):
    def __init__(self, parent=None, *a):
        super().__init__()
        self._visible, self._enabled, self._tip, self._layout, self._parent = True, True, "", None, parent
        self._font = QFont()

    def setVisible(self, on):  # noqa: N802
        self._visible = bool(on)

    def show(self):
        self._visible = True

    def hide(self):
        self._visible = False

    def isVisible(self):  # noqa: N802
        return self._visible

    def isHidden(self):  # noqa: N802
        return not self._visible

    def isVisibleTo(self, other):  # noqa: N802
        return self._visible

    def setEnabled(self, on):  # noqa: N802
        self._enabled = bool(on)

    def isEnabled(self):  # noqa: N802
        return self._enabled

    def setToolTip(self, t):  # noqa: N802
        self._tip = t

    def toolTip(self):  # noqa: N802
        return self._tip

    def font(self):
        return self._font

    def setFont(self, f):  # noqa: N802
        self._font = f

    def parentWidget(self):  # noqa: N802
        return self._parent

    def close(self):
        self._visible = False
        self.closeEvent(None)
        return True

    def closeEvent(self, ev):  # noqa: N802
        pass

    def children_widgets(self):
        return self._layout.widgets() if self._layout else []


class QLabel(QWidget):
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._text = text

    def setText(self, t):  # noqa: N802
        self._text = t

    def text(self):
        return self._text


class _Button(QWidget):
    SIGNALS = ("clicked", "toggled")

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._text, self._checkable, self._checked = text, False, False

    def setText(self, t):  # noqa: N802
        self._text = t

    def text(self):
        return self._text

    def setCheckable(self, on):  # noqa: N802
        self._checkable = bool(on)

    def isChecked(self):  # noqa: N802
        return self._checked

    def setChecked(self, on):  # noqa: N802
        on = bool(on)
        if self._checkable and on != self._checked:
            self._checked = on
            self._emit("toggled", on)

    def click(self):
        if not self._enabled:
            return
        if self._checkable:
            self.setChecked(not self._checked)
        self._emit("clicked", self._checked)


class QPushButton(_Button):
    pass


class QToolButton(_Button):
    InstantPopup = 2


class QCheckBox(_Button):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._checkable = True


class QSlider(QWidget):
    SIGNALS = ("valueChanged", "sliderReleased")

    def __init__(self, orientation=None, parent=None):
        super().__init__(parent)
        self._min, self._max, self._v = 0, 99, 0

    def setRange(self, a, b):  # noqa: N802
        self._min, self._max = int(a), int(b)
        self.setValue(self._v)

    def minimum(self):
        return self._min

    def maximum(self):
        return self._max

    def setValue(self, v):  # noqa: N802
        v = max(self._min, min(self._max, int(v)))
        if v != self._v:
            self._v = v
            self._emit("valueChanged", v)

    def value(self):
        return self._v


class QDoubleSpinBox(QWidget):
    SIGNALS = ("valueChanged",)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._min, self._max, self._v = 0.0, 99.99, 0.0

    def setRange(self, a, b):  # noqa: N802
        self._min, self._max = float(a), float(b)

    def minimum(self):
        return self._min

    def maximum(self):
        return self._max

    def setValue(self, v):  # noqa: N802
        v = max(self._min, min(self._max, float(v)))
        if v != self._v:
            self._v = v
            self._emit("valueChanged", v)

    def value(self):
        return self._v


class QLineEdit(QWidget):
    SIGNALS = ("textChanged", "editingFinished")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""

    def setText(self, t):  # noqa: N802
        if t != self._text:
            self._text = t
            self._emit("textChanged", t)

    def text(self):
        return self._text

    def setPlaceholderText(self, t):  # noqa: N802
        self._ph = t

    def placeholderText(self):  # noqa: N802
        return self._ph


class QComboBox(QWidget):
    SIGNALS = ("currentIndexChanged",)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items, self._ix = [], -1

    def addItem(self, t):  # noqa: N802
        self._items.append(t)
        if self._ix < 0:
            self._ix = 0
            self._emit("currentIndexChanged", 0)

    def count(self):
        return len(self._items)

    def itemText(self, i):  # noqa: N802
        return self._items[i]

    def currentIndex(self):  # noqa: N802
        return self._ix

    def currentText(self):  # noqa: N802
        return self._items[self._ix]

    def setCurrentIndex(self, i):  # noqa: N802
        if i != self._ix:
            self._ix = i
            self._emit("currentIndexChanged", i)


class _ListItem:
    def __init__(self, t):
        self._t = t

    def text(self):
        return self._t


class QListWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []

    def clear(self):
        self._items = []

    def addItem(self, t):  # noqa: N802
        self._items.append(_ListItem(t))

    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def selectedItems(self):  # noqa: N802
        return []


class _Action(_Base):
    SIGNALS = ("triggered",)


class QMenu(QWidget):
    def addAction(self, t):  # noqa: N802
        return _Action()


class QProgressBar(QWidget):
    def setRange(self, a, b):  # noqa: N802
        self._range = (a, b)

    def setValue(self, v):  # noqa: N802
        self._v = v


class QScrollArea(QWidget):
    _w = None

    def setWidget(self, w):  # noqa: N802
        self._w = w

    def takeWidget(self):  # noqa: N802
        w, self._w = self._w, None
        return w

    def widget(self):
        return self._w


class QDockWidget(QWidget):
    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self._title = title

    def windowTitle(self):  # noqa: N802
        return self._title

    def setWidget(self, w):  # noqa: N802
        self._w = w

    def widget(self):
        return self._w


class QMainWindow(QWidget):
    def addDockWidget(self, area, dock):  # noqa: N802
        self.docks = getattr(self, "docks", []) + [(area, dock)]


class QButtonGroup(_Base):
    def __init__(self, parent=None):
        super().__init__()

    def addButton(self, b):  # noqa: N802
        pass


class QAbstractItemView:
    ExtendedSelection = 3


class QMessageBox:
    Yes, No = 0x4000, 0x10000

    @staticmethod
    def question(*a, **k):
        return QMessageBox.Yes


class QApplication:
    _inst = None

    def __init__(self, argv=None):
        QApplication._inst = self

    @staticmethod
    def instance():
        return QApplication._inst


class QPixmap(_Base):
    def __init__(self, path=None):
        super().__init__()
        self.path = path

    def isNull(self):  # noqa: N802
        return True

    def scaled(self, *a):
        return self


class QIcon(_Base):
    def __init__(self, pm=None):
        super().__init__()

    def pixmap(self, *a):
        return QPixmap()


class QDoubleValidator(_Base):
    pass


class QSize:
    def __init__(self, w, h):
        self.w, self.h = w, h


class _Qt:
    Horizontal, Vertical = 1, 2
    LeftDockWidgetArea, RightDockWidgetArea = 1, 2
    ToolButtonTextBesideIcon, ToolButtonTextUnderIcon = 2, 3
    DownArrow, RightArrow = 2, 4
    AlignRight = 2
    RichText = 1
    KeepAspectRatio, SmoothTransformation = 1, 1


def modules():
    """(QtCore, QtGui, QtWidgets) as module objects."""
    core = types.ModuleType("QtCore")
    core.Qt, core.QSize, core.Signal = _Qt, QSize, Signal
    gui = types.ModuleType("QtGui")
    for c in (QPixmap, QIcon, QDoubleValidator, QFont):
        setattr(gui, c.__name__, c)
    w = types.ModuleType("QtWidgets")
    for c in (QWidget, QLabel, QPushButton, QToolButton, QCheckBox, QSlider, QDoubleSpinBox, QLineEdit, QComboBox,
              QListWidget, QMenu, QProgressBar, QScrollArea, QDockWidget, QMainWindow, QButtonGroup,
              QAbstractItemView, QMessageBox, QApplication, QVBoxLayout, QHBoxLayout, QGridLayout):
        setattr(w, c.__name__, c)
    return core, gui, w
