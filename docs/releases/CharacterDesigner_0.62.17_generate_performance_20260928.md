# Character Designer 0.62.17 — Advanced presets and Generate performance

Default / Custom and the associated Custom calibration fields now live inside
the existing Advanced section before generation. The main calibration area no
longer shows them. Saved choices, parameter values, preview signatures and
calibration behavior remain unchanged; no second Advanced section was added.

## Measured changes

Profiled the user's current CoshaRig in a dedicated saved copy, with Blender
5.2.0 LTS, one background thread. The copy initially had generated controls:
108 bones and 32,536 base vertices bound to this rig. Each run measures Update,
then Remove, then fresh Generate of all ten supported components. It never
saves over the input or operates on the original character.

The baseline is the exact 0.62.16 archive extracted into baseline_addons.
Wall times below are two runs without cProfile. They time the service call,
excluding process startup, .blend loading, viewport redraw and GUI undo costs;
other applications remained open, so these are local observations, not a
guaranteed click-to-ready time.

| Operation | 0.62.16 seconds | 0.62.17 seconds | Mean reduction |
| --- | --- | --- | --- |
| Generate after Remove | 10.640 / 10.337 | 8.754 / 7.813 | 21.0% |
| Update existing controls | 1.346 / 1.476 | 0.599 / 0.610 | 57.2% |
| Warm Update after Generate | 1.073 / 1.132 | 0.322 / 0.298 | 71.9% |

Changes selected from profiling:

- Calibrated shallow-limb reach search evaluates each exact float32 solver
  position once per search. All three levels, bounds, strict score comparison
  and best target/pole restoration remain intact. No new quantization or
  tolerance was introduced.
- Already calibrated IK trials no longer assign the same IK value and refresh
  the full dependency graph again immediately after the solver-position update.
  Missing properties, FK/blend states and legacy matching retain the old path.
- Transaction cleanup releases only this checkpoint's valid local zero-user
  mesh backups in one batch. No global orphan cleanup or artist data deletion.
- Removed an unused skin snapshot from the calibrated limb build branch.

On the real copy, IK/FK graph refresh calls fell from 412 to 246. Profiling
still shows time spent in precision matching and shoe/foot widget fitting.
Surface, skin, Rest checks and transaction snapshots/rollback are retained.

## Correctness evidence

For the real copy, every final native/generated pose matrix and local pose
basis exactly matched 0.62.16 (maximum element difference 0.0) in all three
optimized runs. Native Rest, mesh coordinates, weights and Shape Keys were
preserved. Generate's maximum evaluated-vertex movement relative to its
pre-generation surface was 0.0000139666 rig units in both versions, unchanged
by the optimization. The optimization does not claim absolute zero solver
residual. Each input copy hash remained unchanged.

Actual Blender checks passed:

- 2 new search differential/guard tests. Frozen reference functions were
  independently verified against 0.62.16. Repeated reference candidates had
  identical scores and control bases; optimized candidates cover the same set
  and yield exactly the same final matrices, constraint mute and IK state.
  The synthetic parented fixture drops 54 trials to 37 and 396 updates to 238.
- 13 Arms/wrist/UI tests, including collapsed/expanded Advanced and saved values.
- 30 calibration/build/update/remove/rollback tests.
- 10 transaction tests, including edited widgets and native ID restoration,
  batch scope, adopted backups, library data, invalid references and retry.
- 11 legacy IK/FK integration checks, including foot Auto/Manual and animation.
- 7 removal/rollback checks.
- Body Setup UI end-to-end script.
- 4 main UI routing checks.

Known baseline failures are not counted as passes: the older
test_body_setup_blender.py suite reports 7 errors and 1 failure (2 tests pass)
on both this version and the exact 0.62.16 snapshot. Its uncalibrated/posed first
build fixtures are blocked by the existing calibration gate. Production gates
and test expectations were not relaxed to hide these failures. See
baseline_body_setup_tests.log and test_body_setup_blender.log.

The newly added negative-scale, extremely shallow fixture is rejected by the
same deformation guard in both baseline and optimized implementations; its
test verifies that the guard remains enforced. New support for that particular
transform is outside this performance change.

Full interactive motion acceptance and GUI undo timing were not repeated
(未验证). Background equality/rollback evidence does not replace that acceptance.

## Installation and snapshot

Built 0.62.17 and deployed to Blender 5.2 and X/addons using repository tools.
Both targets match all 110 canonical files. Live changed functions were refreshed
without re-registering property groups or resetting the Twist session. Before
and after checks preserve the live original's Rest, pose, mesh, weights, Shape
Keys, Finger records, selection and preset. Original X.blend was not saved or
overwritten; no calibration/generation was run against the original by this task.

The user was working on the Weight page during final screen observation; no
selection/page changes were made for a screenshot. Advanced routing was tested
in Blender through the panel draw tests.

Runtime changes from 0.62.16: __init__.py, body_calibration_ui.py, limb_ik.py,
limb_ik_fk.py, body_setup.py, body_setup_transaction.py. Independent diff review
found no new blocking issue.

Evidence (the task began before midnight):
`D:\Blender\Projects\Character\X\task_artifacts\body_calibration_20260927\generate_perf_0_62_17`

performance_comparison.json contains exact timings/equality measurements.
manifest.json identifies the exact source-and-tests ZIP, package SHA256 and
per-file hashes; changes_from_0.62.16.patch is the runtime diff.

Deployment backup:
`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260928-001528-c0552063`

Local changes only; no commit or push. Unrelated existing work preserved.
