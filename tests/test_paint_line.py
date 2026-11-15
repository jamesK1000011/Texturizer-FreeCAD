# SPDX-License-Identifier: AGPL-3.0-only
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The pointer's ray from a view volume, however pivy wraps projectPointToLine
(one argument in pivy; the C++ form fills a line passed in, which pivy refuses)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from texturizer.paintview import _line  # noqa: E402


class _V:
    def __init__(self, *v):
        self.v = v

    def getValue(self):  # noqa: N802
        return self.v


class _Line:
    def __init__(self, p=(0, 0, 0), d=(0, 0, 1)):
        self.p, self.d = p, d

    def getPosition(self):  # noqa: N802
        return _V(*self.p)

    def getDirection(self):  # noqa: N802
        return _V(*self.d)


class _Coin:
    SbLine = _Line


class PivyPairs:
    def projectPointToLine(self, pt):  # noqa: N802 - pivy: one argument, (near, far)
        return (_V(1, 2, 10), _V(1, 2, 0))


class PivyLine:
    def projectPointToLine(self, pt):  # noqa: N802
        return _Line((1, 2, 3), (0, 1, 0))


class FillsLine:
    def projectPointToLine(self, pt, line):  # noqa: N802 - the C++ form
        line.p, line.d = (4, 5, 6), (1, 0, 0)


class Ray(unittest.TestCase):
    def test_every_wrapping(self):
        self.assertEqual(_line(_Coin, PivyPairs(), None), ((1, 2, 10), (0.0, 0.0, -1.0)))
        self.assertEqual(_line(_Coin, PivyLine(), None), ((1, 2, 3), (0, 1, 0)))
        self.assertEqual(_line(_Coin, FillsLine(), None), ((4, 5, 6), (1, 0, 0)))


if __name__ == "__main__":
    unittest.main()
