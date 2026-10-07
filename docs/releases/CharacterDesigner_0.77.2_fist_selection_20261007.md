# Character Designer 0.77.2 — Apply Fist with a hand controller selected

The legacy **Fist** Pose contains 360 channels on 15 left-hand native fingers.
The current CoshaRig uses Quaternion rotation and no constraints on those
fingers, so the ordinary native application is appropriate. Selecting the hand
IK controller previously excluded every finger from Blender's native Pose
selection filter and produced no visible change. Managed **Arm Flat** already
used the synchronized application service and was unaffected.

For an unconstrained legacy native Pose, the wrapper now temporarily selects
the Pose's destinations when an existing selection has no intersection with
them. It restores the artist's selection and active bone in `finally`.
Intentional partial finger selections, empty-selection native application,
mirrored authored-side subsets and selected center bones retain their behavior.
The missing-destination guard runs before selection changes. No historical Rest
metadata is fabricated for a legacy Pose.

The frozen release passes 18 native-selection tests and 13 existing activation
tests in Blender 5.2.0 LTS, build `fbe6228777e7`. These use real armature selection
state and a spy for the final native Asset Browser operator; they establish
selection and routing contracts, rather than actual GUI Action evaluation.

Release input is the hash-locked approved 0.77.1 package plus three files:
`control_pose_assets.py`, the version in `__init__.py`, and package usage in
`README.md`. Pending Dress work remains excluded. Projection SHA256:
`92f7176029ee8033d60942fabc296121c5bd452a2d75c844bd9fb85f94bf0273`.
Build, Blender 5.2 AppData deployment and the X validation copy all use that
projection. Both deployed copies match 148 files with zero differences.
Archive SHA256:
`c55c5860298e6ef20fe3df6a0db178c2b4c1817970027348e4f6a4eaa8b708e3`.

Pose retention uses one local Action asset and one external `.asset.blend` for
each Pose. They are independent copies; edits do not automatically propagate.
The external Fist file remains byte-identical. External Arm Flat retains its
Pose channels, compatibility metadata and custom thumbnail; only its external
catalog is assigned to Pose Library. Cross-scene application requires compatible
native bone structure and Rest where the saved managed Pose requires it.

Evidence: `D:\Blender\Projects\Character\X\validation\fist_current_file_20261007`.
Local changes and deployment only; no commit, push or remote publication.

The final artist integration used an actual Current File **Fist** thumbnail
double-click while `CTRL_hand_IK.L` was selected and active, at frame 39 in Pose
Mode. The native operator finished; all 15 left-hand fingers changed both raw
channels and evaluated matrices. Saved-channel error and evaluated matrix error
outside the fingers were both zero. Selection, active bone, assigned animation,
native Rest, IK/FK settings, controller channels and existing assets were
preserved. This is ordinary left-hand GUI application; actual mirrored
evaluation and Auto Key enabled were not part of this check.

The running registered operator received the changed `_apply_native` helper
without a full add-on reload. Installed 0.77.2 files match the frozen projection;
the next normal add-on reload uses those files. The one-use validation observer
was removed after the actual double-click.

The final scene save used native Ctrl+S: Blender displayed `Saved "X.blend"`
and the title had no unsaved marker. The save-script API result was not recorded,
so this conclusion uses the final GUI confirmation and disk verification.
The artist file at 2026-10-07 19:51:16 +08:00 contains 32,415,283 bytes, SHA256
`994c1edd4c9bfc338726e9dfa23ee5d76b3b074da2523e860bbf8d3a9e044a95`.
An independent Blender 5.2 factory-startup process loaded only its two Action
assets and verified asset marks, fake users, complete channels, compatibility
properties, catalog details and both custom previews against the protected
external evidence. It did not open or write the artist scene; both external
files and the artist file remained byte-identical during verification.

Final evidence: `current_imported.json`, `live_fist_double_click.json`,
`saved_asset_verification.json` and `current_saved.json`. The pre-crash checkpoint
and a separate checkpoint of the user's newly edited reopened scene remain
available. The latter scene was used for import, validation and final save;
the pre-crash checkpoint was not loaded over the user's subsequent edits.
