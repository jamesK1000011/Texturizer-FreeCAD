# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The Texture Gallery's Turntable (turntable.py), as BumpMesh's viewer.js
and textureGallery.js: one revolution every 24 s about Z through the model's
centre, the step capped at 0.1 s, the person's press / wheel / puck ends it
and pops the button out without changing the choice, the gallery spins on
opening unless switched off, which sticks. With a fake view and timer, and
the gallery on the stand-in Qt (tests/_fakeqt.py) whatever Qt is importable."""
import importlib
import math
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from texturizer import draft, qt, turntable  # noqa: E402


def rot(q, v):
    """v rotated by the unit quaternion q (x, y, z, w)."""
    p = turntable.quat_mul(turntable.quat_mul(q, (v[0], v[1], v[2], 0.0)), (-q[0], -q[1], -q[2], q[3]))
    return p[:3]


class FakeView:
    def __init__(self, pose=((10.0, 0.0, 5.0), (0.0, 0.0, 0.0, 1.0)), pivot=(2.0, 0.0, 0.0)):
        self.p, self.o = pose
        self.piv = pivot
        self.listening = []

    def pose(self):
        return self.p, self.o

    def set_pose(self, p, o):
        self.p, self.o = p, o

    def pivot(self):
        return self.piv

    def listen(self, fn):
        self.listening.append(fn)
        return lambda: self.listening.remove(fn)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class Timer:
    def __init__(self):
        self.fn = None

    def __call__(self, ms, fn):
        self.ms, self.fn = ms, fn

        def stop():
            self.fn = None
        return stop


def rig(view=None):
    v = view if view is not None else FakeView()
    clock, timer = Clock(), Timer()
    return turntable.Turntable(lambda: v, timer, clock), v, clock, timer


class Step(unittest.TestCase):
    def test_yaw_about_z_through_the_pivot(self):
        o = (math.sin(0.3), 0.0, 0.0, math.cos(0.3))   # a tilted camera
        p, q = turntable.step((10.0, 0.0, 5.0), o, (2.0, 0.0, 0.0), math.pi / 2)
        for a, b in zip(p, (2.0, 8.0, 5.0)):
            self.assertAlmostEqual(a, b)
        # The view direction turns with it: world Z rotation after the old orientation.
        d0, d1 = rot(o, (0, 0, -1)), rot(q, (0, 0, -1))
        for a, b in zip(d1, (-d0[1], d0[0], d0[2])):
            self.assertAlmostEqual(a, b)
        self.assertAlmostEqual(sum(c * c for c in q), 1.0)

    def test_one_revolution_every_24_s(self):
        self.assertAlmostEqual(turntable.RAD_PER_S * 24, 2 * math.pi)


class Spin(unittest.TestCase):
    def test_ticks_turn_the_camera_capped_at_a_tenth_of_a_second(self):
        t, v, clock, timer = rig()
        self.assertTrue(t.start())
        self.assertEqual(timer.ms, turntable.INTERVAL_MS)
        clock.t += 1.0   # a second in the background: one 0.1 s step, no jump
        timer.fn()
        ang = math.atan2(v.p[1], v.p[0] - 2.0)
        self.assertAlmostEqual(ang, 0.1 * turntable.RAD_PER_S)
        clock.t += 0.05
        timer.fn()
        self.assertAlmostEqual(math.atan2(v.p[1], v.p[0] - 2.0), 0.15 * turntable.RAD_PER_S)
        self.assertAlmostEqual(v.p[2], 5.0)

    def test_the_persons_input_ends_it_once(self):
        t, v, _clock, timer = rig()
        stopped = []
        t.start(on_stop=lambda: stopped.append(1))
        self.assertEqual(len(v.listening), 1)
        v.listening[0]()   # a press, wheel or puck in the view
        self.assertEqual(stopped, [1])
        self.assertFalse(t.spinning)
        self.assertIsNone(timer.fn)
        self.assertEqual(v.listening, [])
        t.start(on_stop=lambda: stopped.append(2))
        t.stop()   # setTurntable(false): no onStop
        self.assertEqual(stopped, [1])

    def test_no_view_no_spin(self):
        t = turntable.Turntable(lambda: None, Timer(), Clock())
        self.assertFalse(t.start())
        t2, v, _c, timer = rig(FakeView())
        v.pose = lambda: None
        self.assertFalse(t2.start())
        self.assertIsNone(timer.fn)

    def test_a_view_that_fails_stops_quietly(self):
        t, v, clock, timer = rig()
        t.start()

        def gone(*_a):
            raise RuntimeError("closed")
        v.set_pose = gone
        clock.t += 0.02
        timer.fn()
        self.assertFalse(t.spinning)

    def test_prefs(self):
        class Params:
            def __init__(self):
                self.v = {}

            def GetBool(self, k, d):  # noqa: N802
                return self.v.get(k, d)

            def SetBool(self, k, v):  # noqa: N802
                self.v[k] = v

        p = turntable.Prefs(Params())
        self.assertTrue(p.wanted())   # spins by default
        p.set(False)
        self.assertFalse(p.wanted())
        self.assertEqual(p.params.v, {"Turntable": False})


class Gallery(unittest.TestCase):
    """The gallery's Turntable button on the stand-in Qt."""

    @classmethod
    def setUpClass(cls):
        import _fakeqt

        from texturizer import gallery

        cls.saved = (qt.QtCore, qt.QtGui, qt.QtWidgets)
        qt.QtCore, qt.QtGui, qt.QtWidgets = _fakeqt.modules()
        cls.g = importlib.reload(gallery)
        qt.app()

    @classmethod
    def tearDownClass(cls):
        qt.QtCore, qt.QtGui, qt.QtWidgets = cls.saved
        importlib.reload(cls.g)

    def make(self):
        spins = []
        prefs = turntable.Prefs()
        prefs.params = None
        lib = [{"id": "hexagons", "name": "Hexagons", "category": "geometric", "thumb": None, "image": None,
                "credit": None, "licence": "AGPL-3.0", "source_url": None, "defaults": {}}]
        fav = self.g.Favourites(path=os.path.join(tempfile.mkdtemp(), "fav.json"), entries=lib)
        dock = self.g.GalleryDock(lambda: lib, fav, draft=draft.Draft(), spin=lambda on: spins.append(on) or True,
                                  prefs=prefs)
        return dock, spins, prefs

    def test_spins_on_opening_unless_switched_off(self):
        dock, spins, prefs = self.make()
        dock.opened()
        self.assertEqual(spins, [True])
        self.assertTrue(dock.turntable.isChecked())
        dock.turntable.click()   # the person switches it off: it sticks
        self.assertEqual(spins, [True, False])
        self.assertFalse(prefs.wanted())
        dock.opened()
        self.assertEqual(spins, [True, False])
        dock.turntable.click()
        self.assertTrue(prefs.wanted())

    def test_taking_the_view_pops_the_button_out_and_keeps_the_choice(self):
        dock, spins, prefs = self.make()
        dock.opened()
        dock._spin_taken()
        self.assertFalse(dock.turntable.isChecked())
        self.assertEqual(spins, [True])   # the turntable already stopped itself
        self.assertTrue(prefs.wanted())
        dock.close()
        self.assertEqual(spins, [True])


