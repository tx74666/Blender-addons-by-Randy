# Character Designer 0.72.0 — pose Dress originals

Date: 2026-10-03, Asia/Shanghai. Owner: X / Character Designer. Local source,
package and deployment are complete; no commit, push or public release was made.
The execution model and reasoning effort were not independently verified for
this task and are recorded as unrecorded.

## Behavior and use

Previously, rotating a Dress deform bone in Original could change its pose
channels without changing the skirt: the generated Copy Transforms constraint
replaced those channels. Original now accepts a native local pose correction
while the existing curve, Spline IK and physics evaluation continues.

1. Choose **Rig / Weight > Bone Display > Original**.
2. In Pose Mode, select a Dress original bone and rotate with **R**.
3. Return to **Controls**. The adjustment remains, and the existing controls
   and physics continue working with it. Controllers are not relocated.
4. To remove corrections, use **Rig > Skirt > Clear Dress Pose**, or
   **Selected** for selected Dress originals, in Controls. Blender Undo is
   available for the mode switch and clear operation.

An older saved Original session upgrades on an explicit Original click or
another supported display/mode action. Panel drawing does not mutate the rig.
Unrecorded dormant deform channels are neutralized on entry while preserving
the currently evaluated pose, so unsuccessful earlier rotations are not
silently reintroduced. The waist remains its existing editable control.

## Implementation and protection

The owned manual Copy Transforms constraints use **Before Original (Full)**
(`BEFORE_FULL`) so native deform channels contribute instead of being replaced.
Corrections and Original recovery state persist in the blend. Returning to
Controls retains the authored correction, without resetting the artist pose
or mirroring it into a new controller position.

The transfer captures evaluated Dress matrices before Body transfer. A bounded
native local correction compensates numerical changes in the curve/Spline IK
evaluation, preserving shear rather than decomposing the evaluated pose into
location/rotation/scale. It performs at most three batched correction updates,
checks the final output including the Body forearm refresh, and rolls back the
complete transaction on failure.

Ownership, generated constraint order and targets, spaces, mixes and the
physics influence driver are checked before mutation. Foreign constraints,
native deform/waist transform animation or drivers, and unsupported constraint
animation, including actions nested in NLA meta strips, refuse safely. Rebuild
and physics setup/bake actions require the appropriate Controls state;
corrections must be cleared before an unsupported legacy rig migration.

No new bones, helper objects or Python frame handlers are added. Mesh topology,
Shape Keys, UVs, vertex groups and weights, Rest bones, permanent armature
relationships and the character's bone colors are preserved.

## Validation

Isolated background tests used Blender **5.1.0**, factory startup, disabled
auto-execution and serial processes. Earlier failed diagnostic runs remain in
the evidence directory; the table identifies the final successful evidence.

| Check | Final result / evidence |
| --- | --- |
| Dress Original | 17 cases pass, including native curve/physics, persistence, guards and transaction rollback; `dress_original_final17_5_1.log` |
| Body Original | 17 cases passed in the initial 18-case run; the one obsolete Dress-channel assertion was updated to the new entry contract and its affected case passed separately. Final Controls expectations remain strict; `body_original_5_1.log`, `body_original_affected_5_1.log` |
| Bone Display / UI pages | 14 / 6 cases pass; `bone_display_5_1.log`, `ui_pages_5_1.log` |
| Skirt workflow / shared physics | UI workflow and shared physics bake/save/reopen/remove pass; `skirt_ui_5_1.log`, `shared_skirt_physics_5_1.log` |
| Actual Cosha, isolated copy | A 0.08-radian deform-bone rotation moved the skirt skin by 14.6635 mm. Returning to Controls preserved the edited skin within 0.0008442 mm; unrelated Body/Hair/foreign pose changes were zero |
| Repeated transfer / reopen | Two complete no-edit round trips had zero measured pose/mesh drift and exact channels/correction records; candidate save/reopen had zero measured pose/mesh drift |
| Protected data | Raw mesh/keys/UV/weights, Rest bones, permanent relationships and all character colors compare equal |

Evidence: [real-model report](D:/Blender/Projects/Character/X/Validation/dress_original_20261003/real_x_dress_original.json),
[verification script](D:/Blender/Projects/Character/X/Validation/dress_original_20261003/verify_real_x_dress_original.py),
[successful integration log](D:/Blender/Projects/Character/X/Validation/dress_original_20261003/real_x_dress_original_compensated.log).
The isolated verifier saved only `X_dress_original_candidate.blend`; the artist
file hash and modification time were unchanged by those tests. The report's
pink count was corrected from the stored effective `THEME05` palette metadata;
its original RGB-based classifier result and provenance remain recorded.

These measurements describe pose preservation and deformation, not GUI latency,
FPS, memory savings or a new Unity export/animation compatibility result. The
previous performance changes remain; this release has no new speed benchmark.
Blender 5.2 received the matching source deployment but these native tests and
the artist-window verification ran in 5.1.

## Deployment and artist save

The verified package is `dist/character_designer-0.72.0.zip` with 132 files.
Canonical source was deployed to the verified active Blender 5.1 installation,
the Blender 5.2 installation, and X's validation add-on copy. Each deployment
`--check` reported zero differences. Deployment backups are
`addon-deploy/20261003-153328-b58bf253` and
`addon-deploy/20261003-153400-6c0e991b` under the local CodexBackups directory.

The current X window was refreshed, Original was explicitly upgraded, the
native constraint's Before Original (Full) setting was inspected, and
Original/Controls display was checked. No test rotation was applied to the
artist model. The final artist state is Original, Pose Mode, frame 39, with
Body/Hair/Dress visible and the existing pink Dress bones retained.

Blender reported **Saved "X.blend"** and showed a title without the unsaved
marker. The saved file is
[X.blend](D:/Blender/Projects/Character/X/X.blend), timestamp
**2026-10-03 15:48:10.336 +08:00**, 32,299,962 bytes, SHA-256:

`3a5f32ae26b996cadebc1ddacf3abf3e032e7c7fd3451b8989028229dd7daf01`

A [read-only saved-file check](D:/Blender/Projects/Character/X/Validation/dress_original_20261003/saved_artist_readback.json)
opened the actual artist file and the pre-operation backup without registering
the add-on, editing the pose or saving. It confirmed 351 total bones, 33 native
Dress bones with the existing pink palette, an active persistent Original
session, 32 Before Original (Full) deform constraints, unlocked native rotation,
and exact protected-data equality. Both files' hashes and modification times
remained unchanged by this verifier.

The persistent backup
[X_before_dress_original.blend](D:/Blender/Projects/Character/X/Validation/dress_original_20261003/before_live_change/X_before_dress_original.blend)
was saved at **15:32:53.473 +08:00**, before this Original-mode fix. It already
contains the merged 351-bone armature; it is not a pre-merge backup. SHA-256:

`d57a1b976ab803f601301bf5ff3b50826c2c454b4d42ef370395e23a0cb50e58`

Recheck only the relevant invariants if generated constraint ownership, native
animation/drivers, Rest relationships or the rig source changes, or if a new
pose-transfer failure is reported. Keep the earlier performance evidence and
avoid repeating a full unrelated investigation.
