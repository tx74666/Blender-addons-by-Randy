# Character Designer 0.77.54 · Rejected Rebuild, Undo and Pole Layout

Local validation and delivery completed on 2026-10-18. Frozen candidate09 is
accepted for the checks and limits below and installed in Blender 5.2 and X's
add-on copy. The original artist X file and user-saved check copy were unchanged;
the open inspection window was not refreshed or saved.

## Incident and reproduction limits

The artist clarified that the crash occurred on **Undo after Rebuild was
rejected**. A new crash trace reaches native dependency-graph object building;
it does not contain a Python callback establishing the same cause as the older
.51/.52 history incidents.

In an isolated .53 GUI using the saved X layout and frame 20, clicking **Both
Legs > Rebuild Controls** produced the real guard message
`Removing controls would change 'Shoes' (0.0954632 rig units)`. This operation
internally releases and rebuilds the selected controls; the wording does not
mean the artist clicked Release to Edit. The operation returned to the GUI, but
the protected-state comparison did not pass and the recovered ownership proof
was no longer valid. It is retained as a failed case.

The fixed saved X input has SHA256
`8d25e5ef2dc158b42acbeeb41ccdba7ed4aaff068cd55b22fd57118ebb792581`.
It loads in Object Mode with the rig's pose position set to Pose. The actual
unsaved artist scene has not been reconstructed. Public calls made by a timer
after a marker transform can change evaluation timing: the non-injected timer
case completed instead of reproducing the UI guard. It is not evidence that the
artist's rejection disappeared.

During the pause, the user manually reopened and saved
`validation/rebuild_rejection_crash_20261018/CD_Rebuild_Check.blend`. That copy
now has SHA256
`f2278362720dcbb7a1ac9adbf18fe36d8ac9878356d148a9c8f8123236f556e5` and must
be preserved as user work, not reused as the pinned diagnostic input. The
original `X.blend` and the incident01 copy remain at the `8d25...2581` hash
above. `resumption_observation_20261018.json` records this distinction; those
reopen/save inputs were not performed by the agent.

## Changes

A same-rig regional rebuild shares one outer transaction checkpoint. Nested
release/bind failures must not restore and recreate the same widget IDs twice.
Style cleanup waits until the owning transaction has completed recovery, so a
failed nested release cannot discard the style resources that recovery needs.

After a transaction reports completed in-place recovery, the public regional
operator reports a warning and closes its native Undo boundary. The requested
Rebuild remains unapplied. Its `FINISHED` result records the recovered native
state rather than implying that rebuilding succeeded. Preflight refusal and
incomplete recovery retain their refusal/error behavior. Candidate04's strict
injected-failure GUI case verified this boundary, and final candidate09 passed
the strict trusted native history case.

The native-roll approach introduced in candidate04 captures scalar EditBone roll from an
independent Armature data copy. The source rig never enters Edit Mode for this
capture. The temporary copy is removed and the source context restored before
the transaction proceeds. Changed Rest frames restore that native roll;
unchanged frames bypass reconstruction. `_bone` retains its .53 representation,
so the earlier full-matrix snapshot does not expand protected-state guards.
This changes neither raw shoe vertex arrays nor authored weights, and
introduces no compensating mesh movement or relaxed ownership tolerance.

The Body Part box now contains Part, status and Rebuild Controls. Release to
Edit is available inside the existing Advanced section for bound arms/legs.
Routine guidance and preservation paragraphs are removed; actionable errors
remain visible. The existing regional backend and foot-only selection remain
available.

After the user reported unexpected Pole placement, candidate05 added a regional
Pole layout and fixed-Pole matching path. Rebuilding carries the intended Pole
layout through generation and solves the IK angle while preserving the native
pose. Candidate06 tightens the stored layout's version/type and finite-coordinate
validation. The user explicitly requires both leg Poles in front; the fixtures
record the configured `-Y` front rather than inferring it from knee appearance.
Candidate09 includes the stronger skin-matrix angle objective and refinement
at the `-pi`/`+pi` endpoints. Validation uses fixed saved X and disposable
fixtures, without applying a repair to the current unsaved artist scene.

## Validation and preserved history

An earlier isolated Rest-reconstruction comparison covered 346 bones. Saved-Z
alignment introduced up to `4.295259714126587e-6` matrix-component error and
exceeded the diagnostic `1e-6` limit on three right toe helpers. Full-matrix
Axis/Roll reconstruction kept head/tail error at zero and maximum matrix error
at `4.731118679046631e-7`. This is a numerical reconstruction check, not proof
that complete failed-Rebuild recovery or Undo/Redo is correct.

