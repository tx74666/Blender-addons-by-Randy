# Composition Pose selection compatibility (2026-10-11)

The actual isolated n7 run stopped in `animation_composition._prior` because
Blender 5.2 no longer exposes `Bone.select`. It did not launch a B Sync worker.
The n7 runtime and evidence remain unchanged.

The official [Blender 5.0 Python API release notes](https://developer.blender.org/docs/release_notes/5.0/python_api/#animation-rigging)
document that Pose/Object selection belongs to `PoseBone.select` and that
`Bone.select` was removed. The [PoseBone API](https://docs.blender.org/api/5.2/bpy.types.PoseBone.html)
exposes the replacement property. Canonical `control_pose_assets._apply_native`
already uses PoseBone when that property exists, with legacy Bone fallback.

The narrow Composition change adds `_pose_selection_owner(pb)` and uses it in
prior-state capture, scope selection restriction and prior-state restoration.
It prefers the per-object PoseBone property. Legacy fallback reads the actual
Bone property before returning its owner; an unknown API raises instead of
assuming deselection or creating an ineffective Python attribute. Active bone
and exact selection flags are restored after mode/object recovery, with active
assignment first, so an active but deselected bone remains deselected.

Only `animation_composition.py` changes at runtime. Full reference receipt keys,
native Action/cache authentication and receipt transaction rollback are untouched.
Separate tool owners must use the same helper for their Pose selection operations.

Validation is source-only: eight focused tests exercise actual canonical prior
capture, modern/legacy scope restriction, mode/lock/property restoration, active
but deselected and empty selections, unsupported API failure and actual Enter
failure recovery after selection restriction. Modern fixtures have no
`Bone.select`; active assignment and mode recovery deliberately disturb flags
before restoration. Syntax compilation passes. No native Blender, worker,
projection, deployment, installation, artist operation or commit was performed.
Native acceptance remains the next root-owned step after a new pinned candidate.
