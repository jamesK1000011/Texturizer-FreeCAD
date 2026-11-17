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

# Second round: window review round 4

Source: the round 4 window review of Workshop, Texturizer and Tutor (71
pictures). Worked from the code and FreeCAD 1.1.3's source; no FreeCAD
here: each item has a live check.

## C. The preview showed up in other documents (Tutor 0001, 0002, 0009, 0013)

**Cause (sure, from code).** The draft names its part by the object's
Name ("Box"), and every lookup used the document in front:
`Window.snapshot()` → `live.mesh_for(d.part)` (the active document),
`CoinView._body()` (`FreeCADGui.ActiveDocument.getObject`), the draft's
follower (`DraftFollower.doc_fn`: the active document) and the tools'
`_part()`. The Tutor's PointBox in TutorRest is also Name "Box": it was
meshed and previewed, hidden as the preview's stand-in body, and followed
as the draft's part (the Report line "PointBox (the sidebar's draft): 2
new faces since texturing" is `linked.DraftFollower.follow` moving the
draft's masks onto it). And `CoinView._graph()` dropped its node
references when the view changed without taking the nodes out of the old
scene graph, so a shown preview stayed in a view nothing pointed to.

**Change.**

- `live.py`: `DRAFT_DOC`, `bind_doc`, `draft_doc`, `in_front`,
  `document_closed`: the draft is bound to the document its part was taken
  from (the tools bind before they set the part; a part set another way is
  bound to the document in front). `draft.set_part(force=)` says a part
  change when the same Name comes from another document.
- `window.py`: `_view()` is the active view only while the draft's
  document is in front; `snapshot()` is None otherwise; `_body()` is the
  part in its own document. `CoinView.detach()` takes the CPU node and the
  Quick look out of the graph they were added to; `_graph()` detaches on a
  view change. `Window.step_aside()`, `document_changed()`,
  `document_closed()`, `end_preview()`; `cancel_preview()` and `leave()`
  take the nodes out too.
- `handlers.py`: `Observer.slotActivateDocument` / `slotDeletedDocument`
  forward to the window; `_draft_home()` / `_take_part()`: the tools find
  the draft's part in its own document and bind a part they take;
  `texture_apply` and `texture_export` end the preview (`previewClosed`).
- `linked.py`: the follower reads the draft's own document.
- `turntable.py`: the pivot is the part in its own document.
- Tests: `tests/test_preview_document.py` (two stand-in documents, an
  object named Box in each).

**How sure.** Sure of the mechanism (the Report line names the follower).
That App's `slotActivateDocument` fires on every MDI switch: FreeCAD 1.1.3
`Gui::MainWindow` sets the active document on a window change
(`src/App/DocumentObserverPython.cpp` forwards it).

**Live check.** `texture_draft { part: "Grip" }`, `texture_preview {}`,
then `doc_new { name: "TutorRest" }`, `part_primitive { kind: "box", name:
"PointBox" }` (its Name is Box too), `view_snapshot`: PointBox shown, no
teal or orange; `report_read { since: "last" }`: no "the sidebar's draft"
line. Back: `doc_switch { document: "<the Grip document>", front: true }`,
`view_snapshot`: the preview again, Grip hidden under it. Then
`texture_apply {}`: `previewClosed: true`, `view_snapshot`: the Textured
object only. Close TutorRest and the Grip document in turn:
`report_read` has no error.

## D. texture_apply's panel kept Knurling; the preview did not follow set / mask / layer (0010, 0015-0019)

**Cause (sure, from code).** `texture_apply { texture }` runs
`_apply_args`, which never touched the draft the sidebar shows. And
`texture_preview` drew a CPU result (`show_result`) while the controller
stayed in Quick look mode (3D Preview off): later draft changes updated
the hidden Quick look only (`request()` returns early when not
`cpu_mode`), so the CPU picture stayed as it was. Where the CPU preview did
rebuild, it was after the debounce and the worker, after the call had
answered and the picture been taken.

