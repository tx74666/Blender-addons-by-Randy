# Character Designer 0.67.0 — Quick Bind object hierarchy

Surface Transfer / Automatic Weights now parent a successfully bound target mesh
to Main Rig. Previously, weights and the Armature modifier were sufficient for
bone deformation but did not create an Outliner parent, so Clothes and Stocking
remained at scene level.

Parenting preserves the object's local and delta channels. The new parent inverse
compensates the rig transform and any prior Object parent; the evaluated world
transform is verified before committing. An existing same-rig Object parent keeps
its inverse. Cycles, unsupported parent types, and active object constraints on a
mesh needing a parent change are rejected before weight calculation.

Parent history is stored independently of the original weight backup. Rebinding
never rewrites the first weight backup. Previous Weights restores a tool-owned
parent change while retaining current world placement; a missing old parent,
changed artist parent or unrepresentable transform refuses restoration and keeps
the record. Existing Remove / Restore Binding disconnects and reconnects the rig
parent with current weights. Bind and Previous Weights roll back both parenting
and weight state on failure. Existing animation channels are not re-keyed;
reparenting intentionally changes future parent inheritance and is not animation
retargeting.

Validation in serial Blender 5.2 processes: 12 new parenting cases, 9 existing
solver/preservation cases, 3 topology-rebind cases and 8 remove/restore cases all
passed. Coverage includes rotated/nonuniform/negative scale, old-parent shear,
delta channels, actual transfer and Auto solvers, evaluated mesh following a rig
translation once, injected partial failures, save/reopen, first-backup byte
preservation and operator Undo/Redo. An isolated 0.66.1 hotload followed by the
parenting suite also passed.

Live diagnostic: Clothes and Stocking each already had a CoshaRig Armature
modifier and Quick Bind backup, with no parent or object constraints. Their
hierarchy can be repaired without running either weight solver. Evidence is in
`D:\Blender\Projects\Character\X\task_artifacts\quick_bind_parent_20260928`.
Release archive and both installed copies match all 115 shipped files. The
binding service was hot-reloaded with artist data/context preservation checks.
Then Clothes and Stocking were attached to CoshaRig in the running X scene without
calling either weight solver. Before/after checks confirmed unchanged base mesh,
vertex groups, shape keys, modifiers, original weight backups, world transforms,
active mode/selection/frame and rig pose. A separate readback confirmed both
parents. The scene's on-disk blend file was not saved or overwritten.
Local source/deployment only; no commit or push.
