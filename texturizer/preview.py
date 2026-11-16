# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# Part of Texturizer for FreeCAD, based on BumpMesh by Stefan Hermann
# (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer. The staged
# preview follows runPreviewPipeline / choosePreviewEdge in
# js/previewPipeline.js.
"""The live-preview controller (design 3.2 to 3.6).

PreviewController(engine, draft, view, quicklook_ok=...) follows the draft:
each change bumps a generation counter, drops the stage cache from the stage
the change touches (STAGE_OF), and after a 150 ms debounce (commit(): at
once) runs engine.preview on one worker thread. Results of an older
generation are dropped. The Quick look (when the shader runs) is updated on
every change and shown while a CPU job is pending; the CPU node replaces it
when the latest result arrives (3.4 "When it shows").

The engine is injected:

    engine.preview(stage_inputs, progress, should_abort) -> result or None
        stage_inputs  {"draft": draft.to_dict(), "rerun_from": stage,
                       "cache": {stage: result of that stage} for the stages
                       before rerun_from, "tri_budget": 500 k / 1.5 M,
                       "memory_limit": bytes}
        progress(stage, fraction)   from the worker
        should_abort()              True once the job is stale
        result        {"stages": {stage: reusable result}, "mesh": what
                       view.show_cpu draws, "note": optional text (a coarser
                       edge, say)}; None when aborted

So is everything Qt or Coin, through the view object (all methods optional,
called on the GUI thread):

    show_cpu(mesh), set_cpu_visible(bool), set_cpu_stale(bool) (grey tint)
    update_quicklook(stage), set_quicklook_visible(bool)
    get_body_visible() -> bool, set_body_visible(bool)
    progress(stage, fraction), note(text), lock_preview(on, text)
    error(text)

Progressive first look (roadmap v19 item 5): a run from subdivide on a part
whose subdivision is not kept (engine.kept(stage_inputs) false: a new part,
a new refine length) first runs a coarse preview (inputs["coarse"]: longer
edges, about 20 k triangles, no sliver clean-up) and shows it, then the full
one in the same job, which replaces it. A change drops both (the generation);
one that reruns from a later stage lets the cold subdivide finish and keep
its mesh (inputs["subdivide_abort"] follows only subdivide changes), so the
rerun after it is warm. An engine without kept() gets no coarse pass.

A mask change with Preserve Untextured on (a brush stroke, a face picked)
needs a new subdivision too (the untextured faces stay coarse), about a
second of work; its first look is not coarse: the engine runs it on the
part's last full subdivision (result["provisional"]), which shows the
textured result as it will be within a few hundred ms, and the exact one
replaces it from the same job.

and the threads and timers through run_async(fn) (the worker),
post(fn) (back to the GUI thread: a Qt signal) and schedule(seconds, fn) ->
cancel(). The defaults use threading, for headless use and tests.
"""
import threading
import time

from . import timing

STAGES = ("subdivide", "map", "displace", "colours")
TRI_BUDGET = {"draft": 500_000, "fine": 1_500_000}
MEMORY_LIMIT = 1 << 30
DEBOUNCE_S = 0.150
DRAG_DEBOUNCE_S = 0.120   # held (a ring, stamp or brush drag) with no Quick look to carry it
NO_QUICKLOOK = "Quick look not available on this graphics setup"
COARSE_NOTE = "First look (coarse): refining…"
# The first look on the part's last subdivision (a mask change with Preserve
# Untextured on): the textured result as it will be, the untextured faces
# denser until the exact subdivision replaces it.
PROVISIONAL_NOTE = "Preview: finishing the untextured faces…"

