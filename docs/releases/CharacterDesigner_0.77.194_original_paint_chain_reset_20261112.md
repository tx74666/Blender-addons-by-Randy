# Character Designer 0.77.194 — Original Pose, painting and selected-chain Reset

**Completed 2026-11-12: source/native validation, official build and matching
dual deployment checks passed; current artist strict ALREADY194 audit preserved
protected authors/context/display with zero writes.**

Original Pose has the same formal-session meaning on Rig and Weight. Entering
preserves the visible pose and releases only generated constraints admitted by
the existing owner. Controls matches native edits, including related dormant
limb inputs, then restores the saved constraint state while retaining IK/FK.
A temporary native display does not establish an active Original Pose session.
Entering Original Pose does not reset the character.

Painting uses **Paint Weights / Test Pose / Paint Weights / Finish Painting**.
Finish retains newly edited weights and restores that painting session's saved
test-pose baseline, frame and Auto Key. Painting and formal Original refuse each
other while active; recovery remains available. Rest, calibration, animation and
bindings retain their existing ownership boundaries.

**Reset Selected Chains** is a separate explicit operation. A supported selected
native limb, spine or finger resolves to its complete related chain and resets
its current Rest-relative pose under its current outside parent. Related Controls
match without changing IK/FK. Unsupported or mixed selection refuses. Alt+R keeps
its established wrist scope. Reset is not triggered by entering Original Pose.

## Complete source and disk delivery

Complete194 extends approved193 by seven changed files and one new file:
`__init__.py`, `README.md`, `body_original_mode.py`, `body_original_owner.py`,
`limb_ik_wrist_rotation_ui.py`, `weight_paint_entry.py`,
`weight_paint_pose_session.py`, and new `body_pose_reset.py`.
It contains271 files/239 Python, with263 inherited files exact. Projection:
`D:/Blender/Projects/Character/X/validation/animation_recovery_20261112/stage194_03/projection.json`
SHA256 `999f18ddecd419e5038df5a1ae835e8b0251492212e88a0a3665ea55ad0d8dce`.

Official ZIP SHA256
`d1cdec9bde16bd329fc8684f3dc8feeff8e98041cf8dedf148c113916291d975`
passes CRC and exact271-file payload verification. Roaming Blender5.2 and X each
match all271 payload files. Original official build/deploy/matching-check commands all completed exit0;
`official_commands194_01.json` preserves the three exact completed events.
Independent host `delivery_readback194_01.json` also establishes bytes/CRC. Deployment backup:
`C:/Users/Randy/AppData/Local/CodexBackups/addon-deploy/20261112-010114-d4c859b4`.

Unrelated canonical work remains excluded, including current differences in
`animation_worklist_ui.py`, `body_region.py`, `body_setup.py`, `bone_display.py`,
`hair_strand_registry.py`, `unity_export.py`, `unity_export_ui.py` and
`unity_export_worker.py`. The194 increment files match the reviewed projection.
Use the complete approved projection, not plain canonical or an old updater.

## Validation and limits

Eight final native Original/painting groups pass on stage194_03, covering actual
native forearm edits, dormant IK/Pole matching, passive Action history arrays,
edited weights through Test Pose/Resume/Finish, mutual session refusal and rollback
after real matching writes. Sixteen Controls and nine Hair native groups plus
five nested author-mutation refusals pass on stage194_01. Their exercised paths
are inherited through the bounded static proof `native_inheritance194_01.json`;
this is not a native rerun on final194 or whole-module behavior equivalence.

Thirteen Reset groups pass on stage194_02, including both arm sides/modes, whole
finger and spine, foot/toe/pivots, Original session, genuinely keyed Actions and
rollback. Reset backend/UI bytes match final194. The only stage02-to03 change is
typed IDPropertyArray history support in the owner helper, separately proved by
the final entry8. Reset with that passive-array combination is not directly tested.
See `chain_reset_closure194_01.json` and `native_entry_summary194_01.json`.

The scoped updater passes six native groups, including28 journal/postcheck fault
boundaries, partial native registration rollback, active legacy checkpoint Finish
and cold/warm zero-write repeat. Existing definition/default/RNA/keymap identities
remain protected; the new Reset operator is the single admitted RNA addition.
See `updater194_02.result.json`. Earlier fixture/product refusals, failed receipts
and pure-test limitations remain unchanged and linked by the final acceptance.

## Actual artist194 audit

The current artistPID24068 was already194. The strict pinned
audit returned **ALREADY, zero writes**, with no new RNA registration. Both fresh
reads proved the current raw Rig, six meshes, all9 Actions,
STATE None/Finish values and aliases, display, raw pose and context exact.
Protection SHA256: `7ee54969f75094a947a2954c5a24a70dcb37cdfd8cf53c1cdaa314f19bae11f9`.
Observed POSE/frame22, Auto Key
Off, active `CTRL_knee_pole.L`,
dirtyTrue; visible selection locks after:0. The refresh time and
initiator preceding this audit remain unknown; no193-to194 identity continuity
is claimed. This audit changes no pose, weights, display, Finish state or scene.

Actual report: `artist_audit194_02.json`, SHA256 `4c5edb43c437eaf7f6f5b62576a33f9cafa97ae9b169e99bbc0bcfde7269e7b4`.
Acceptance: `acceptance194_01.json`, SHA256 `f8a083285e02eae1f83735d62a5c31a2904270ddd6a919e01a57e4938d53a8eb`.
The witness covers Main Rig raw data, six meshes and all Action native contents;
arbitrary other scene objects, unrelated mesh custom attributes and evaluated
solve are outside its scope. UI/owned-cleanup observation is in
`root_ui_observation194_01.json`. Root restored the lower-left Graph Editor, observed the current
dirty/Pose/frame22/CoshaRig/knee-Pole/no-selected-bone context and released the
foreground. No full author reread after that editor-only change is claimed.
The executed task-generated command left an empty Console prompt; no historical
unsubmitted189 text restoration is claimed. Root launched no Blender worker;
only the existing artist process remained. UI observation is separate from
product behavior acceptance.

The initial audit wrapper refused its raw Windows path-string spelling before
the current probe or commit. `artist_activation194_02.py` changes only that
comparison to exact keys/SHA/resolved-path equality. The original failure and
scripts remain retained; no product gate or source changed. The later strict
audit success does not relabel that refusal or repair prior history.


The artist acceptance must distinguish the current strict audit from any earlier
unrecorded refresh. No actual194 Original/Paint/TestPose/Finish/Reset, brush,
physical mouse selection, Play or visual behavior acceptance follows from these
fixtures or a code-only audit. No scene/preferences save, restart/full reload,
rebuild/reweight/calibration recapture, Unity write or Git upload is performed.
The existing playback-FPS branch remains closed.
