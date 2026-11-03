# Character Designer 0.77.156 — standard wrist shape and frame 14

The FK wrist now uses the same canonical curved, three-dimensional HAND geometry
as the existing IK wrist. The previous plain FK circle was the wrong shape for
this character. Upper and lower FK arm bones retain their native appearance;
the FK wrist still operates the native hand bone and its existing rotation and
keying channels. IK retains its target and source shape.

`Rig → Body → Bone Setup` exposes `All Arms / Legs · Current Frame` with the
primary IK/FK buttons. These switch both arms and both legs together at the
current frame using the existing pose-matching/keying service. `Advanced`
retains individual limb switches, and mixed-mode diagnostics identify each limb.
The Body Part selector does not narrow the global buttons. Hair and Skirt have
their own controls.

The user's frame 14 was repaired to all four limbs in IK and saved normally to
`D:/Blender/Projects/Character/X/X.blend`. The current Action is
`Cosha · Walk_N · Custom.002 · Current Rig.003`; the original Action remains
available unchanged. This is a current-frame repair, not conversion of the whole
animation: existing frame 13/15 state is retained. Native matching error at frame
14 was at most 2.88e-6 position, 1.47e-5 rotation and 4.00e-5 scale. Independent
private IK→FK→IK checks passed the existing preservation bounds.

## Implementation and delivery

Complete156 extends approved complete154: 257 files, three changed files and
one new module; 253 files are identical. Selected changes are `__init__.py`,
`limb_fk_visuals.py`, `limb_ik.py` and new `limb_fk_wrist_visuals.py`. The unrelated
paused controller-authoring candidate was excluded; only its reviewed global
switch scope/diagnostic drawing function was adopted for this request.

The HAND planner admits owned canonical/fitted IK display transforms, aligns
them to the native hand frame, and avoids stale IK target positions in FK.
Migration accepts only validated owned legacy circles. It updates existing
vertex coordinates while preserving object/mesh IDs, topology, edge order,
custom attributes, materials and selection. Artist-edited/shared shapes are
refused or skipped under the scoped guards. Original view protections remain.

Projection: `D:/Blender/Projects/Character/X/validation/fk_wrist_standard_20261103/stage156_01/projection.json`
SHA256: `b3e00548bad25aa1050850a3c2b1a36f1214ecd70885cc44f810acf0e58a1883`.
Official ZIP: `dist/character_designer-0.77.156.zip`
SHA256: `5d99c3a52ce37df0e4c9f4ba02f74a7a1563413d985096fd04fdb07305676d7b`.
Official build, ZIP CRC/exact257 and Blender5.2/X dual deployment/check passed.

## Validation and limits

Pure checks: existing FK visuals13, HAND planner8, global switch scope3. Native
HAND checks8, narrow update/migration helpers10, mode helper3, private current
scene snapshot, actual-scene frame14 preservation and free switching checks
passed. The fresh private snapshot preserves authored arrays; expected generated
Forearm target handoff is checked separately from unchanged calibration fields.
Independent review found no additional functional blocker.

Artist36008 received three scoped code assignments and one new plain module;
both existing wrist resources migrated in place, and frame14 was keyed through
the existing global service. No Body rebuild or preference save was performed.
The scoped admission initially observed metadata154. A later fresh read observed
metadata156, the three target functions already156, and the new leaf absent
under normal lazy loading. Refresh time/initiator is unknown: no full-runtime
identity claim is made across that gap, and this task did not request full reload.

The native save returned FINISHED and retained the target state. Its first
receipt remains failed because the immediate dirty-flag assertion failed after
save. A later post observer incorrectly required the lazily loaded leaf to remain
imported; that failed receipt also remains. The final read observed dirty=true;
this does not negate the earlier save or prove later unsaved state is on disk.
The saved file is checked independently in a private Blender, without reopening
the artist or repeating its save. Exact final evidence is linked by
`D:/Blender/Projects/Character/X/validation/fk_wrist_standard_20261103/acceptance156_01.json`.

All earlier fixture/gate failures and their bounded derivatives remain indexed
by the component acceptance records; no old failure is relabeled passed. No
artist physical wrist-drag/Undo lifecycle, whole-animation conversion, complete
save/reopen lifecycle, physics dynamics, FPS or performance claim is made.
No Unity operation, Git commit/push, application restart or preference save.
