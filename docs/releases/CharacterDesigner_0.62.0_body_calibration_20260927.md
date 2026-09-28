# Character Designer 0.62.0 — Body Calibration

Base: `acaa4b61b39a3b8989e8e9be6c7b909c4c2cd032`, branch `main`, initially clean.
Changes are local; no commit or push is authorized/performed. Development source is
`D:\MyRepository\Blender-addons-by-Randy\addons\character_designer`.

## Implemented workflow

`Rig > Body > Body Controls > Setup`: Default/Custom → Show Directions → Preview →
Apply Calibration → Confirm each applicable row. Then choose Controls → Generate.
Arms and Legs can be excluded before generation. Existing controls retain their
required dependencies even when an inclusion checkbox is cleared. Fingers can be
explicitly excluded; its check does not generate finger topology or weights.

Default is armature-local elbows +Y/back, knees -Y/front, palms -Z/down. Custom
exposes named local axes, an optional vector, stability repair and displacement/
length limits. Choosing settings, inspecting a part, or toggling the eye does not
write Rest. Palm selection uses an explicit mesh/face index and reversible arrow;
the user must acknowledge the real palm side. The saved normal uses Basis geometry
and inverse-transpose conversion; Roll never pretends to rotate the mesh palm.

Apply is an undoable transaction. Its independent record owns the earlier Rest;
generated Direct ownership records contain the **calibrated** Rest in both
original/applied fields. Generate, Update, removal and Advanced rebuilding cannot
use these records to restore a pre-calibration skeleton. Old Direct/Stable records
are not silently migrated. Removing their controls and explicitly applying the new
calibration opts into the new workflow.

Stable joints within 5 degrees of the requested direction are exact joint no-ops.
Otherwise the closest direction within that tolerance preserves endpoints and both
lengths. Unstable chains require explicit Custom repair: endpoints remain fixed,
length ratio remains fixed, and the minimum bend defaults to 5 degrees. Limits
default to 10% of chain length for joint displacement and 0.5% per bone for length
change. Over 2% displacement or 0.2% length change requires an additional explicit
acknowledgment. The common runtime stability test is bend height > max(1e-8,
length*1e-4), reach deficit > max(1e-8,length*1e-6). Candidates below Blender's native
scale tolerance fail instead of presenting a reliable direction.

Signatures include algorithm version, part settings, ordered/side-specific mapping,
relevant native geometry/axes/parents and reference evidence. Arms does not depend
on hand Roll; Hands does depend on forearm Rest. Fingers consumes existing saved
definitions and a separately resolved reference after permitted vertex reindexing,
without rewriting the artist's capture or binding. Pose animation and display
settings do not enter the Rest signature. Axis signatures use 1e-5 component
quantization to tolerate Edit/Object float reconstruction; native Rest guards use
2e-6 of bone length for endpoints and 1e-5 for normalized axes. Skin and surface
checks retain the existing stricter matrix/surface contracts.

## Audit and generated helpers

The original Rest writer was `limb_ik._apply_direct_preroll`; its registry recovery
paths are `_restore_edit_rest_states`, `_remove_owned`, `_purge_snapshot_owned`,
and `_restore_owned_snapshot`. New provenance starts at successful Apply, including
before Advanced Build is available. Its builder registers Rest without invoking
Pre-Roll. `sync_wrist_local_axes` is a display/input-reference setting only.

No objects, Empty objects or bones are created by the new Overlay. New Direct
arms retain the existing Target, Pole, hidden `MCH_elbow_pole_aim.L/R` display
reference and `MCH_hand_rotation.L/R` wrist rotation reference. Legs retain Target,
Pole and `MCH_knee_pole_aim.L/R`. These helpers are non-deforming. They are not a
second MCH/ORI solver chain. Whole Body Root and optional body components retain
their existing controls. The overlay reads real control axes, not shape transforms.

## Files

- New: `body_calibration.py`, `body_calibration_math.py`,
  `body_calibration_overlay.py`, `body_calibration_ui.py`.
- Integrated: `__init__.py`, `body_setup.py`, `body_setup_plan.py`, `limb_ik.py`.
- New tests/probes: `test_body_calibration_math.py`,
  `test_body_calibration_blender.py`, `probe_body_calibration_character.py`,
  `probe_body_calibration_gui.py`.

## Actual validation

Runtime: Blender **5.2.0 LTS**, build `fbe6228777e7`. Tests used separate factory
processes, at most one at a time, started only when at least 1 GiB was available.
One additional real-copy attempt omitted factory startup, loaded the old user
installation and failed import before the probe; its failure log is retained.
The successful rerun used factory startup and the canonical new module.
No user Blender/Unity process was stopped or used for testing.