# Design 3.2: the stage each setting reruns from. Keys not listed (and not
# ui ones) rerun from subdivide, the safe end.
STAGE_OF = {
    # subdivide
    "part": "subdivide", "refineLength": "subdivide", "previewQuality": "subdivide",
    "preserveUntextured": "subdivide", "maxTriangles": "subdivide", "harvestFlatFaces": "subdivide",
    "harvestTol": "subdivide",
    # map
    "mappingMode": "map", "cylinderCenterX": "map", "cylinderCenterY": "map", "cylinderRadius": "map",
    "frame": "map", "rotateX": "map", "rotateY": "map", "rotateZ": "map", "scaleU": "map", "scaleV": "map",
    "offsetU": "map", "offsetV": "map", "rotation": "map", "rotateAroundZ": "map", "mappingBlend": "map",
    "seamBandWidth": "map", "capAngle": "map", "snapSeamlessWrap": "map", "stampOnce": "map", "layers": "map",
    "stampCenterX": "map", "stampCenterY": "map", "stampCenterZ": "map", "stampSide": "map",
    "stampNormalX": "map", "stampNormalY": "map", "stampNormalZ": "map",
    # displace
    "amplitude": "displace", "invertDisplacement": "displace", "symmetricDisplacement": "displace",
    "textureSmoothing": "displace", "invertTexture": "displace", "texture": "displace",
    "boundaryFalloff": "displace", "boundaryFalloffCurve": "displace", "layer": "displace",
    "bottomAngleLimit": "displace", "topAngleLimit": "displace", "noDownwardZ": "displace",
    "smoothBottom": "displace", "includeOnly": "displace",
    # colours
    "active": "colours",
}
NO_RERUN = {"lockScale", "bakeMask", "useDisplacement", "rotate", "exclusionTool", "brushIsRadius",
            "brushPrecision", "brushSize", "brushHardness", "bucketThreshold"}


def stage_for(event, draft=None):
    """The stage a draft event reruns from, or None (nothing to rerun)."""
    kind, key = event.get("kind"), event.get("key")
    if kind == "set":
        if key in NO_RERUN:
            return None
        return STAGE_OF.get(key, "subdivide")
    if kind == "mask":
        keep = draft is not None and draft.get("preserveUntextured")
        # A new face set with Preserve Untextured; else the map stage, which
        # builds each layer's mask (its cover and hard faces) for the displace.
        return "subdivide" if keep else "map"
    if kind in ("frame", "part", "layers", "layer", "texture", "active"):
        return STAGE_OF[kind]
    if kind in ("undo", "redo") and isinstance(event.get("value"), dict):
        return _undo_stage(event["value"], draft)
    return "subdivide"  # reset, load (and an undo that does not say what it changed)


def _undo_stage(ch, draft=None):
    """The earliest stage an undo or redo touched (draft.changes), or None
    when it changed nothing the preview draws."""
    got = []
    if ch.get("part"):
        got.append("subdivide")
    if ch.get("frame") or ch.get("layers"):
        got.append("map")
    if ch.get("masks"):
        got.append(stage_for({"kind": "mask"}, draft))
    if ch.get("layer"):
        got.append(STAGE_OF["layer"])
    if ch.get("active"):
        got.append(STAGE_OF["active"])
    for k in ch.get("keys") or ():
        st = stage_for({"kind": "set", "key": k}, draft)
        if st is not None:
            got.append(st)
    return min(got, key=STAGES.index) if got else None


def _thread(fn):
    threading.Thread(target=fn, name="TexturizerPreview", daemon=True).start()


def _timer(seconds, fn):
    t = threading.Timer(seconds, fn)
    t.daemon = True
    t.start()
    return t.cancel


class _NullView:
    def __getattr__(self, name):
        if name == "get_body_visible":
            return lambda: True
        return lambda *a, **k: None


