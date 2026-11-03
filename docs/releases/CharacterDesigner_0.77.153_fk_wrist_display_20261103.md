# Character Designer 0.77.153 — FK wrist control display

FK upper/lower arm segments retain their native bone appearance; the wrist
retains its existing owned ring on the native hand bone. Wrist rotation and
keyframes continue to use that FK hand bone. IK target controls and Original
native-bone display retain their existing behavior.

The prior display synchronization detached every owned ring in FK, including
the wrist. The corrected handoff excludes the arm's third chain bone from that
detach. A validated older detached-wrist marker is restored through the same
transaction, with ownership, artist-display, animated-display and rollback
guards retained. Existing ring meshes, sizes and IDs are reused.

The complete candidate extends approved151 with only version metadata and
`limb_fk_visuals.py`. Two unchanged helper definitions retain their canonical
position; all other function ASTs and non-function statements match151. No
unrelated Animation2/private150 or Hair follow-up candidates are included.

Evidence and final delivery status:
`D:/Blender/Projects/Character/X/validation/fk_wrist_display_20261103/acceptance153_01.json`.
Disk deployment and any observed live code/display adoption are recorded
separately. This release does not save the artist scene or preferences.

Delivery completed locally: both Blender5.2 and X copies matched all255 files;
the official ZIP passed CRC and exact projection comparison. Pure13, focused
native5, display10, visuals8, scoped updater9 and scoped assets6 passed. The
first visuals fixture failure remains retained beside its corrected derivative.

Artist36008 had an older8-binding registry without either wrist. One function's
code was adopted without reload, then precisely two owned wrist ring objects
and meshes were added through the existing guarded build. No Body rebuild or
membership refresh ran. Post-event readback found both wrist rings and target
code; frame14, Pose mode, the selected Hair bone, dirty state, native ARM inputs
and Action slot were retained within bounded checks. No scene/preferences save
or physical wrist manipulation/save-reopen acceptance is claimed.
