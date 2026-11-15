# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""The timing probe: where a live drag, stroke or Undo spends its time.

Off by default. With the FreeCAD parameter Mod/Texturizer Bool TimingProbe
true, every stage wrapped here prints one line to the Report view:

    Texturizer timing: <stage> <ms>

(stage(name) as a context manager, timed(name) as a decorator, report(name,
ms) for a duration measured elsewhere, such as the worker's preview stages).
told(fn, name) wraps a Coin event callback: its traceback reaches the Report
view whole (pivy prints only the exception's class) and, with the probe on,
its time. No FreeCAD import until a line is printed; the parameter is read at
most once a second, so turning it on in the Parameter editor takes effect
during a session."""
import functools
import time

PARAMS = "User parameter:BaseApp/Preferences/Mod/Texturizer"
KEY = "TimingProbe"
RECHECK_S = 1.0

# Tests set FORCE (True / False) and SINK (a callable taking the line).
FORCE = None
SINK = None
_cache = [None, -1e9]   # value, when read


def enabled():
    if FORCE is not None:
        return bool(FORCE)
    now = time.monotonic()
    if now - _cache[1] >= RECHECK_S:
        _cache[1] = now
        try:
            import FreeCAD

            _cache[0] = bool(FreeCAD.ParamGet(PARAMS).GetBool(KEY, False))
        except Exception:  # noqa: BLE001 - no FreeCAD: off
            _cache[0] = False
    return bool(_cache[0])


def _print(line):
    if SINK is not None:
        SINK(line)
        return
    try:
        import FreeCAD

        FreeCAD.Console.PrintMessage(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def report(name, ms):
    if enabled():
        _print(f"Texturizer timing: {name} {ms:.1f}")


class stage:  # noqa: N801 - read as a phrase: with timing.stage("...")
    """with stage("draft frame"): ... times the block when the probe is on."""

    __slots__ = ("name", "t0")

    def __init__(self, name):
        self.name = name
        self.t0 = None

    def __enter__(self):
        if enabled():
            self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        if self.t0 is not None:
            report(self.name, (time.perf_counter() - self.t0) * 1000.0)
        return False


def timed(name):
    def wrap(fn):
        @functools.wraps(fn)
        def call(*a, **k):
            with stage(name):
                return fn(*a, **k)

        return call

    return wrap


def told(fn, name=None):
    """A Coin event callback whose errors reach the Report view whole, and,
    with the probe on, its time under name."""

    def call(*a):
        try:
            if name is None:
                return fn(*a)
            with stage(name):
                return fn(*a)
        except Exception:  # noqa: BLE001 - one bad event never stops the view
            import traceback

            text = "Texturizer: " + traceback.format_exc()
            if SINK is not None:
                SINK(text)
                return None
            try:
                import FreeCAD

                FreeCAD.Console.PrintError(text)
            except Exception:  # noqa: BLE001
                pass
        return None

    return call