Candidate03 passed the strict saved-X injected-guard GUI case, including both
Undo and both Redo boundaries. It then failed one of the 11 regional-style tests:
the retained-owner fixture exposed two helpers with approximately `1.9e-6`
Rest matrix error under the full-matrix Axis/Roll method. The failing result and
source are preserved. Candidate03 is not the final candidate.

A 74-bone shadow-copy prototype restored native scalar roll with zero measured
Rest error. Source Bone/PoseBone wrappers, context, ID names/pointers/users,
resources and ownership remained unchanged. The source never entered Edit
Mode. This prototype isolates the capture/reconstruction method; it does not
replace the complete transaction/history checks.

Candidate04 passed all 11 regional-style tests. Its retained-owner rollback
fixture kept all 74 native/generated Rest states exactly unchanged
(`exact_rest_changed_bones: []`), retained the original recovery-proof bytes and
passed both single-armature and revision authentication. Four shadow-capture
fault cases also passed: failure after real shadow Edit entry or Object exit,
each with the source in Object and Pose Mode. Candidate04's strict injected GUI
case passed protected-state checks and both native Undo/Redo boundaries. These
results do not establish final acceptance of the later Pole candidates.

Candidate04's actual-click diagnostic passed after-click model protection.
Its recorded `after_undo` phase is a failed harness-input check: the physical
Undo event had not been delivered, but the phase signal was sent and recorded
zero native Undo callbacks. Preserve that false result; it is neither an
accepted Undo test nor evidence that native Undo itself failed.

Candidate05's trusted saved-X diagnostic completed with exact raw mesh and
native Rest preservation, valid ownership and evaluated surfaces within the
existing limit. It is an in-memory service diagnostic, not a physical UI or
native history test. It does not certify every Pole layout or a full animation.

The historical candidate03 projection was independently checked against all 202 manifest
entries with no hash mismatch. Its transaction AST changes are limited to
`_bone` and `restore`. Candidate04 replaces that reconstruction approach with
the native-roll probe/capture/restore path. Its 202-file projection retains the
same six-file delta over .53; manifest SHA256 is
`7e2b286faa559d5a90735cbe74e781eaa84ada18af969ec92f858c04574c1356`.
Source checks and a manifest do not constitute runtime acceptance.

The current final candidate is candidate09: 202 files and seven changed files,
including fixed-Pole matching in `limb_ik_fk.py`. Manifest SHA256 is
`6204990572509a1915a379f8699c0e3cee97097622943f00378d4c1a937d296c`.
This is the accepted local-delivery projection.

All seven final candidate09 Pole-native cases passed in 15.937 seconds. The
report and stdout record per-case maxima. Successful operations retain the
existing public native-skin matrix limit `2e-4`, exact raw mesh/native Rest and
world surfaces within `1e-4`. The largest recorded success skin error was
`2.9385089874267578e-5`; the largest world surface error was
`8.456999751809665e-6`. Failed-operation rollback remains subject to the
separate `2e-5` skin check; the recorded rollback cases had zero skin/surface
error.

Candidate07/08 test failures are retained: the new success tests initially
applied rollback's `2e-5` limit to successful rebuilding. The same shortened-leg
case on baseline candidate04 also failed that mistaken assertion at
`2.9027462e-5`. The corrected tests distinguish the existing public success and
rollback contracts; the runtime success limit was not increased. Candidate09's
seven-case report records this distinction rather than silently discarding the
failed runs.

The final candidate09 regional suite passed all 18 cases in 48.231 seconds;
the final style suite passed all 11 in 44.082 seconds. The one UI check from
candidate07 is carried forward because all three tested UI files are byte
identical to final09. The four shadow-failure scenarios from candidate04 are
carried forward because transaction/capture and atomic-owner bytes are
identical. Pure checks passed: nine atomic, 21 regional UI and 13 adjacent UI.

The trusted candidate08 GUI check passed native Undo 1 and Undo 2 with their
strict states/markers. Redo 1 then failed its native poll; a new operator was
visible between steps, but its origin is unproven. Preserve the failed report;
it is not complete history acceptance. The final candidate09 trusted v3 GUI
run passed in 72.1 seconds. The loaded file had no script-autoexec failure.
All four native history boundaries met their exact state/marker checks, with
Undo pre/post counts of two each and Redo pre/post counts of two each. No
intervening operator appeared in this run. This verifies native GUI history
invocation, without claiming physical keyboard or actual-button coverage.

The final candidate09 trusted saved-X diagnostic passed in 29.634372 seconds.
Raw meshes and native Rest were exact, ownership authenticated, and maximum
evaluated world-surface error was `7.119551203929834e-6`. Both Pole Rest and
Pose positions were in front on their own side. Their armature-space heads
were approximately L `(0.0969186, -1.1762775, 0.6832272)` and R
`(-0.09692185, -1.1763467, 0.6832279)`. This service-call case remains distinct
from the native history test and the latest unsaved artist scene.

