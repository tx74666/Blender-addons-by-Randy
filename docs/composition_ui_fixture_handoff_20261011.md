# Standalone composition UI fixture — 2026-10-11

`tools/run_character_composition_ui_fixture.py` is a separate root-invoked
Blender entry. Importing it launches nothing. It opens only the immutable QA
seed saved by the official numeric fixture's explicit callback after numeric
and physical B proofs pass. The user artist file is never opened by this
runner. The numeric scope completes its own original-scene recovery first;
the UI process subsequently owns its intentional edits, Undo and save/reopen.
No runtime source, projection, deployment or release is changed by this tool.

Root invokes the tool inside its separately scheduled Blender process with
`--python <absolute runner path> -- --fixture <absolute actual fixture JSON>`.
Root still owns the verified executable, serial window, current memory read
and process lifecycle. Set `BLENDER_USER_DATAFILES` to an empty child of a new
UI evidence directory. Keep the fixture JSON outside that directory. Existing
reports, `.blend` files or cache files cause refusal before acceptance.

The strict fixture schema is `x.validation.character-composition-ui-fixture/1`.
Its exact fields are:

- `inputBlend`, `inputBlendSha256`: the original numeric driver's verified
  saved-artist input copy. Artist `X.blend` is rejected.
- `addonPackage`, `runtimeManifest`, `runtimeManifestSha256`: the same pinned,
  complete 167-file 0.77.25 projection used by numeric acceptance. The existing
  `verify_character_composition_native.runtime_projection` verifies and loads it.
- `numericReport`, `numericReportSha256`: the independently saved, completed
  native report, including original fixture path/hash and `qa_seed` receipt.
- `seedBlend`, `seedBlendSha256`, `sceneName`, `rigName`, `targetGuid`,
  `modelSha256`, `workItemId`: exact values from that receipt; `rigName` must be
  `CoshaRig`. Supply a real prepared B Work Item ID, never a row label or clip name.
- `evidenceDirectory`: the new directory for this process's cache and reports.
- `sessionHelper`: `{path, sha256}` for the pinned session acceptance helper.
- `sessionConfig`: required `createAWorkCopy` boolean, two to four distinct
  finite `seekSeconds`, and finite `poseTolerance` greater than zero and at
  most `0.0002`. Optional `rotationControl` and `aRotationControl` name exact
  native inputs; optional `aWorkItemId` is an actual existing UUID. Optional
  `headfulCheckpoints` uses the explicit schema below. The runner
  supplies the helper's output directory and `character_designer` module.
- `schema`: the schema above. Unknown fields and publication/output overrides
  are rejected.
- Optional `quitOnCompletion`: an explicit boolean, default absent/false.
  `true` requires headful checkpoints and requests normal exit of this admitted
  owned QA process only after completed acceptance, saves/reopens, final guards
  and report persistence. It never exits on startup, Cancel, timeout or failure.

The numeric report must prove prior Actions/assets and historical recovery,
and its seed receipt must pin the completed physical report and its hash.
The first two physical entries must be the same actual prepared SOURCE B
variants and fresh model as the numeric proof. Their two saved native item IDs
are authenticated separately from Work Item IDs.

The saved Rig retains every exact historical Scene membership plus the
official copied QA Scene. Both membership names and the native adoption owner
are checked; the original Scene is preserved. After loading the seed, the
runner verifies the exact target, current workspace/model, prepared Link,
readonly Source cache, sealed packet and 217-bone native scope. It explicitly
activates that row's Custom and proves its fresh native Action/slot association.
The linked-model API returns `(Path, digest)`; the runner unpacks it, checks
the digest against the fixture, and pins the actual file path. A changed model
still fails the canonical source helper before UI acceptance.

Run this UI process before Parent Collect/Prepare expands the shared workspace
catalog. The seed deliberately pins its original workspace bytes; that pin is
not replaced with a newer hash. The subsequent Raise stage has its own explicit
initial-snapshot and same-identity catalog-extension admission. The UI-edited
saved copies are never used as the Raise stage's collected Hold source.

`composition_session_acceptance.run` may then author the disposable B and
supported genuine A work copy, and save/reopen its active, closed and edited
fixtures. The caller keeps only plain IDs/reports across these operations and
never enters the numeric fixture's native-ID recovery `finally`. Disk hashes
for the immutable seed/input/runtime manifest/proofs/model/packet/Source caches
and all catalog Source/B Links are checked even when the session helper fails.
It starts no export worker or Source/B publication.

