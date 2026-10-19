# Character Designer 0.77.52 · Rebuild evaluation and history

Historical local validation and installation record. A subsequent artist
Ctrl+Z crash reached the deferred `_resume_saved` scene traversal in .52, so
this version did not resolve the artist's history crash. Original acceptance
reports remain unchanged. See the new
[.53 mitigation and limits](CharacterDesigner_0.77.53_history_recovery_20261018.md).
The artist scene was not refreshed, rebuilt or saved by the .52 validation work.

## Incident and preserved inputs

The artist reported that **Both Legs > Rebuild Controls** displaced the shoes,
then Blender crashed after Undo. The 0.77.51 incident included the guard message
`Removing controls would change 'Shoes' (0.0954632 rig units)`.
This message comes from a shared deformation check: direct Rebuild internally
uses release and bind, so it does not establish that the artist chose Release.

The crash trace places the Undo failure in `skirt_wiggle._undo_post`, during iteration
over scene data. The preserved crash trace and saved X input are under
`D:\Blender\Projects\Character\X\validation\body_rebuild_regression_20261018\incident01`.
The saved input SHA256 is
`8d25e5ef2dc158b42acbeeb41ccdba7ed4aaff068cd55b22fd57118ebb792581`.
This saved copy does not contain a proven reconstruction of every unsaved edit
at the time of the crash. No complete reproduction of the unsaved artist state
is claimed.

Artist process 10432 and the saved artist file are not rebuilt, moved or saved
by these checks. Read-only inspection identified the latest `quit.blend` as
Builder6, not an X recovery. The scoped Temp/Blender 5.2 recovery search found no
new X autosave for this incident. Existing backups and the incident evidence
remain intact.

## Changes

Regional operations synchronize the current dependency graph before capturing
the pose and evaluated surfaces that must be preserved. Limb matching keeps the
current frame **and subframe** during temporary evaluations. These changes avoid
using a stale evaluation as the rebuild baseline or silently returning to a
different fractional frame.

Undo/Redo callbacks clear obsolete Python references and defer work on freshly
resolved Blender IDs until native history restoration has completed. The
pre-history handling preserves the existing **Automatic** setting; the fix does
not force a different artist setting as a workaround.

The intended contract is to keep the current authored meshes, weights, Shape
Keys, native Rest and world placement while rebuilding the selected controls.
No saved raw shoe vertex array is written back, and no compensating mesh shift
is applied to hide a deformation difference. A guard failure must preserve the
existing scene through rollback.

## Validation

Four targeted evaluation cases expose differences not covered by the earlier
saved-copy acceptance. On 0.77.51, direct Both Legs rebuilding produced a
`0.14509` shoe surface difference; a public Both Legs case at a fractional frame
produced `0.0648089`. These are controlled diagnostic cases, not measurements of
the artist's complete unsaved scene.

After current-graph synchronization and subframe preservation, all four targeted
evaluation cases meet their preservation checks. Fourteen pure history/lifecycle
checks and 45 native checks passed: four pending-evaluation cases, 14 real
Wiggle 2 lifecycle cases, 18 regional regressions and nine regional-style cases.
The external Wiggle 2 source is version 2.2.4, SHA256
`7333687ae3b6fd20feae8afda649faed1d99bc7b98e92622a8993c09061deac1`.

A separate factory-startup GUI process completed the public operator with its
native automatic Undo history: one baseline entry, the Python operator's native
Undo flag explicitly enabled, no manually inserted entry after Rebuild, timer
settling, then native Undo and Redo. All three comparisons passed, including
exact restored graph/recovery revisions after Undo and Redo. Raw shoe data,
weights, Shape Keys, native Rest, object matrices and custom styles were kept;
evaluated world surfaces stayed within the existing 1e-4 m numerical limit.
This uses native GUI history invocation, not a claim of physical keyboard input.

Earlier diagnostic failures are retained. The first GUI harness omitted the
Python API Undo flag, so Redo did not restore an after-Rebuild entry; two F20
keymap harnesses did not dispatch the target operator. They were not reported
as passing or used to weaken graph assertions. An older export-test fixture
also lacked current export configuration defaults; the corrected test still
calls the real export capture and verifies the original snapshot barrier.

## Source scope and delivery state

The release is the accepted 0.77.51 projection plus four runtime files:
`__init__.py`, `body_region.py`, `limb_ik.py` and `skirt_wiggle.py`. The paused 0.77.48
optimization candidate remains excluded. No playback or FPS gain is claimed.

Evidence belongs to
`D:\Blender\Projects\Character\X\validation\body_rebuild_regression_20261018`.
The complete 202-file projection SHA256 is
`30ed525289bed552c82a94fec4fdb489ff7edd79477cc71eb944167987938fdc`;
package SHA256 is
`ba7aee84e84b373ae1b79c587be14794da6b3b3b09aca5ceb4b6ce388d75185d`.
Blender 5.2 and X/addons were deployed and checked with zero mismatches.
Previous deployed files are backed up under
`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20261018-051809-e7245571`.

The native incident stack is preserved, but the complete lost unsaved artist
state was unavailable. These results do not certify every scene or third-party
callback configuration, a full gait, physical sole contact, or FPS improvement.
Only isolated test scenes/recovery copies were saved. The artist file hash stayed
unchanged and all owned Blender checks exited. No Git commands, commit, push or
publication were performed. Actual assistant model/effort: unrecorded.