- Python geometry suite: **6 passed** (aligned no-op, 90/180-degree reorientation,
  unequal lengths, scales, explicit repair, conflicting limits, invalid input,
  right-handed frames).
- Blender integration suite: **20 passed**, including actual IK, evaluated mesh
  preservation, full body components, repeated Apply/Generate/Update/Remove/rebuild,
  rollback injection, Edit Mode, save/reopen, dependency invalidation, explicit
  exclusion safety, Advanced protection, corrupt saved mappings, exact selection
  restoration, legacy Direct/Stable, transformed and
  mirrored objects, existing finger references and actual vertex reindexing.
- Continuous synthetic wrist/hand/Pole motion: **81 samples**, largest adjacent
  forearm rotation step **0.0197861 rad** on that path. This is not a guarantee for
  every extreme animation or skin weighting configuration.
- Separate GUI probe: native Apply Undo/Redo, multiple viewports, handler
  register/unregister and save/reopen handler cleanup all passed.
  The probe draws the actual Setup function in a test-only Item panel; production
  still uses its existing Character Designer / Body location. Four screenshots
  show current, candidate, applied and generated states. Additional GUI lifecycle
  results are recorded in the final artifact `gui_report.json`.
- The unchanged legacy limb suite reached **30 passing cases** then stopped at its
  old “schema-5 three-bone contract” assertion. The same failure was reproduced
  from untouched base HEAD in an independent extracted tree. That old expectation
  omits the existing wrist rotation reference. The test was left unchanged; this
  report does not claim the whole historical suite passed.
- AST parsing and `git diff --check` passed. The historical Body Setup tests written
  for immediate unconfirmed Stable generation were not represented as new-workflow
  passes. The new whole-body fixture covers the replacement confirmed Direct flow.

## Real character evidence and remaining acceptance

Only `X_validation_copy.blend` was opened, copied byte-for-byte from `X.blend`.
Source snapshot SHA256: `a60219e748d6b7b31abe1412bf1d89a6f4d577b67b8cc1ff96d8ab61224f0b36`.
The saved character has no generated body controls. Unconfirmed Generate was
correctly refused. Legs Preview/Apply/Confirm succeeded on the copy. Arms Apply
was safely refused because `f_index.01.L` is not at Rest. Real palm orientation
requires the user's selected face; the probe did not guess or silently clear the
finger pose. Original mesh coordinates, Shape Keys and weights were unchanged;
the probe did not save either the input copy or the original file.
The original file changed during the task outside these probe writes: final
observed SHA256 is `a4677efd5618402a7222a7e478cebf9d028f28ced18f15cae04e424447cf8816`.
Validation applies to the earlier copied snapshot, not that later original revision.

**未验证**: a complete new Body rig and full motion/visual skin acceptance on the
user's character; the real palm reference and affected finger Rest state must be
resolved explicitly first. Export still uses the existing exporter and was not
rerun as an animation export acceptance test. No universal twist-free claim is made.

## Review, delivery and rollback

The requested Ultra reviewer first verified a frozen manifest and all 236 listed
files, then independently reported seven issues. Main implementation fixed them:
Advanced provenance, Arms/Hands dependency, existing-component exclusions, resolved
finger references, ordered mapping signatures, live Edit controls, and consistent
stability. A second review found excluded anatomy still being parsed and saved
mappings bypassing complete hierarchy checks; both were fixed. All mapping sources
now verify distinct native Deform bones, side, complete upper/lower/end hierarchy
and live geometry. Active-bone restoration precedes selection restoration.
The third snapshot passed independent hash verification (261 files) and 9 mapping
probes. Final integration evidence is `M5_tests_round12.log` (20 tests passed).
The independent final review response and exact manifest are delivered separately.

Artifacts live under
`D:\Blender\Projects\Character\X\task_artifacts\body_calibration_20260927`.
The final manifest, complete patch (including new files), baseline SHA, source ZIP,
test logs, original/copy hash, screenshots and real-character JSON make the state
reviewable without a commit. Build uses `python tools/build_releases.py`.

Deployment is to X's validation add-ons and a dedicated staging add-ons directory
via `tools/deploy_local.py --module character_designer --addons-dir <staging>
--project-addons D:\Blender\Projects\Character\X\addons`, followed by `--check`.
The running user's Blender installation is not hot-reloaded. The deployment tool
backs up changed destination files; its returned backup path is recorded in the
delivery log (`20260927-125818-14faa4d9`). Both destinations matched all 108
shipped files. Restore those files for deployment rollback, or use the baseline
source snapshot. Use Blender Undo for an unsaved Apply; do not use Remove to undo
calibration. Source rollback must only revert this task's listed files, preserving
any later user work. No commit, push or original `.blend` overwrite was performed.
