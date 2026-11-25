# Character Designer 0.77.188 — Forearm Enable

Forearm Twist Correction now displays a checkbox-style control with the fixed
label **Enable**. Unchecking it disables both saved sides while native wrist
rotation and animation continue through the existing Armature skinning.

Previously, disabled records muted the owned corrective keys but still entered
runtime preparation and read topology, key coordinates, input mix, weights,
matrices and signatures. Entirely disabled owners now return before that work
and before requesting a dependency graph. The shared lifecycle handlers and
lightweight scene-owner/record-flag traversal remain; this is not zero callback
cost or an FPS measurement. A manually altered muted/output state is no longer
continuously repaired while the owner is disabled.

Enable reuses the existing calibration, keeps the current strict ownership and
binding validation, invalidates output/prepared caches and recomputes the current
pose. A renamed owned key is refused before preparation can repair its name.
Binding, support-topology, key-structure and calculation failures preserve the
previous Off records, output keys and author data. No reweighting, rig rebuild,
new listener, timer, Original permission change or historical-reference repair.

The complete release extends approved186 with only `forearm_twist.py`,
`__init__.py` and `README.md`: 270 files / 238 Python files, 267 exact inherited
files. Weight painting/pose previews from186 and all other inherited features
remain in the projection. All other Forearm helper/math files are byte-exact186.

Projection: `D:/Blender/Projects/Character/X/validation/forearm_enable_20261111/stage188_01/projection.json`
SHA256: `a6d6b8608e3e425e34ca68a955981640a016aaf065b551359e8a2f5639ac2923`.

Verification: 58 pure tests; four tiny bilateral native tests plus three existing
stale/ownership/UndoRedo recovery tests. Native tests cover Off across actual
animated frames against an independent LBS/correction oracle, zero calls to
geometry/preparation/evaluation traps while disabled, both sides, retained
calibration/weights/Keys/Rest/Action/slot/locks/selection, current-pose recompute,
renamed-key refusal, and a second-side calculation fault followed by recovery.
These are isolated generated fixtures, not a new saved-X or GUI FPS benchmark.

Official ZIP: `dist/character_designer-0.77.188.zip`, SHA256
`5e60740a0d63144e05e820313a21990876c32da175b220b71bcb4e41b181ef8d`.
CRC and all270 payload hashes match. Official deployment and matching `--check`
completed for Roaming Blender5.2 and X/addons, 270 files / 0 differences each.
Receipts: `validation/forearm_enable_20261111/{pure188_01,native188_02,disk188_01}.json`.

Preserved intermediate evidence includes the built/disk-deployed187 candidate
(never live-refreshed by this task), the188 native test's renamed-key lookup
postcondition failure and its original source, lifecycle mock/signature and
derivative-fixture failures, and updater refusals from unhandled native property
metadata, the persistent marker, and the absent-annotation snapshot. Later passes
do not relabel those failures. The final package has a distinct version and ZIP.

The new narrow updater passed13 native cases, auditing85 Forearm definitions and
seven injected code/version/postcheck fault boundaries. It changes only five
existing function code objects and the version entry; guarded validation observed
zero Scene/context/operator access and no RNA lifecycle. Function/import/class/
cache/property races refuse without repair; ALREADY performs zero writes.

Artist51688 / Blender5.2 / X.blend freshly audited **ALREADY188**. The five changed
functions and all85 Forearm definitions match the final projection. This task's
live transaction made zero code/version/RNA/Scene writes; the refresh time and
initiator are unknown, so no identity continuity from186 is claimed. A separate
before/after native author/context witness matched exactly, including meshes,
weights, Shape Keys, Rest, raw pose, Action/slot headers and retained text fields.
This is not whole-runtime protocol, all-attribute or all-Action-curve certification.

The physical UI shows fixed **Enable** with an empty checkbox. Cosha L/R records
are Off, both corrective keys remain muted. No live toggle or frame change was
performed. Final observed Pose / CoshaRig / hand.L / frame28 / AutoKeyOff /
dirtyTrue, Cosha+CoshaRig selected and no selected pose bones. Graph Editor was
restored and the Forearm panel left visible. An existing unexecuted186 Console
line was retained as an inert comment, never executed. Input released.

Final receipts: `validation/forearm_enable_20261111/artist188_01.json`,
`updater_native188_03/result.json`, `artist_enable188_01.png`, and
`acceptance188_01.json`. No agent scene/preferences save, restart/full reload,
Restore Main Rig, bake/export, Unity operation or Git commit/push. The closed FPS
investigation remains closed; the user's20FPS/10+FPS observation is not a measured
causal result or a promised target.
