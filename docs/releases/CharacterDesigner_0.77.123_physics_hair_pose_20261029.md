# Character Designer 0.77.123 — Physics Off, Hair versions and Relax Pose

Status: the official ZIP and dual disk deployment completed, but final whole
release acceptance was held. Independent Hair review reproduced loss of current
temporary animated values outside the version-family transform snapshot during
sampling, on success and rollback. The frozen 123 package and its receipts remain
unchanged. The correction is released as 124; do not replay a 123 installer.

Physics Off previously left muted bones in Wiggle 2's native solver list. The
backend still reset those bones and updated the view layer, while Hair and Dress
guards continued full checks on paused frames and timers. Paused owners now
detach from that list and skip those repeated scans. Running peers remain owned
and connected; explicit resume and cleanup retain their ownership and recovery
checks. Hair pose edits made while paused survive resume or stop. A private
three-frame reproduction changed from 60 native bone resets to zero. This is a
residual-work fix, with no artist FPS recovery claim.

Change Hair Segments creates an independent version of an existing binding at
1–12 bones per chain, including six and seven. Each currently painted influence
migrates through its original Rest interval; unrelated paint, locks, mesh data,
Shape Keys, rig Rest, Actions and the original version remain available. Visible
Action-slot/NLA motion is sampled into an independent Action, with retained
strand settings and character ownership for Physics. Changed counts approximate
bending and motion between samples. Explicit version switching, native Undo/Redo
and owned cleanup retain current author edits across the version family.
Cleanup requires resolved temporary animation edits; a refusal keeps the
retained versions and their data available.

Creating a version requires stopped Hair preview and paused running physics.
An explicit active Hair source takes precedence over a remembered version.
Native copied solver fields use the authenticated write guard, and failed
transactions remove only their own created IDs.

Saved regional Poses now validate their keyed native bones and both recorded and
current ancestor chains. Unrelated branches can retain their current Rest.
Otherwise unchanged connection flags are compatible only with proven zero
translation; related Rest, hierarchy, missing-channel and legacy checks remain.
Real Original-session validation is unchanged. The saved X Relax asset contains
15 left-finger bones, rather than a full-body Relax pose. A private saved-file
application verifies those 15 bones against a native source-skeleton reference.

The complete release extends approved 0.77.122 with explicit reviewed files.
Approved Body/Forearm and Animation UI implementations are retained, and unrelated
canonical candidates remain excluded. Component failures and pinned harnesses
remain in their original evidence folders.

Source pins, focused tests, native rollback/history/preservation receipts,
official ZIP and dual Blender 5.2/X deployment results are indexed in
`D:/Blender/Projects/Character/X/validation/reassigned_work_20261029/release_status123.json`
and `official_release123.json` in the same folder. Those receipts retain their
component scopes and do not grant final whole-release acceptance.
Disk deployment is separate from live activation. No artist scene or preferences
are saved, no artist Hair count is changed, and no Unity or Git upload is made.
