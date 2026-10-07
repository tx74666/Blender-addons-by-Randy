# Character Designer 0.77.1 — Save Pose and synchronized application

Select native Body bones or their corresponding public controls, choose
**Save Pose**, name it and optionally enable **Include Fingers**. One Action
asset is saved in **Current File**. Save the `.blend` to keep it. Double-click
the asset to apply through Character Designer; Shift-double-click mirrors it.

Capture records the evaluated native pose and authoring Rest. Selected limb
bones and public targets/Poles resolve to the complete chain. Application
matches current and dormant controls, preserves the current IK/FK mode and
updates the Original return checkpoint. Compatible controller rebuilds use
the same asset; incompatible native Rest is rejected. Animation, selection
and editing state are preserved, with complete transaction rollback on error.

0.77.1 repairs a native GUI crash observed in 0.77.0 after successful capture.
The previous browser helper visited hidden workspace screens and called
deferred asset activation. Blender 5.2's callback registration writes through
the browser runtime without a null check, consistent with the observed
zero-address write. The helper now updates only the current window's visible
Asset Browsers and never calls automatic asset activation. No window or no
visible browser is a harmless no-op. The captured asset remains valid if
presentation parameters cannot be updated.

Primary source and native crash evidence:
[Blender 5.2 callback implementation](https://raw.githubusercontent.com/blender/blender/fbe6228777e7/source/blender/editors/space_file/space_file.cc)
and `validation/pose_asset_sync_20261007/save_pose_browser_context.crash.txt`.

The unchanged pose engine passed 63 frozen-release tests and all six actual
CoshaRig checkpoint integration stages in 0.77.0; its native GUI attempt did
not complete. The frozen 0.77.1 capture suite passes 27 cases, including six
browser boundary checks. The latter also pass in a pure-Python runner without
importing Blender. These mock checks alone do not establish native GUI safety.

Native GUI verification on the recovered CoshaRig artist scene also passes:
the real VIEW3D Save Pose button creates one internal **手臂平舉** asset with
four bones and 96 channels, fingers excluded. Four hidden workspace browsers
retain their prior parameters; the visible browser switches to Current File.
Capture leaves all 184 native matrices exactly unchanged. Actual double-click
routes through the synchronized Character Designer application, preserves FK,
and differs from the preceding visible pose by at most 6.56e-7. Frame 39,
active/selected bones, assigned Action/slot/keys, native Rest and Original state
are preserved. The temporary validation observer is removed and the artist's
Shader Editor restored before the native save.

Blender reports `FINISHED` for `D:\Blender\Projects\Character\X\X.blend`;
the final title has no unsaved marker. File size is 32,288,571 bytes, saved at
2026-10-07 05:49:54 Asia/Shanghai, SHA256
`e0b30f73fc2f8ecc91ae418602fd3bec279b19071be3529b3d109ed55ec40dd7`.
Crash recovery used the verified pre-operation checkpoint, preserving the
artist's previously unsaved content. The preceding disk file and checkpoint
remain separately backed up with hashes. An image thumbnail was unavailable
in the camera-less scene; the named Pose is saved and functional.

The release uses approved 0.76.4 Frozen R8/Forearm bytes plus eight Pose files.
Seventeen pending Dress and other non-Pose differences remain excluded. Build,
deployment and deployment check use `approved_release_0771/release_projection.json`,
SHA256 `96cb0675d2fe72377fbfa4ca4e717bf47663640ba32e79624f306755f55c9f80`.
AppData Blender 5.2 and the X add-on copy each match all 148 files, with zero
differences and `RELEASE_PROJECTION_MATCH`. The 0.77.1 package SHA256 is
`4c0861a2db9f847cbcf0075b183175c13365f2fef1af3278950fe0c624f2eda5`.
The original 0.77.0 projection, archive, receipt and failed GUI evidence remain
intact; the new proof is stored under `approved_release_0771`.

Evidence: `D:\Blender\Projects\Character\X\validation\pose_asset_sync_20261007`.
Local source and deployment only; no commit, push or remote publication.
