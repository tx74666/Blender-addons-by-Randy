# Character Designer 0.77.55 · Preserve Shoe Object Placement

Validation record, 2026-10-18. Frozen candidate02 passed 15 native fixture cases,
11 pure transaction cases and the dense saved-X integration with an unkeyed
shoe Object offset. The ZIP and both installed copies passed final acceptance.
No claim about the current artist's unsaved scene follows from these fixtures.

## Problem and reproduced cause

The user clarified that **Generate / Regenerate moves the shoe Object**, and
Undo brings it back. The required behavior is to rebuild the selected controls
while retaining the already edited original bones and current model placement.
This investigation concerns generation changing Object transforms. It does not
attribute every previous Undo/Redo issue to the same cause.

An isolated .77.54 fixture retained a shoe Object Action with location keys at
frames 24 and 25. At frame 20 plus subframe `.375`, it added an unkeyed Z offset
of `.0745561`. All four public-operation checks reproduced the loss: regional
Rebuild, whole Body Setup Generate, and injected native failures in both paths.
Shoe Object Z changed from `.0825560986995697` to the Action value
`.00800000037997961`. The first recorded change was immediately after control
bone creation; the entry checks, checkpoint capture and regional release had
retained the intended input in these synthetic fixtures. Recovery did not
restore the shoe Object input. There were no test or observer errors.

Control creation used `scene.frame_set` at the current frame/subframe. A
same-time call still evaluates animation and can replace unkeyed edits. Removing
that call alone was insufficient: candidate01 passed only one of the four new
checks. Foot driver relation reconstruction and failure recovery could also
resample Object animation. The old transaction protected original ID references,
the rig, helper resources and authored mesh data; it did not restore bound-mesh
Object transform channels. Exact vertices, weights and native Rest therefore
did not establish unchanged visible shoe placement.

The separately pinned saved-X .54 baseline also reproduced the problem. With
the same added unkeyed offset, Rebuild was rejected with a Shoes surface guard
of `.0953027` rig units and remained unapplied. Raw mesh/Shape Keys/weights,
native Rest, Actions and hand inputs were exact, but Object preservation and
world-surface checks failed. Shoes moved by `.07455623279686069` world units;
the other six checked meshes had zero measured world movement. This input is
the newly saved file described below, not the older `.54` incident pin.

## Change and exact scope

Body transactions now retain authored transform inputs for the original rig
Object and mesh Objects bound to it through an Armature modifier. The snapshot
includes all rotation representations and rotation mode, location/scale and
deltas, parent identity/type/bone/vertices, and the parent-inverse matrix.
Native IDs are retained; generated widget resources are excluded.

At the relevant graph-evaluation boundaries, the active transaction restores
only changed input fields and verifies the channels and their derived basis.
It does not decompose or assign the world matrix. Successful completion restores
placement after returning the original context. Failure recovery restores it
after native graph/context restoration and again after display recovery.
Control creation uses an ID update tag instead of changing scene time.

This applies to **Body Setup Generate**, regional **Rebuild** and failure
recovery owned by their existing Body transaction. Legacy standalone IK or Foot
buttons do not necessarily enter that transaction and are not certified by
this release's Object-input protection. Ground/whole-character movement does
not enter this transaction; it remains an intentional placement operation.
The synchronization helper has no active-transaction effect outside that scope.

The update retains original mesh coordinates, topology, weights, Shape Keys,
native Rest and Action keys. It preserves the current unkeyed adjustment during
the operation; it does not mute animation or make that adjustment permanent.
Intentional later timeline changes normally resample the unchanged Action.

## Validation completed

All runs used Blender 5.2 build `fbe6228777e7` in disposable background processes.
The authoring tests never open/save the artist file or invoke Undo/Redo.

| Checks | Result | Approximate elapsed time |
| --- | --- | --- |
| Public Body Generate / regional Rebuild, success and injected native failure | 4 / 4 pass | 5.87 s |
| Existing pending-evaluation and subframe cases | 4 / 4 pass | 24.10 s |
| Existing fixed-Pole layout and protection cases | 7 / 7 pass | 13.73 s |
| Pure transaction join, recovery marker, Object-input order and rig scope | 11 / 11 pass | 0.061 s |
| Fixed saved-X Rebuild with unkeyed shoe Object offset | Applied; every named preservation gate passes | 58.36 s |