`composition_ui_run_report.json` records admission, helper results and exact
immutable file hashes. Native acceptance remains pending root's explicit
fresh window. `tests/test_character_composition_ui_fixture.py` has 24 passing
pure admission tests; these validate inputs, proof/ID/membership routing and
evidence ownership, and exercise the actual canonical model helper body for
tuple unpacking and digest/changed-file rejection. They do not claim Blender
UI, Undo or save/reopen success.

## Opt-in real native screenshots

The original synchronous `composition_session_acceptance.run(context, item_id,
config)` API remains available. Internally it drains `run_steps`, ignoring
visual checkpoints. No timer or image capture runs without the opt-in.

To review actual visible production panels, actor, weapon and controls, supply
`sessionConfig.headfulCheckpoints` with exactly these fields:

- `schema`: `x.validation.character-composition-ui-checkpoints/1`.
- `enabled`: `true`.
- `helper`: `{path, sha256}` pinning the actual canonical sibling
  `tools/composition_ui_checkpoints.py`.
- `settleSeconds`: finite `1..10`, normally `2`.
- `timeoutSeconds`: finite `30..3600`, normally `1200` per checkpoint.

Root launches a separately owned **headful** Blender process with a fresh
profile: `BLENDER_USER_CONFIG` and `BLENDER_USER_DATAFILES` must be distinct
children of its dedicated evidence directory. Existing files in either child
cause startup refusal. The opened file is still the exact immutable QA seed,
and the original workspace SHA pin remains unchanged. The first numeric
driver's held-ID recovery scope is never used for this process.

The runner schedules the actual session generator with a persistent
`bpy.app.timers` callback. Each yielded state returns control to Blender's
event loop, tags native areas for redraw, waits for the configured settling
period, and invokes `bpy.ops.screen.screenshot`. The real Viewport, Timeline
and production Character Designer panels remain visible. The temporary
`QA Visual Checkpoint` panel provides Continue, Recapture and Cancel; it is
tool-only and removed at completion or failure. Use the Character Designer
sidebar tab and frame the real actor before Recapture/Continue when necessary.
No raster image is synthesized, no GUI layout assertion replaces pixel review,
and no synchronous sleep blocks Blender's event loop.

The checkpoint sequence exposes the actual readonly A and Create A Work Copy
entry, C/Edit B with the measured imported weapon and one exact native FK
control selected, real native playback, stopped timeline seeks, the genuine
separate Edit A work copy, captured B rotation, and explicitly resumed
save/reopen state. Continue is enabled only after a native screenshot exists.
Root may independently inspect/capture the owned QA PID with Window.GCapture
before clicking Continue. Recapture always writes a new file.

`headful_checkpoint_report.json` pins PNG hashes, actual native/public work
identities, selected editing target, scope proof/channels, frame and playback
state. Each capture and acknowledgement reacquires the actual row/Scope/edit
state and rejects changed identity, editing target or playback status. A paused
checkpoint also requires its generated seek frame; only a playing checkpoint
allows frame progression. All produced PNG hashes are checked again before
acknowledgement and final completion. A screenshot is evidence requiring human review, not an automatic
claim that the view is visually correct. Every Continue and final completion,
Cancel or timeout checks immutable disk inputs plus native Source baselines,
the current model/packet and readonly A cache. A stopped check retains its
owned draft without implicit commit, save, Sync or artist-window cleanup.
Timers are persistent across the deliberate file reopen; native context and
IDs are reacquired rather than dereferencing stale Undo/load pointers.

Class registration, initial report persistence and timer registration form one
startup transaction. If any step fails, rollback attempts every successfully
registered temporary class, unregisters only the exact owned timer if present,
closes the generator, resets its global controller and rechecks protected inputs.
The retained failure report preserves the original startup error and records
secondary cleanup errors. A failed startup never calls the completion callback.

