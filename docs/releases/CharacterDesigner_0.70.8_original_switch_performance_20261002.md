# Character Designer 0.70.8 — Original / Controls performance

Entering Original previously evaluated the entire scene after every native bone
assignment. On the saved Cosha character (184 native bones, 236 total), one
switch performed 163 pose updates. Native local bases are now computed from the
already captured desired parent frames, assigned as a batch, and evaluated once
per pass. The same character needs three pose updates for Original and two for
an unedited return to Controls. Final pose verification and transactional
rollback remain unchanged.

Original computes the final bone and collection visibility once instead of
hiding then unhiding each target. Display and channel setters skip identical
values. Entering while the correct rig is already in Pose Mode retains that mode
and bone selection. The duplicate display evaluation is removed. Returning
invalidates only the current rig's collection cache and scopes the collection
membership refresh to that rig; frame-change handlers keep their scene-wide
default behavior.

Only the requested Original or Controls display is shown. Deformation bones,
control relationships and helper bones remain part of the existing armatures;
they are not deleted or replaced. Native posing, authored-pose matching, saved
IK/FK modes, weights and Shape Key data keep their existing semantics.

## Measurements

Serial background Blender 5.2 profiling used the saved X.blend without saving or
modifying that file. Mesh coordinates, Shape Keys, weights, vertex groups,
edges/faces, skeleton Rest matrices, parent relationships, pose channels and
bone display were hashed before and after each no-edit roundtrip; all matched.

| Switch | 0.70.7 baseline | Optimized candidate |
| --- | --- | --- |
| Original pose updates | 163 each | 3 each |
| Controls pose updates | 3 each | 2 each |
| Original, first cycle from Object Mode | 5.001 s | 2.083 s |
| Original, next cycle in Pose Mode | 4.714 s | 0.743 s |
| Controls, two cycles | 0.616 / 0.569 s | 0.607 / 0.901 s |

The timings include profiling overhead and vary with system activity. The
consistent improvement is removal of per-bone scene evaluations; an edited
Original pose may still require the existing control-matching solver on return.
Controls timings do not demonstrate a consistent latency improvement.

Evidence in the X validation directory:

- `original_switch_0707_baseline.json` and `.log`.
- `original_switch_0708_optimized.json` and `.log`. The optimized runtime was
  measured before the metadata version bump, so this report retains 0.70.7 in
  its version field. The final package metadata is 0.70.8.
- `probe_original_switch_performance.py` is the read-only profiling harness.

## Verification and deployment

- 13 Original-mode tests passed, including authored poses, save/reopen, Undo,
  refresh, selected groups and complete failure rollback.
- Added a >200-native-bone connected-chain regression with NONE/FIX_SHEAR/FULL
  scale inheritance, nonuniform scaling, generated target movement, preserved
  pose and a bounded update count.
- Added a Pose Mode regression that rejects any mode switch and verifies active
  bone/selection through Original, repeated Original and Controls.
- 11 bone-display tests and the bone-collection suite passed.
- Built and verified `character_designer-0.70.8.zip` (130 files).
- Installed Blender 5.2 and X validation copies match canonical source;
  deployment `--check` reports zero differing files for both destinations.
- Refreshed the running X scene, returned from its current Original session to
  Controls, then entered Original again. Both succeeded with the pose retained;
  only the current mode button was highlighted and the expected bone display
  returned. No artist pose edits were made during the check.
- Saved `D:\Blender\Projects\Character\X\X.blend` in its original working
  mode, Original, at 20:15:12 Asia/Shanghai on 2026-10-02. Blender confirmed
  Saved "X.blend"; the file is 32,207,054 bytes.

Changes and deployment are local. No Git commit, push, PR or publication.
