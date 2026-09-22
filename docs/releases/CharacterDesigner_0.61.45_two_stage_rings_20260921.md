# Character Designer 0.61.45 — Two-stage central loops and new supports

The .44 implementation reused support loops as well as central joint loops. The
requested behavior is narrower: only central loops move; both supports are new.

## Workflow

1. Prepare Joints and adjust the center sliders. Move Centers + Bones moves safe
   existing central loops and the enabled bone junctions together. Vertex count
   stays unchanged; no local joint weight assignment occurs in this step. Moved
   vertices still interpolate their existing attributes and weights from the
   immutable source surface, preserving authored data along that surface.
2. Add Support Rings + Apply adds two new loops around each confirmed center,
   keeps the center identities and bones fixed, then applies enabled local weights.
   Fill Empty Middle adds one ordinary ring only where the interval has none.

Each step has its own atomic mesh/metadata transaction; the first also restores
rest bones on failure. Center selection is independent of spacing. The second
step refuses unconfirmed center changes, pins the source row identities, and
never moves an ordinary row to make it a support. Coincident/crossing supports
require spacing adjustment. After finalization, undo the second step to move
centers again; width/weight updates remain available without adding duplicates.

Main-panel Joint number and Use Selected Loop controls are removed. Advanced
per-joint widths/ratios remain accessible by Shift-clicking Prepare Joints.
Phase readiness is read from saved metadata in panel drawing without mesh scans.
Bone Roll, four-finger alignment and curved Thumb Relax remain separate actions.

## Validation and deployment

All **80 focused cases passed** in Blender 5.2: ring allocation 14, two-stage
workflow 12, existing workflow 12, joint defaults 6, preview eye 11, confirmation
8, combined preview 6, idle monitor 5 and packed proof/drag 6. Tests ran serially
on small synthetic fixtures; the live X model was not used for Apply or Undo.
Logs are under `D:\Blender\Projects\Character\X\outputs\two_stage_20260921`.
The 11 non-native stage cases are recorded in `two_stage_non_native.log`; the
native operator Undo/Redo case is recorded in `native_undo.log`.

Checks cover exact vertex counts, new support identities, center-only source
interpolation, no local weight assignment in Step 1, no bone writes in Step 2,
phase rollback, save/reopen, inactive-finger draft isolation, repeat updates,
empty-middle behavior and the removed main-panel row. Real Blender operators
with native undo enabled restore Step 2 to CENTERS, Step 1 to the prepared source
and unchanged original rig, then redo Step 1 and accept repositioning/refinishing.
Only the initial Undo baseline is pushed manually. The background context keeps
the real window/screen and omits the uninitialized viewport area to avoid
Blender's HUD initialization crash; no Undo behavior or assertions are mocked.

An additional allocation check compared the actual center matcher against
brute-force ordered assignments in 1,000 random small cases and passed.
This release did not repeat .44's real-asset integration run.

Built `dist/character_designer-0.61.45.zip` with 106 files. Blender 5.2's installed
add-on and X's validation copy each received four changed runtime files.
`deploy_local.py --check` reported zero differences at both destinations.
Previous files are backed up at
`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260921-075731-0adb926a`.

The existing X Blender window was refreshed. The main-panel Joint selector /
Use Selected Loop row is absent; Move Centers + Bones is available and Add
Support Rings + Apply is correctly disabled until Step 1. The current Middle
preview reports four moved centers and zero new loops. Mesh statistics remain
3,418 vertices / 6,748 edges / 3,344 faces, with no selection, in Edit Mode.
No step was applied to the live model and no blend file was saved.

Changes remain local and uncommitted; no GitHub push was made.