Public success is not inferred from `FINISHED` alone. The pinned tests require
generated controls, and regional success must change both selected LEG rig IDs.
A fully restored rejection cannot count as successful Rebuild. Whole Generate
actually created nine components. Object channels/basis/world, native IDs,
raw authored meshes, original Rest and Action contents were preserved. Injected
failure cases recovered with zero measured native-skin/world-surface error.
Existing success and rollback numeric limits were not relaxed.

The original nine pure assertions remain intact. Two new cases check that
successful completion restores Object inputs after context restoration; failure
recovery synchronizes after native context and after display recovery before
the completed-recovery marker. The helper refuses another rig, a same-name
impostor, a different Armature data pair, and calls without an active transaction.

## Saved-X integration and delivery status

The new immutable input is
`D:\Blender\Projects\Character\X\validation\shoe_redo_displacement_20261018\input_saved_X.blend`,
34,567,562 bytes, SHA256
`f545c3828be77465e59ac94c66ce1815bc98f80b4369d268d75c0ca4e694112b`.
It is a copy of a user-saved disk state. A synthetic `.0745561` shoe Object
offset and frame/subframe/mode preparation are applied only before its baseline.
The script runs with authorized embedded scripts enabled and never saves the
loaded blend file.

The .55 `saved_x055_report.json` passed with Rebuild actually applied and both
LEG rig IDs changed. All 15 named checks passed: original Object transforms,
raw meshes/weights/Shape Keys, native Rest, Actions, hand inputs, rig data
identity, time/mode, other regional IDs and ownership were retained. Both leg
Poles were in front on their own sides. All seven world surfaces passed the
unchanged `1e-4` limit; the largest distance was Shoes at
`8.126449047965124e-6`. Four other meshes had zero measured movement; Cosha and
Stocking were approximately `7.03e-6` and `7.12e-6`. The input pin and frozen
source remained unchanged. The report SHA256 is
`ee1416e81100ae1871e1eaef1ac439c3af3793490b630f3b91d695cc9f3f0b0a`.

The artist continued working and saving the original `X.blend` during these
isolated checks. That changing file is separate from the immutable validation
copy. Its current hash is observed separately and a difference is not attributed
to the plugin or the read-only acceptance writer. Only the fixed copy must
continue matching the test pin. Passing this check does not validate newer
saved changes or the artist's latest unsaved state.

Candidate02 contains 202 frozen files, with manifest SHA256
`8783078fa232b8507d042319877a0b3ee589967e4929684ee39de144c2608c4e`.
Its six-file delta over accepted .77.54 is restricted to `__init__.py`,
`limb_ik.py`, `body_setup.py`, `body_region.py`, `foot_controls.py` and
`body_setup_transaction.py`. Paused watcher/provenance/display optimization
changes remain excluded by the reviewed projection. No ownership or deformation
tolerance is widened.

The built package is `dist/character_designer-0.77.55.zip`, SHA256
`5b29d508f2bd15fad25470b3d645804beb743d2640c225fa37f209ce49817001`.
Blender 5.2 and X/addons each passed `deploy_local.py --check`: 202 files,
zero differences. `acceptance_summary.json` SHA256 is
`8f7764af162274856d54bf1709ab3184b1f16fde88810e6f869a839dff97b807`.
The native Refresh Add-on button was clicked in the live X window; its refreshing
label completed and the panel returned without an error. This is a UI completion
observation, not a separate in-process Python version or full model fingerprint.
The live artist's controllers were not rebuilt and its scene was not saved by
this task. Current unsaved artist work remains outside the fixed-input proof.
No Git commit, push or publication was performed.

## Local evidence and replay

The evidence directory is
`D:\Blender\Projects\Character\X\validation\shoe_redo_displacement_20261018`.
It retains `baseline054_report.json` (4 expected failures),
`candidate055_report.json` (candidate01: 3 failures / 1 pass),
`candidate02_report.json` (4 passes), `eval055_report.json` (4 passes),
`poles055_report.json` (7 passes), `pure_atomic_result.txt`, the pinned input,
`saved_x054_report.json` and the passing `saved_x055_report.json`.
Failure reports and their frozen sources remain intact.

Replay public tests with `run_shoe_transform_tests.py --source
candidate02/approved_source --report <fresh-report.json>` through
`tools/run_trusted_blender_validation.py` in an empty factory-startup background
process. `run_regression_tests.py` selects `--group eval` or `--group poles`.
`probe_saved_x_shoe_controls.py` loads only the fixed copy, with optional
`--unkeyed-shoe-z .0745561`. All runners retain their manifest and script/test
hashes. `write_acceptance.py` rejected incomplete evidence by design, then its
check-only and final acceptance runs both passed against candidate02.