**Change.** `_draft_follows_args()`: the draft takes the applied part,
texture, settings (texture_apply's names mapped: textureHeight →
amplitude, mapping modes by name), layers and face masks, in one undo step.
`PreviewController.forced` (set by `texture_preview`, cleared by close,
cancel and the person turning 3D Preview off) keeps the CPU preview through
draft changes; `texture_set`, `texture_mask` and `texture_layer` wait for
a shown CPU preview to catch up (`_follow_preview`, at most 20 s;
`preview { triangles, ms }` or `{ building }` in the answer).

**Live check.** `texture_apply { body: "Grip", texture: "Hexagon", name:
"Grip_hex" }` → `view_snapshot`: the sidebar's layer shows Hexagon.
`texture_preview {}`, `texture_set { texture: "Leather" }` → answer has
`preview.triangles`; `view_snapshot`: leather on the preview.

## E. Two rings; no ring on texture_apply / texture_export; words

**Cause.** Agent Link puts the last ring out only at a changing call
(`echo.rings_off` in `before`); Texturizer's draft tools are reads that
light a button (`OPENS`), so their rings piled up. `texture_apply`'s
command (Add to Document) had no toolbar button; `texture_export`,
`texture_prepare` and `texture_check` were reads that never switch the
bench, so no button lit. The words: no `PHRASES` for the draft tools;
`texture_mask`'s answer has `triangles` (the brush's), which the caption
added as "0 triangles".

**Change (`fam_texture.py`).** `one_ring()`: as a Texturizer tool starts
(GUI thread only), every echo flash ring but the newest is cleared through
Agent Link's overlay (`overlay.service().clear(owner="echo", handle=)`;
the Tutor's own rings are not the echo's). `OPENS` adds texture_export,
texture_prepare, texture_check. Add to Document on the Texturizer Panel
toolbar. `PHRASES` for texture_settings, texture_draft, texture_set,
texture_mask (the faces kept plain), texture_layer, the job's first answer
("… in the background"), texture_export by `file`; answers carry `part`
(and `label`, `texture`, `setNames`, `layerCount`, `file`) for them.
`SELECTS` for the draft tools; `OUT_MORE = {"texture_apply":
("object",)}`. Tests: `tests/test_gui.py` (OneRing, phrases).

**Hub side (not here).** (1) A read that lights its button (`OPENS`) puts
the earlier ring out, as a changing call does; `one_ring` is a stop-gap.
(2) `echo.declare` reads `SELECTS` ({tool: answer key}, the value a name or
a list of names) and `OUT_MORE` ({tool: (answer keys,)}). (3) A read run as
a job: its words when its answer comes back (`texture_prepare`'s stale
"Checked …" caption, 2000, is that).

**Live check.** `texture_draft { reset: true }`, `texture_preview {}`,
`texture_set { settings: { amplitude: 0.6 } }`, `view_snapshot`: one ring.
`texture_apply {}`: ring on Add to Document. `texture_export { format:
"3mf" }`: ring on Export 3MF; `session_state` words "Exported
Grip_textured.3mf: … triangles".

## F. texture_preview blocked the window with no limit

**Change.** `PreviewController.build_now(budget)`: the job starts at once
on the preview's worker (the engine's maths), and what the worker hands
back to the GUI thread (progress, the coarse first look, the result) is
run by the waiting call as it comes, so the result is drawn before the
answer. Past the budget it returns; the build goes on and its later
hand-offs go to the window's queue (it shows itself); the next
`texture_preview` waits for that same build (no second one); a preview
already current answers at once (`reused`). `texture_preview { wait }`
(default 45 s, at most 60); past it `building: true`, `verdict: "still
building"`, `next`. Headless (no window) the call runs the engine itself
as before. Tests: `tests/test_preview_wait.py` (a real worker thread).

**Live check.** On a large part: `texture_preview { quality: "fine", wait:
5 }` → `building: true` within about 5 s; `report_read`: no QThread or
QObject line; `texture_preview {}` → the triangles, `reused` false, the
engine run once (timing probe on: one "preview from subdivide" line).
