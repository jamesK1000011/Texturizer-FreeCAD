# SPDX-License-Identifier: AGPL-3.0-only
# theme.apply must not recurse: setStyleSheet sends the widget a
# PaletteChange, and the panel's changeEvent calls apply again. Found by the
# GUI pass (FreeCAD died of a stack overflow on a theme switch).
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from texturizer import theme  # noqa: E402


class _Widget:
    """setStyleSheet answers with a PaletteChange the way Qt does."""

    def __init__(self):
        self.sheet, self.calls = "", 0

    def objectName(self):
        return "TexturizerSidebar"

    def styleSheet(self):
        return self.sheet

    def setStyleSheet(self, css):
        self.calls += 1
        self.sheet = css
        theme.apply(self)  # the panel's changeEvent, in effect


class ThemeLoop(unittest.TestCase):
    def test_apply_sets_once_and_returns(self):
        w = _Widget()
        roles = {"accent": "#111111", "accent2": "#222222", "accent3": "#333333", "muted": "#808080", "ok": "", "warn": "", "bad": ""}
        with mock.patch.object(theme, "roles", return_value=roles):
            theme.apply(w)
            self.assertEqual(w.calls, 1)
            theme.apply(w)  # same sheet: not set again
            self.assertEqual(w.calls, 1)

    def test_a_changing_palette_still_ends(self):
        w = _Widget()
        n = iter(range(10 ** 6))
        with mock.patch.object(theme, "roles", side_effect=lambda _w: {"accent": "#%06x" % next(n), "accent2": "#000000", "accent3": "#000000", "muted": "#000000", "ok": "", "warn": "", "bad": ""}):
            theme.apply(w)
        self.assertEqual(w.calls, 1)


if __name__ == "__main__":
    unittest.main()