class Controls(unittest.TestCase):
    """The View toolbar's Turntable and the agent's texture_preview { turntable }
    drive the gallery's one spin; a number of degrees turns the view once
    (a render series)."""

    def setUp(self):
        self.t, self.v, self.clock, self.timer = rig()
        self.saved = (turntable._SHARED, turntable._adapter)
        turntable._SHARED = self.t
        turntable._adapter = lambda: self.v

    def tearDown(self):
        turntable._SHARED, turntable._adapter = self.saved

    def test_turn_once_about_z_through_the_pivot(self):
        self.assertTrue(turntable.turn(90))
        for a, b in zip(self.v.p, (2.0, 8.0, 5.0)):
            self.assertAlmostEqual(a, b)
        self.assertFalse(turntable.turn(10, adapter=lambda: None))

    def test_toolbar_command_toggles_the_shared_spin(self):
        from texturizer import commands

        self.assertIs(turntable.shared(), self.t)
        self.assertTrue(commands.toggle_turntable())
        self.assertTrue(self.t.spinning)
        self.assertFalse(commands.toggle_turntable())
        self.assertFalse(self.t.spinning)

    def test_texture_preview_turntable(self):
        from texturizer import handlers

        out = handlers._turntable(True)
        self.assertTrue(out["turntable"]["spinning"] and self.t.spinning)
        out = handlers._turntable(30)
        self.assertEqual(out["turntable"], {"spinning": False, "turned": 30.0})
        self.assertFalse(self.t.spinning)          # a turn stops the spin: the frame stays put for the render
        self.assertAlmostEqual(math.degrees(math.atan2(self.v.p[1], self.v.p[0] - 2.0)), 30.0)
        self.assertEqual(handlers._turntable(False), {"turntable": {"spinning": False}})
        for bad in ("fast", float("nan"), None):
            with self.assertRaises(handlers.ToolError):
                handlers._turntable(bad)
        turntable._adapter = lambda: None
        with self.assertRaises(handlers.ToolError):
            handlers._turntable(15)


if __name__ == "__main__":
    unittest.main()