class PreviewController:
    def __init__(self, engine, draft, view=None, quicklook_ok=False, run_async=None, post=None,
                 schedule=None, debounce=DEBOUNCE_S):
        self.engine, self.draft = engine, draft
        self.view = view or _NullView()
        self.quicklook_ok = bool(quicklook_ok)
        self.run_async = run_async or _thread
        self.post = post or (lambda fn: fn())
        self.schedule = schedule or _timer
        self.debounce = debounce
        self.gen = 0
        self.cache = {}          # stage -> the engine's reusable result
        self.dirty = "subdivide"  # the earliest stage changed since the last result
        self.dropped = 0         # stale or aborted results dropped
        self.running = None      # the generation the worker is on, or None
        self.has_cpu = False     # the CPU node holds a mesh
        self._pending = False    # a job waits for the worker
        self._cancel_timer = None
        self._body = None
        self._unsub = None
        self.showing = None      # "quicklook", "cpu", "body" or None (closed)
        self.held = False        # a drag in the view: the CPU rebuild waits (hold)
        self._deferred = False   # a change came while held: one rebuild on release
        self.sub_gen = 0         # bumped by subdivide changes (and stop): a cold subdivide's own abort
        self.coarse_shown = 0    # coarse first looks shown (tests, the probe)

    # -- state ---------------------------------------------------------------
    def _v(self, name, *a):
        fn = getattr(self.view, name, None)
        return fn(*a) if fn else None

    @property
    def cpu_mode(self):
        """3D Preview on, or no shader (3.5: the CPU preview carries both)."""
        return bool(self.draft.get("useDisplacement")) or not self.quicklook_ok

    @property
    def current(self):
        """The CPU node shows the latest settings."""
        return self.has_cpu and self.dirty is None and self.running is None

    def _show(self, what):
        self.showing = what
        self._v("set_quicklook_visible", what == "quicklook")
        self._v("set_cpu_visible", what == "cpu")
        self._v("set_body_visible", what == "body" or (what is None and bool(self._body)))

    # -- open / close ----------------------------------------------------------
    def open(self):
        """Records the body's visibility (3.6); Quick look at once, or the
        plain body with mask colours while the first CPU job runs."""
        self._body = self._v("get_body_visible")
        self._unsub = self.draft.subscribe(self.on_draft)
        if self.quicklook_ok:
            self._v("update_quicklook", "subdivide")
        else:
            self._v("lock_preview", True, NO_QUICKLOOK)
        self._resume()
        if self.cpu_mode:
            self._start()

    def _resume(self):
        if self.quicklook_ok:
            self._show("quicklook")
        elif self.has_cpu:
            self._show("cpu")
            self._v("set_cpu_stale", True)
        else:
            self._show("body")

    def close(self):
        self._stop()
        if self._unsub:
            self._unsub()
            self._unsub = None
        self._show(None)

    def cancel(self):
        """Cancel: the job stops and the body comes back (3.6)."""
        self._stop()
        self._v("progress", None, 0.0)
        self._show(None)

    def _stop(self):
        self.gen += 1
        self.sub_gen += 1
        self._pending = False
        self._cancel_pending_timer()

    def _cancel_pending_timer(self):
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None

    # -- changes -----------------------------------------------------------------
    def on_draft(self, event):
        if event.get("kind") == "set" and event.get("key") == "useDisplacement":
            return self._toggle_3d()
        stage = stage_for(event, self.draft)
        if stage is None:
            return
        if timing.enabled():   # which stage, and what asked for it
            what = event.get("key") or ""
            ch = event.get("value") if event.get("kind") in ("undo", "redo") else None
            if isinstance(ch, dict):
                what = ",".join([k for k in ("part", "frame", "layers", "masks", "layer", "active") if ch.get(k)]
                                + list(ch.get("keys") or ()))
            timing.report(f"preview request {stage} ({event.get('kind')} {what})".rstrip(), 0.0)
        if self.quicklook_ok:
            with timing.stage(f"preview quicklook update ({stage})"):
                self._v("update_quicklook", stage)  # uniforms only: < 16 ms a frame
        self.request(stage)

    def _toggle_3d(self):
        if not self.cpu_mode:  # off, with the shader: the Quick look only
            self._stop()
            self._show("quicklook")
        elif self.current:
            self._show("cpu")
        else:
            self._start()

    def request(self, stage, immediate=False):
        """A change touching stage: bumps the generation (a running job goes
        stale), drops the cache from stage on, and in CPU mode runs after the
        debounce (immediate: at once)."""
        self.gen += 1
        if stage == "subdivide":
            self.sub_gen += 1
        i = STAGES.index(stage)
        for s in STAGES[i:]:
            self.cache.pop(s, None)
        if timing.enabled():   # who asked: the start stage's reason in the worker's probe line
            import sys

            f = sys._getframe(1)
            self.why = f"{stage} by {f.f_code.co_name}" + (f" < {f.f_back.f_code.co_name}" if f.f_back else "")
        if self.dirty is None or i < STAGES.index(self.dirty):
            self.dirty = stage
        if self.showing is None:  # after Cancel or an error, a change resumes
            if not self._unsub:
                return
            self._resume()
        if not self.cpu_mode:
            return
        if stage != "colours" and self.showing == "cpu":
            if self.quicklook_ok:
                self._show("quicklook")  # the stale CPU node hides while dragging
            else:
                self._v("set_cpu_stale", True)  # 3.5: the last preview stays, grey
        self._cancel_pending_timer()
        if self.held and not immediate:
            self._deferred = True
            if self.quicklook_ok:
                return   # the Quick look carries the drag; one rebuild on release
            self._cancel_timer = self.schedule(DRAG_DEBOUNCE_S, lambda: self.post(self._start))
        elif immediate:
            self._start()
        else:
            self._cancel_timer = self.schedule(self.debounce, lambda: self.post(self._start))

    def hold(self, on):
        """A drag in the 3D view (a Rotate ring, a stamp, a brush stroke):
        while held, changes update the Quick look (uniforms only) and the
        CPU-displaced preview is not rebuilt per move: with the Quick look
        it waits for the release; without it, it is coalesced at
        DRAG_DEBOUNCE_S. The release starts one rebuild at once (when
        anything changed), with the same inputs as any other change: the
        settled result is the same mesh."""
        on = bool(on)
        if on == self.held:
            return
        self.held = on
        if not on and self._deferred:
            self._deferred = False
            self._cancel_pending_timer()
            self._start()

    def commit(self):
        """Enter in a box: what waits for the debounce runs now."""
        if self._cancel_timer:
            self._cancel_pending_timer()
            self._start()

    # -- the worker ------------------------------------------------------------------
    def rerun_from(self):
        """The first stage to run: the first one missing from the cache, or
        the earliest changed one; None when the CPU result is current."""
        for s in STAGES:
            if s not in self.cache:
                return s
        return self.dirty

    def _start(self):
        self._cancel_timer = None
        if not self.cpu_mode or self.showing is None:
            return
        self._deferred = False
        if self.running is not None:
            self._pending = True  # one worker: this runs when the stale job stops
            return
        start = self.rerun_from()
        if start is None:
            return self._swap()
        if timing.enabled():
            missing = [s for s in STAGES if s not in self.cache]
            timing.report(f"preview start {start} (missing {','.join(missing) or '-'}; dirty {self.dirty}; "
                          f"last request {getattr(self, 'why', '?')})", 0.0)
        gen = self.gen
        quality = self.draft.get("previewQuality") or "draft"
        inputs = {"draft": self.draft.to_dict(), "rerun_from": start,
                  "cache": {s: self.cache[s] for s in STAGES[:STAGES.index(start)]},
                  "tri_budget": TRI_BUDGET.get(quality, TRI_BUDGET["draft"]), "memory_limit": MEMORY_LIMIT}
        sg = self.sub_gen
        inputs["subdivide_abort"] = lambda: sg != self.sub_gen
        self.running = gen
        marks = []   # (stage, perf_counter) as the worker reaches each stage: the timing probe
        kept = getattr(self.engine, "kept", None) if start == "subdivide" else None

        def should_abort():
            return gen != self.gen

        def progress(stage, fraction):
            if not marks or marks[-1][0] != stage:
                marks.append((stage, time.perf_counter()))
            self.post(lambda: self._progress(gen, stage, fraction))

        def first_look():
            """The coarse preview, when the full one would be slow; posted to the GUI thread."""
            try:
                if kept is None or kept(inputs):
                    return
                tc = time.perf_counter()
                res = self.engine.preview(dict(inputs, coarse=True), lambda *a: None, should_abort)
            except Exception:  # noqa: BLE001 - the full run reports its own failure
                return
            ms = (time.perf_counter() - tc) * 1000.0
            if res is not None:
                self.post(lambda: self._engine_timings("preview coarse", res, ms))
                self.post(lambda: self._coarse(gen, res))

        def work():
            first_look()
            t0 = time.perf_counter()
            try:
                res, err = self.engine.preview(inputs, progress, should_abort), None
            except Exception as e:  # reported on the GUI thread
                res, err = None, e
            t1 = time.perf_counter()
            if res is not None:
                self.post(lambda: self._engine_timings("preview", res, None))
            import sys

            sub = dict(getattr(sys.modules.get(__package__ + ".engine"), "LAST_SUBDIVIDE", None) or {}) \
                if start == "subdivide" else None
            if sub:
                self.post(lambda: timing.report("preview subdivide cache " + " ".join(f"{k} {v}" for k, v in sub.items()), 0.0))
            self.post(lambda: self._timings(start, marks, t0, t1))
            self.post(lambda: self._done(gen, res, err))

        self.run_async(work)

    @staticmethod
    def _timings(start, marks, t0, t1):
        """The worker's stages, printed on the GUI thread (timing.py)."""
        if not timing.enabled():
            return
        for k, (st, t) in enumerate(marks):
            end = marks[k + 1][1] if k + 1 < len(marks) else t1
            timing.report(f"preview {st} (worker)", (end - t) * 1000.0)
        timing.report(f"preview from {start} (worker, total)", (t1 - t0) * 1000.0)

    @staticmethod
    def _engine_timings(prefix, res, total):
        """The engine's own stages (subdivide1, regularize, subdivide2, map,
        displace, ...: engine.preview's "timings"), printed on the GUI thread."""
        if not timing.enabled() or not isinstance(res, dict):
            return
        for name, ms in res.get("timings") or ():
            timing.report(f"{prefix} {name} (engine)", ms)
        if total is not None:
            m = res.get("mesh") or {}
            n = len(m.get("faces", ())) if isinstance(m, dict) else 0
            timing.report(f"{prefix} (worker, total, {n} triangles)", total)

    def _coarse(self, gen, result):
        """The coarse first look, shown while the full preview runs; a change
        since (another generation) drops it, and the full result replaces it."""
        if gen != self.gen or self.showing is None or not self.cpu_mode:
            return
        self.has_cpu = True
        self.coarse_shown += 1
        with timing.stage("preview show_cpu coarse (Coin mesh rebuild)"):
            self._v("show_cpu", result.get("mesh"))
        self._v("note", PROVISIONAL_NOTE if result.get("provisional") else COARSE_NOTE)
        self._v("set_cpu_stale", False)
        self._show("cpu")

    def _progress(self, gen, stage, fraction):
        if gen == self.gen:
            self._v("progress", stage, fraction)

    def _done(self, gen, result, err):
        self.running = None
        if gen != self.gen or (result is None and err is None):
            self.dropped += 1  # stale or aborted
            if self._pending:
                self._pending = False
                self._start()
            return
        self._pending = False
        if err is not None:
            self._v("error", f"Preview failed: {err}")
            self._v("progress", None, 0.0)
            self._show(None)
            return
        self.cache.update({s: v for s, v in (result.get("stages") or {}).items() if s in STAGES})
        self.dirty = None
        self.has_cpu = True
        if result.get("note"):
            self._v("note", result["note"])
        with timing.stage("preview show_cpu (Coin mesh rebuild)"):
            self._v("show_cpu", result.get("mesh"))
        self._v("progress", None, 1.0)
        self._swap()

    def _swap(self):
        self._v("set_cpu_stale", False)
        if self.cpu_mode and self.has_cpu:
            self._show("cpu")

    # -- the shader at run time --------------------------------------------------------
    def quicklook_failed(self, reason=""):
        """The shader stopped working (a Coin error later on): the CPU
        preview from now on, 3D Preview locked on (3.5)."""
        if not self.quicklook_ok:
            return
        self.quicklook_ok = False
        self._v("lock_preview", True, NO_QUICKLOOK)
        if reason:
            self._v("note", reason)
        if self.showing is None:
            return
        if self.current:
            self._show("cpu")
            return
        if self.has_cpu:
            self._show("cpu")
            self._v("set_cpu_stale", True)
        else:
            self._show("body")
        self._start()