With explicit `quitOnCompletion=true`, a successful final report schedules a
three-second grace period for the final view. The timer rechecks immutable and
native protection guards before calling `bpy.ops.wm.quit_blender`; the report
records `normal_quit_requested`, while root independently verifies actual
process exit before Parent Collect. Failed guards or scheduling/quit failures
retain a failed report and keep the owned window available for root's normal
UI/API close. No external process termination or implicit artist save occurs.

The first pre-Collect seed contains actual HoldPose rows. Its report therefore
retains `raise_visual_pending=true`. Real Raise screenshot acceptance belongs
to the later window after genuine Prepare/Register/physical NEW publication.
The shared controller can accept a separately admitted Raise generator; only
an actual acknowledged Raise checkpoint with its authenticated following Hold
WorkItem can clear that separate visual-pending marker. No Hold DTO is edited
to simulate Raise, and the UI-edited Hold files are never its collected source.

The two UI suites now pass 44 focused pure admission/timer tests and the three
tools compile. Those checks launch no Blender, capture no pixels and do not
claim native UI acceptance. Frozen Python05/runtime projections are unchanged.

The immutable X validation tools-source manifest pins these tools, tests and
this handoff; earlier manifests remain preserved. No actual native IDs are
generated in examples.

The actual n3 run exposed Blender's 63-byte IDProperty-key limit in the
runtime reference-cache receipt. New headful tool snapshots use the official
`reference_receipt(rig, full_sha)` accessor rather than indexing the full
64-character hex SHA directly. The accessor encodes the complete SHA without
truncation and checks the receipt's schema and full packet identity. The
visual guard still requires the exact readonly Action motion hash, cache-file
SHA and native linked-library path; a missing or malformed receipt rejects.
Immutable `tools_headful_01` and earlier execution snapshots remain preserved;
root must freeze a new tool snapshot with the matching runtime accessor before
launching the next headful process.

Three new focused tests execute the actual visual guard to prove accessor
use and rejection of missing/malformed receipts, changed cache/library and
changed readonly motion. They pass with compilation; the previous 44 UI checks
were not repeated for this narrow change. No native execution, screenshot,
fixture generation or runtime deployment was performed by this tool update.

The n5 failure exposed native Worklist row wrappers held across later collection
growth. New tool snapshots retain plain row IDs, require one exact current row,
and use the official workspace, route and association APIs. Each genuine B Add
immediately anchors its original full row/spec, Custom Action identity and slot,
and known content fingerprint before the next mutation. All later imports,
activation and isolated checks must match that original proof; a coherent
replacement with the same IDs and updated Rig association still rejects.

Create A Work Copy now anchors the original B Custom and Source identities,
slots, full row/spec and motion hash before adding A. It reacquires A and B by
plain IDs afterward and checks B against that original proof before capturing
the session baseline. Undo and file reopen continue to reacquire native rows.
The UI runner also captures its original import anchor before activation and
calls the strict native proof API with that anchor; it has no old-signature or
empty-identity fallback.

Four focused native association/import-anchor tests and five focused session
lifetime/UI-caller tests passed with compilation. The earlier whole suites were
not repeated. These are source checks; native pixels, timers, Undo/reopen and
normal exit remain pending. Freeze a new matching tool snapshot; immutable
tools_headful_01/02 and all earlier attempt files remain unchanged.

The actual n7 failure reached Blender's moved Pose selection API. The new
product helper uses `PoseBone.select` when available and verifies the legacy
`Bone.select` before fallback. Tool selection resolves those real owners,
assigns the active Bone first, then writes the exact final Pose selection.
An unknown API or a real selection write failure propagates without creating
attributes on shared Armature Bone data. The existing three-argument Raise
viewport caller remains supported.

The five tool candidate filters now read actual Pose visibility through the
shared session helper `_pose_hidden`: `PoseBone.hide` when available, otherwise
the real legacy `Bone.hide`. Unknown or nonboolean visibility fails closed.
These checks change no hidden flags, colors or collection memberships.

Background timing and Raise authoring import this readonly visibility helper.
Their next snapshot must include the new matching session tool; an old `faf`
session copy is incompatible even with numeric `sessionChecks=None`. The
background run does not execute the session generator. Nine focused tool tests
prove modern/legacy selection, shared Bone separation, other Rig preservation,
active-before-final-flags, strict failures and Pose visibility. They passed with
compilation; prior whole suites were not repeated. New runtime and matched tool
snapshots still require root's explicit native window.
