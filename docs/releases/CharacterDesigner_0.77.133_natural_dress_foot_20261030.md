# Character Designer 0.77.133 — neutral tests with Dress and Foot controls

Rest Pose was refusing a generated Dress Spline IK constraint as foreign, then
refusing the generated Foot roll/bank drivers. Natural tests now authenticate
those owned graphs. The neutral test stays independent of the retained Action
while ordinary posing and Forearm Test Angle remain available. Returning to Pose
restores the prior Action, slot, NLA, Object/pose inputs and constraint mutes.

Only the owned terminal Dress Spline IK constraints pause during Natural; Mid/Hem
curve controls pause with them. Foot drivers remain enabled. Their exact native
targets, expressions, array indices and generated two-point linear identity
mapping must validate. Unknown or edited evaluators still refuse before posing.
Manual Dress with no backend and stopped-confirmed motion is covered; active
motion/recovery/backend guards remain. Source/wire Object references are pinned;
an independent same-content CurveData/MeshData identity lock is not claimed.

Complete133 has239 files/207 Python files. It extends approved complete131_03 by
three changed files,236 exact; `_natural_plan` is the only changed runtime AST
function. All approved Hair130 bytes and the131 Dress helper are retained.
The132 disk intermediary is superseded: review found its missing FCurve mapping
check before live installation. Both native modes reproduced the old gap and
133 refused all14 mapping mutations with the current state exact. The132 raw
receipts and earlier131 failures remain unchanged.

Validation: Controls/Original genuine bilateral Foot+Body+manual Dress positives,
native wrist rotation,90-degree Cancel/Return, keyed Reset,22 unchanged-state
refusals; bounded updater20 checks/two exact rollbacks;17 guard+19 legacy tests.
The unchanged131 Dress64-angle/save-reopen evidence is inherited within its
recorded scope. Official ZIP and Blender5.2/X deployments each239 exact files;
independent CRC/payload/AST verification passed.

Artist12460 received only the final function update after the earlier four131
Natural functions. Read-only current-character admission passed; bounded state
was unchanged, frame6/Object Mode/dirtytrue retained. Right Asset Browser was
restored, left pending Console input untouched. No artist pose trial, Inputs
close, scene/preferences save, restart or full package reload occurred. Loaded
package metadata was observed130; complete133 RNA re-registration is not claimed.

Acceptance: `D:\Blender\Projects\Character\X\validation\natural_foot_drivers_20261030\acceptance_summary133.json` SHA256 `52d9dd6e3fc0358cadc4a292049d656096ddb7f1371f36aaf991dc1019bbfa64`.
Projection SHA256 `bf2af66db506ae65634680acd1ff9b1cb7de720de171f7ea1ed47ce4c7b025d5`.
ZIP SHA256 `9c21adb0b1de4f04f00315152e4d6c38c4484cb68e618f4fd43db8f172dcc1bb`. No Git upload or closed FPS branch work.
