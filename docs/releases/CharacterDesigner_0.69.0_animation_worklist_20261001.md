# Character Designer 0.69.0: Animation Worklist

Date: 2026-10-01. Local release only. No Git commit, push, pull request or
published release.

## Behavior

**Character Designer > Animation** connects to one character's Unity workspace.
The Clip Browser reads a bounded metadata index; only explicitly added prepared
clips are imported. Worklist entries share one independently imported editing
rig, with linked read-only Source Actions and independent local Custom Actions.
The original Blender authoring scene and shared Unity motion assets are kept.

Explicit Source/Custom buttons select the correct Object Action slot, preview
range and rig. Existing Action Editors follow that rig. The saved Scene rig also
remains the playback target after reopening, even with a selected mesh, renamed
rig or stale WindowManager target from another scene.

Ordering changes only the worklist. Removing a row retains its Actions. Sync
exports only the selected active Custom through its existing immutable-revision
Link; Unity candidate preview and explicit Apply remain separate operations.

Workspace, model, optional Avatar/base Controller provenance, clip GUID and exact
signed 64-bit local ID are checked at import/return boundaries. Apply-related
target dependency hashes and current-override display metadata are informational.
Existing matching single-Action edits can be adopted; stale revisions stop
without replacing edits. A failed Add restores the original Action/NLA/pose,
armature data and context, and removes only its unpublished Source cache.

See [Animation Worklist](../animation_worklist.md) for operation, external-cache
backup requirements and the retained single-Action workflow.

## Validation completed

- All 52 pure metadata/backend/UI/coordinator checks pass, including the final
  compact row layout and hidden legacy reorder entry.
- Thirteen isolated native Blender tests pass, including Source immutability,
  shared-rig Action ownership, exact slots, actual Action Editor context,
  legacy adoption, save/reopen and injected failure rollback. The final
  post-activation failure case also restores the original WindowManager target,
  scene and selected authoring rig before removing only the failed import's IDs.
- The final cold Action Export UI native regression passes, including repeated
  registration, integrity verification and unregister/timer cleanup.
- Real current Cosha-model Walk_N and Idle were imported into one Blender rig.
  Both initial Source/Custom curve comparisons have zero numeric difference.
  Independent left/right forearm edits leave the other Custom and both loop
  endpoints unchanged.
- The real editing file survives save/reopen with identical identities, order,
  slots, curves, FPS, rest skeleton and evaluated poses (maximum matrix change
  zero). Both Custom Actions return through production Sync as revision 1 with
  matching FBX/metadata hashes. Workspace, source packets, model and Source
  cache files remain unchanged.
- Actual Source/Custom mouse clicks show the corresponding native Action Editor
  keyframes and Source disables Sync. Actual held mouse drags in the tall handle
  invoke Blender's LEFTMOUSE RELEASE event and move Idle down and back up by one
  row, preserving its selected/active UUID, Custom Action and slot without errors.
  The old per-row grip has been removed; its operator remains internal.
- Unity accepted both genuine revision-1 returns, rendered nonempty candidate
  previews and applied them separately to a private Cosha test prefab. Repeated
  sampling has maximum matrix error zero. Applying the second candidate retains
  the first replacement and the other 27 Controller slots. Refresh after each
  Apply retains the workspace path/UUID, all 29 original clip GUIDs and exact
  signed 64-bit IDs, model, Avatar, base Controller and both prepared Links.
- The canonical bundled Unity helper's complete 10 Editor and 3 Runtime sources
  compile against the project's Unity SDK in isolated artifact output: zero
  errors and 18 existing CS0649 deserialization/test-hook warnings. Its preview
  changes avoid allocating export frames during interactive sampling and reuse
  the RenderTexture while paused, with pose, size, camera, Undo and project
  invalidation. The standalone helper keeps its single-Action scope.

Private Blender evidence is retained under
`D:\Blender\Projects\Character\X\task_artifacts\animation_worklist_20261001`.
Those model, packet, editing-scene and Source-cache assets are not distributed
in the add-on archive.

The real-pair record is `actual_pair/prep_report.json`. Its original saved
`example.blend` had SHA-256
`666d55dd6c2cfa5f7457627ffed37533b6d2f9f690c9bcfeaf2b6f1f66e0bea1`,
which remains preserved in `actual_pair/example.blend1`. During normal QA-window
quit, user input preceded the automation's discard click; the log records a save
and normal exit. The current private `example.blend` is preserved at SHA-256
`e5015760b27f5eb6d189b163cfde60c39ceb6cb340c1da7aef91a94b26f979c7`.
Walk_N and Idle each published revision 1. Their FBX SHA-256 values are,
respectively, `de8fee5efc91bef175e8395be68805690b6c62d16dd576eccbf4dbde2fded646`
and `6e48dcff68503a53f261202f4fc278b664e8c2983dd534f538e7a874f24cb86b`.

Unity double-return evidence:
`D:\Unity Projects\RandomRealm2\Logs\CharacterTuning\AW\Returns\27b7e69c\pair-return-verification.json`.
Both preview images are stored beside that report. Production assets, current
Scene identity/file/dirty state and selection are preserved during this run.
The report separately records the user's intervening Adventure Scene save:
`preparedSourceFilesPreserved=false` refers to that historical Scene-file
difference; `preparedAnimationSourcesPreserved=true` and current-run protection
checks pass. The task does not undo that user save or apply candidates to the
production character.

Canonical helper compile evidence:
`C:\Users\Randy\.codex\visualizations\2026\10\01\01a0f5ba-b79b-7182-b151-fc9347d65e86\animation-workspace\evidence\canonical-helper-build.log`.
Compilation outputs stay in the task's artifact directory; the full bundled
helper was not copied into the Unity project as part of this integration.

Final native/UI evidence: `native_worklist_final.log`,
`native_export_ui_final.log`, `gui_before_final_drag.json`,
`gui_after_final_drag_down.json`, `gui_after_final_drag_up.json` and
`gui_final.log` in the private Blender evidence directory above. Only the
task-owned QA window was closed; the user's X, Builder6 and Unity windows remain.

## Release status

Source and local deployment version: **0.69.0**.

- `dist/character_designer-0.69.0.zip`: 124 shipped files, SHA-256
  `3956ad897dd079011a9287f4d128f1a80c169c21c3bda1f558b746ba44ba3273`.
- Module-only deployment updated 9 files in Blender 5.2's installed add-on and
  X's `addons/character_designer` validation copy. Final `deploy_local.py
  --module character_designer --project-addons D:\Blender\Projects\Character\X\addons
  --check` reports 124 files and zero differences at both destinations.
- Previous files and deployment record:
  `C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20261001-193242-1d50913f`.

Local file deployment does not reload an already running Blender session. Save
work and reopen normally; the user's X, Builder6 and Unity scenes are not
restarted by this task. No Git operations or production-character Apply were
performed.
