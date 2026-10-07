# Character Designer 0.77.0 — Save Pose and synchronized application

Select native Body bones or their corresponding public controls, choose
**Save Pose**, name it and optionally enable **Include Fingers**. One Action
asset is saved in **Current File**. Save the `.blend` to keep it. Double-click
the asset to apply through Character Designer; Shift-double-click mirrors it.

## Capture and application

Capture reads the evaluated native pose, including the visible IK result.
Native limb sources, targets and Poles resolve to the complete native chain.
Selected Region and All Body are supported. Native transforms, supported
B-Bone channels, authoring Rest and diagnostic control modes are recorded.
The assigned animation Action, keys, mode, selection and Original session
are preserved. Saving opens Current File, enables Action visibility and
focuses the new asset using Blender's deferred browser selection.

Application matches the existing control graph, including dormant limb
controls, while preserving the current IK/FK mode. In Original it temporarily
verifies the live constraint graph, updates the return checkpoint and resumes
native editing. Asset Rest provenance allows compatible controller rebuilds;
native skeleton changes are rejected. An unrepresentable current IK pose
fails with rollback rather than silently changing modes.

Rollback includes native custom shapes, collection layout, pose channels,
constraint mutes, B-Bone fields, Action/slot and the exact Original session.
Auto Key copies the preceding Action, clears the copied Pose marker and keeps
the original asset unchanged. Corrective output updates once inside the
verified transaction. Body reconstruction defers display synchronization
until its outer layout is ready; ordinary switching retains strict missing
or repurposed Body collection checks.

Hair, Dress, foreign controllers and generated mechanisms are outside this
capture scope. Unsupported constraints/drivers or transforms requiring shear
or singular scale are refused without creating a partial asset.

## Validation

Blender 5.2.0 LTS, isolated fixtures: 21 capture/application regressions pass,
including an independent Blender Action evaluation, cross-mode restores,
Original return, compatible controller rebuild, finger inclusion, capture
preservation, real partial key failure and complete display/session rollback.

The frozen release projection passes 63 tests: capture/application (21),
legacy workflow (14), activation (13), mirroring (9) and shortcut routing (6).
Shortcut fixtures use a synthetic background keymap; the artist GUI double
click is checked separately. Pure release-tool checks pass 19 cases with one
Windows symlink-permission skip; the previous default build-scope check passes.

Actual CoshaRig integration passes all six isolated stages on the saved artist
checkpoint: read-only capture, independent Blender Action evaluation, FK
restore, IK restore and continued target editing, and Original apply/return.
The selected shoulder/upper-arm/forearm expands to four bones including the
hand, with fingers excluded. 184 native matrices are checked. The independent
oracle's maximum error is 3.58e-7; IK restoration's maximum is 3.20e-5 against a
4e-4 tolerance. Artist and checkpoint file hashes are unchanged during testing.

The local release uses the approved 0.76.4 Frozen R8/Forearm baseline plus eight
Pose files. Seventeen pending non-Pose source differences are excluded; this
is not a Dress release. The complete 148-file manifest is
`approved_release/release_projection.json`, SHA256
`c9dcc6929f1a85c67616be6f60ba82f79561ac670591cb3025888701075771ea`.
Build, deployment and `--check --projection` all use that same manifest.
Both AppData Blender 5.2 and X's add-on copy report zero differences and
`RELEASE_PROJECTION_MATCH`. Package SHA256:
`b90e49d2bc52c806f7f8727349b88f7a8cac48272b229b839e58dadb45bdb828`.

The live Blender 5.2.0 LTS artist session refreshed to 0.77.0 and exposed the
Save Pose button, but the first native GUI creation crashed while selecting
the asset after capture. The crash stack identifies
`file_on_reload_callback_register` writing to address zero, called from
`activate_asset_by_id(..., deferred=True)`. The prior scene checkpoint preserves
the artist's unsaved content. This version's GUI validation and artist save
did not complete; 0.77.1 removes automatic activation and restricts browser
presentation updates to the current window's visible areas. Background passes
above do not establish that the native browser runtime path is safe.

Evidence: `D:\Blender\Projects\Character\X\validation\pose_asset_sync_20261007`.
Local source and deployment only; no commit, push or remote publication.
