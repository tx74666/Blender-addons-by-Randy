# Character Designer 0.61.40 — persistent joint monitoring

## Fixed

`finger_workflow_ui._dirty` previously called `hide` on any relevant dependency
geometry/transform notification unless a slider refresh was already pending.
Thus ordinary updates or rig edits could close the eye and cancel future slider
feedback. Bank operations, bend flip/preview and generic failure handlers also
closed/replaced the ring overlay.

The object-scoped `preview_enabled` setting now records the user's intent.
Only explicit eye-close and Release disable monitoring. Other operations
schedule validation, preserving drawable references while they update. Source
failures use amber, explicitly non-current comparison references plus a reason;
they do not weaken source fingerprints or permit overwriting intervening edits.
Undo/load clear invalid RNA/GPU caches but retain the setting, and post handlers
request revalidation. Ring and Bend previews can coexist. Basic Setup axes are
still independent. No geometry, bone, weight or Shape Key writes were added.

## Actual verification

- `test_finger_preview_eye_blender.py`: **11 passed**, including rig translation,
  bone roll edits, ordinary geometry notifications, selection, hidden object,
  prepare/reprepare, flip, failed loop/apply, source edit/recovery, weight warning,
  individual slider updates, manual Hide and event-loop cache resumption.
- `test_finger_workflow_blender.py`: **12 passed**, including pair rollback,
  topology/weight idempotence, locked/impossible budgets, persistence, transforms,
  missing groups, unbound/missing opposite cases and source conflict protection.
- `test_finger_drag_performance_blender.py`: first **5 checks passed**. The sixth
  failed its *old* closed-eye assertion for modified weights/Shape Keys; the
  assertion now requires a stale-labelled reference and the same source-error
  message. A subsequent complete rerun was not started (memory gate below).
- No new native GUI or live-user-scene test in this release. No new input/display
  timing claim. Existing GUI scripts are not claimed to pass this changed intent.
- No user blend file was saved or overwritten. Isolated workflow test saved and
  reopened only its own temporary synthetic fixture.

Available RAM fluctuated down below 2 GB. The next serial test group was blocked
before Blender launch; there is no background test left running. The complete
cache/multiselect/GUI follow-up remains to be run when resources permit.

## Release isolation

Unvalidated Unity animation-library development has been retained in
`.codex-drafts/animation-library/` and is **not shipped** in this finger hotfix.
Existing animation runtime files were restored from the verified 0.61.39 package.
No RR Helper or Unity Editor deployment is part of this release. Local package
and Blender/X deployment only; no git commit or push.