Two preliminary GUI harness attempts stopped before executing Rebuild because
their report observer assumed a class/report API that Blender did not expose.
Those failed reports remain intact. The final history harness uses no report-method
monkeypatch; the warning severity is checked separately in captured output and
the operator warning branch is covered by pure UI tests. No native pass is
claimed from the preliminary attempts.

## Trusted script execution

The user authorized the trusted X/development script workflow. The saved
Blender 5.2 Auto Run Python Scripts preference was already enabled; earlier
validation launches explicitly disabled execution and opened files with
`use_scripts=False`, causing the repeated prompt. The isolated trusted fixture
executed its registered Text with no autoexec failure and left the preference
file unchanged. Evidence is in `validation/autoexec_preferences_20261018`.

New trusted checks use `tools/run_trusted_blender_validation.py` and trusted
file loading. Old disabled-policy harnesses/results are retained. Measurements
must disclose that embedded scripts and drivers are enabled; these evaluation
conditions differ from the old disabled launches.

The saved profile originally enabled eight add-ons without Character Designer.
After explicitly loading that saved profile, only the requested
`character_designer` module was added to the preserved original module list.
Non-add-on RNA matched the baseline taken after `read_userpref`; other add-ons'
preference contents were not independently fingerprinted. This does not prove
equality with every original on-disk preference field, and the current
preferences panel page may have reset. Auto Run Python Scripts remained true.
Native preference save and readback passed. The backed-up before hash is
`12d68414248fb96e05aa98f7b442c82afa1f5e3bc70b8ff0a980979f4feadb35`; the saved
after hash is
`b3954dcba4c1d068f22d4c517cdbc782aa9909284fb31451073bd1fb8182cd30`.
The saved profile was loaded before editing; the comparison scope is limited
to the after-read baseline and module list described above.

A fresh normal startup, without factory-startup mode, registered installed
.54 from the saved preferences, verified all 202 deployed files and reported
no autoexec failure. See `character_designer_enabled_preferences.json` and
`normal_saved_startup_verified.json` in the autoexec evidence directory.

The original X file remains at the fixed `8d25...2581` hash.
The old PID 30560 diagnostic window is preserved for user inspection; it retains
its original disabled-execution launch conditions and has not been refreshed
or saved by this validation. The original artist X file was not changed.

## Local delivery and limits

The completed-recovery history check establishes two native history
boundaries: baseline marker M0, transformed marker M1, then rejected-but-restored
Rebuild at M1. Undo once must preserve M1 and restore the pre-Rebuild graph;
Undo twice must reach M0. Two Redo steps must restore M1 and the recovered graph.
Each step requires a distinct history callback and strict model, custom-style,
ownership, reference and resource checks. No manual Undo entry may be inserted
after Rebuild to make this pass.

The .54 package is
`D:\MyRepository\Blender-addons-by-Randy\dist\character_designer-0.77.54.zip`,
SHA256 `d32c6086a22e8aedfe0d8497c697e2111838034faa8986b09e02855d56245cd0`.
Both Blender 5.2 and X/addons contain 202 verified files; seven files were
updated per target and the final check found zero differences.
`deployment_check.json` retains the receipt and prior-file backup location.

`accepted_summary_v2.json` indexes the final results and corrected preference
comparison limits, SHA256
`edf96200f2933e7f086babb7b6c08cf450887a8053e4ffd226be08cf039ec5e7`.
It supersedes `accepted_summary.json`; that original summary is retained
unchanged for provenance, including its overbroad preference statement. Runtime
validation results are unchanged.
The old PID 30560 inspection window and its unsaved work were preserved. All
other owned native validation jobs exited. On-disk deployment and a verified
fresh startup do not replace the runtime in that existing window.

Final history coverage uses two native Undo and two Redo calls in a GUI event
loop after an injected failure following the real guard. No final physical-key
claim, complete reconstruction of the lost unsaved state, full gait/sole-contact
certification or general third-party/FPS guarantee is made. Failed historical
reports remain intact. No artist repair or scene save was performed.

The candidate is a scoped projection over .53: `__init__.py`, `body_setup.py`,
`body_setup_transaction.py`, `body_region.py`, `body_region_ui.py`, `limb_ik.py`
and `limb_ik_fk.py`. The latter two include regional Pole integration and the
Body Advanced UI hook. Paused .48 optimization work remains
excluded. No FPS or performance improvement is claimed.

Raw evidence and failed attempts belong to
`D:\Blender\Projects\Character\X\validation\rebuild_rejection_crash_20261018`.
No Git operation or publication was performed. Actual assistant model/effort:
unrecorded.
