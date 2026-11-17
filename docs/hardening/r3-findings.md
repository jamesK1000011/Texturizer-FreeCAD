<!-- SPDX-License-Identifier: AGPL-3.0-only -->
# Window review round 3: Texturizer findings and fixes

Source: the round 3 window review (Texturizer section) and the live crash
after a Texturizer batch (FreeCAD 1.1.3, macOS). Worked from the code and
FreeCAD 1.1.3's source; no FreeCAD here, so every item below has a live
check to run.

## A. Crash: SIGBUS after `texture_preview { cancel: true }`

**What was seen.** The last calls were `texture_layer`, then
`texture_preview` answering `{ cancelled: true, job: "job-…" }`. The Report
view had "QObject::startTimer: Timers can only be used with threads started
with QThread" from 0014 on. Stack: a Python slot, `Gui.updateGui()`,
`QTimerInfoList::activateTimers`, a timer delivered to a deleted QObject.

**Cause (sure, from code).**

1. The `job` key in the answer means Agent Link ran the call on its job
   thread: `texture_preview` is `heavy: true` and `changes: false`, so
   `agentlink/jobs.py wanted()` sends it to a worker
   (`threading.Thread`, not a QThread).
2. On that thread `texture_preview` touched Qt:
   - `handlers.py _watch()` (old line 974) called `live.GUI.open()`, which
     can build the sidebar and calls `_part_changed()` →
     `PreviewController.request()` → `window._schedule()`, which made and
     started a parentless `QTimer` on the worker (old `window.py:51-56`):
     the "startTimer" messages.
   - `cancel: true` called `Window.cancel_preview()` →
     `PreviewController.cancel()` → `_cancel_pending_timer()` →
     `QTimer.stop` of a GUI-thread timer from the worker, and the last
     Python reference to that timer was dropped there, so its C++ object was
     deleted on the worker. Qt cannot unregister a timer from another
     thread, so the GUI thread's timer list kept an entry for freed memory;
     the next `updateGui()` fired it: the SIGBUS stack.
   - `show_result()` drew Coin nodes from the worker (the "no change in the
     view" at 0014).
3. Two more faults of the same kind: `window.open()` made a new `Poster`
   QObject on every reopen and dropped the old one while the old
   controller's worker could still post to it; `linked.Reruns.install(...,
   later=lambda fn: _schedule(0.25, fn))` threw away the cancel function,
   which was the only reference to the timer, so that timer was deleted at
   once and the "transaction still open" retry never ran;
   `docdraft.POST` used `QTimer.singleShot` from whatever thread changed the
   draft.

**Change.**

- `texturizer/guithread.py` (new): `GuiQueue`, made once on the GUI thread
  (refused elsewhere). `post(fn)` is a queued signal; `schedule(s, fn)`
  makes a single-shot `QTimer` parented to the queue's QObject on the GUI
  thread only (posted there when asked from a worker), deletes it with
  `deleteLater` after it fires or is cancelled, and runs the callback only
  while the owner is valid (`shiboken.isValid`); `cancel()` is safe from
  any thread; `close()` stops every timer. `on_gui_thread()`.
- `window.py`: `_poster()` and the bare-timer `_schedule()` are gone;
  `_queue()`, `_post()`, `_schedule()` use the one GuiQueue. `open()`,
  `show_result()` and `cancel_preview()` hand themselves to the GUI thread
  when called from another one; the draft's subscriber hops too. Reopening
  the sidebar closes the old controller and waits up to 1 s for its worker
  before a new one is made. `Reruns` and `docdraft.POST` use the queue.
- `preview.py`: `_thread()` returns the thread; the controller keeps it;
  `cancel(wait=s)` joins it; `worker_alive()`, `wait()`; a draft event on
  another thread is posted to the GUI thread (`on_gui`).
- `handlers.py`: `texture_preview { cancel }` waits up to 2 s for the
  worker (a `note` says so if it had not ended); file dialogs only on the
  GUI thread (`_asks()`): a job writes to the default name.
- `fam_texture.py`: `IN_PLACE` for the five draft tools, told to Agent
  Link with `jobs.in_place(name, why)`, so they never run as jobs.
- `tools.json`: `texture_preview` and `texture_export` say so (facts added).
- Tests: `tests/test_threads.py` (a Qt stand-in that records the thread of
  every timer call): post/schedule/cancel from a worker, close, and
  cancel-then-destroy of a running preview leaves no worker thread and no
  live timer.

**How sure.** Sure of the mechanism and that these paths ran on the job
thread; the exact timer that fired is not named by the stack.

**Live check.**

1. `texture_draft { part: "Grip" }`, `texture_preview {}` — the answer has
   no `job` key; `report_read { since: "last" }` has no "QObject" or
   "QThread" line.
2. `texture_preview { background: true }` — refused: "texture_preview
   cannot run as a job — it changes nothing, but it draws the preview in the
   3D view …".
3. `texture_set { settings: { amplitude: 0.6 } }`, then at once
   `texture_preview { cancel: true }` — `{ cancelled: true }` with no
   `note`; `report_read` clean; repeat 5 times, then `view_snapshot`.
4. `texture_export { format: "stl", background: true }` (no path) — writes
   `<part>_textured.stl` in the output folder, no dialog.

## B. Words, ring and result for `texture_preview`, `texture_export`, `texture_prepare`

**Cause (fairly sure).** All three are heavy reads, so Agent Link runs them
as jobs; reads that are not heavy (`texture_check`, `texture_list`) got
their own words. A read lights a button only when it switches the bench
(`agentlink/echo.py before()`: `cmd = command_for(...) if switch`), and a
read switches it only when declared `OPENS`; Texturizer declared no
`OPENS`, so no Texturizer button was ever lit. `texture_export` was mapped
to `Texturizer_AddToDocument`, which is in the menu only (no toolbar
button). The results had no words of their own (no `PHRASES`).

**Change (`fam_texture.py`).**

- `OPENS`: `texture_draft`, `texture_set`, `texture_mask`,
  `texture_layer`, `texture_preview` (they open the sidebar: true).
- `command_for(name, args)`: `texture_export` → `Texturizer_Export3MF`
  (3mf), `Texturizer_SaveProject` (bumpmesh), `Std_Export` (stl);
  `texture_draft` → `Texturizer_Reset` / `Texturizer_LoadProject` /
  `Texturizer_Texture`; `texture_set` with only a texture →
  `Texturizer_Gallery`; `texture_preview { turntable }` →
  `Texturizer_Turntable`. `COMMANDS["texture_export"]` is now
  `Texturizer_Export3MF`.
- `PHRASES` for `texture_preview` (cancelled, turntable, suggest,
  "Previewed the texture on {part}: {triangles} triangles"),
  `texture_export` ("Exported {path}: {triangles} triangles", project,
  cancelled), `texture_prepare`, `texture_apply`. Checked against Agent
  Link's own `activity.did` in `tests/test_gui.py`.
- `SELECTS`: the answer key holding what a read worked on
  (`texture_preview: part`, `texture_export: object`,
  `texture_prepare: body`, `texture_check: object`).

**Hub side (not here).** (1) `echo.declare` reads `SELECTS` and
`_read_shown` selects `body[SELECTS[name]]` (today it selects only what
the arguments name, and never a PartDesign Body). (2) A read run as a job:
its words and selection when its answer comes back (in the call when the
job ends within 45 s; at `job_status` when it ends later — `job_status` is
`WATCH`, so today it keeps the earlier caption). (3) Optional: a read's
declared button lit without a bench switch (for `texture_export`,
`texture_prepare`, `texture_check`, which open nothing).

**How sure.** The declarations: sure (they follow `echo.declare`). That
jobs are what lost the words: fairly sure (every stale caption in the
review, here and in Workshop, is a heavy read); not proven from the hub's
code.

**Live check.** `texture_preview {}` → `session_state` shows the echo
trace `{ tool: "texture_preview", button: ["Texturizer_Texture", true],
words: "Previewed the texture on Grip: … triangles" }`; then
`view_snapshot`: Texturizer bench, the Settings Sidebar button ringed.
`texture_export { format: "3mf", path: "grip.3mf" }` → `session_state`
words "Exported grip.3mf: … triangles".

## Not done here

- 0010 `texture_apply` panel showing Knurling not Hexagon, and the preview
  not following `texture_set` / `texture_mask` / `texture_layer` (0015,
  0016, 0019): not in this round's list. After the fix above the draft
  tools run on the GUI thread, which may already change 0015-0019; check
  with the same calls and `view_snapshot`.
