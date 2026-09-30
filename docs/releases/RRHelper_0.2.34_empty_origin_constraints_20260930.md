# RR Helper 0.2.34 — Keep constrained parts in place when placing an Empty

Modeling > Origin > Apply to Selection now accepts ordinary transform constraints
on parts, including paired doors with Copy Rotation and Limit Rotation. It moves
the existing Empty and compensates its direct children's parent-inverse matrices.
It does not dissolve/recreate the assembly, bake constraints, or change local
animation channels, object IDs, export IDs, modifiers, or constraint settings.

Constraint targets and custom spaces are checked through their parent and
constraint dependencies. References to moving Empties, driven dependencies and
unsupported constraint types are rejected. Constraints/drivers on the moving
Empty itself remain unsupported. After applying, world matrices and evaluated
mesh coordinates/topology are compared; failure restores locations and parent
inverses for the entire operation. Evaluated checks cover this assembly and any
selected target at the current frame, not arbitrary objects elsewhere in a scene.

Validation on Blender 5.2.0 LTS:

- 48 origin regression tests passed, including world/local constraint spaces,
  two animation frames, custom-space dependencies, Undo, and a real indirect
  Mirror dependency that changes geometry and must roll back.
- 63 add-on registration/reload checks passed.
- An isolated copy of Builder6's Hub_Elevator_A moved from (200, 31, 8.1) to the
  current cursor at (200, 31, 0). All six parts retained their world matrices and
  evaluated geometry across five door angles (-0.2, 0, 0.3, 0.6, 0.9 radians).
  All four door constraints, local channels and export identities were retained.

The original live scene was backed up before the change to
`D:/Blender/Projects/Build/Recovery/EmptyOriginConstraints_20260930/Builder6_before_empty_origin.blend`.
Validation scripts, snapshots and logs are kept beside that local backup and are
not part of the distributable add-on. Package: `dist/rr_helper-0.2.34.zip`.

The live Blender session was refreshed to 0.2.34 and the same operation was
successfully applied to Hub_Elevator_A. Its origin now matches the cursor at
(200, 31, 0); all other scene world matrices, all six evaluated part meshes,
object identities and four constraint instances were verified unchanged.
Undo is available. The working Builder6.blend was left unsaved so the user can
review the result before saving. Evidence: `live_result.json` beside the backup.

Deployment checks passed with zero differing files for Blender's addons_core,
the user add-ons directory, and RandomRealm2's AssetPipeline add-on copy.

Local changes only; no Git commit, push, or release publication.
