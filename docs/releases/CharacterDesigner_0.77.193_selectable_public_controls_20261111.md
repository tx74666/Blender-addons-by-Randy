# Character Designer 0.77.193 — Selectable public Controls

**2026-11-11: official build, product validation, final eight-case scoped updater
and both disk deployment checks passed. Actual current-artist ALREADY193 audit and20-lock selection repair passed on2026-11-12; protected authors/context stayed exact.**

Normal Controls now restores selection for its publicly displayed operation
bones. The saved X.blend at 21:13 had 20 visible Body controls with native
`hide_select=True`, covering the master, torso, eyes, hand/foot IK, poles, Foot
Roll and Toe Bend. The earlier Controls code changed visibility but retained
these locks. The initiating writer is unproven. The saved file is evidence of
that state, not a substitute for the current unsaved artist session.

Body selection repair uses the managed public group and existing native/generated
operation records. It follows the current IK/FK display and effective Pose hide
state, requires the Rig object and a participant collection to be visible, and
leaves unknown owners, guide-only bones and internal Foot/Wrist mechanisms
outside that permission. Hidden IK inputs retain their locks. Explicit Hair or
Dress group requests repair their own accepted visible operation bones.

The normal Controls button repairs visible Body selection after a formal
Original return, a temporary native-bone display return, or an ordinary Controls
request. This step changes selection locks only; independent Hair/Dress flags,
collection membership and eyes, separate Edit/Pose hide flags and object flags
remain exact. Direct recovery/display restoration retains its saved flags.
Weight-page Original / Controls and Finish Painting already use the normal Body
policy, so no painting-session code change is included. No new Original, Rest,
structural, recovery or calibration authority is introduced.

## Complete source and paused work

Formal193 extends complete approved191 by four files: `__init__.py`, `README.md`,
`bone_display.py` and `body_original_mode.py`. It contains 270 files / 238 Python
files, with the other 266 inherited bytes exact. Projection:

`D:/Blender/Projects/Character/X/validation/controls_selectability_20261111/stage193_01/projection.json`

SHA256 `41790111f27a239ea30e0cb4c53ee1673ef96aa12760036c3f1afd4f03f68757`.

Canonical source was updated first through a four-file selective merge. All 267
unselected canonical module files stayed exact. Receipt
`canonical_before193_01/apply_receipt.json` SHA256
`a8d882f18cb07c8fb09c55ff7f9c23d0362ff66cc189696d23ac960b63cb4827`.
The merge preserves Animation's incomplete192 work, including its other AST and
bytes in the same source files. Formal193 deliberately excludes those paused UI,
entry, poll and README fragments and retains the approved191 UI names/routes.
All reviewed display definitions and the Original Controls-return tail match
the actual canonical source by AST and exact raw segment hashes; all remaining
release definitions and the Original operator prefix/suffix remain approved191.

The reviewed overlay is `reviewed_release193_inputs03`; the existing official
projection API was used with that explicit frozen root. Unrelated paused Rig
provider and Body/UI work remain excluded. Plain canonical is not a release
source. Future Animation work must rebase onto complete193 and prepare a new
explicit delta; neither its incomplete192 updater nor any old191/192 installer
may be replayed against the newer dirty artist session.

## Validation and build

Sixteen native Controls cases and nine inherited Hair/legacy cases passed.
The original191 implementation independently reproduces all 20 locks. The new
cases verify the fixed 20-name public set, mixed FK/IK, toggle Off/On, registered
ordinary Controls, formal Original and temporary-native returns, real edited
weight painting/preview/resume/Finish, passive Action references, foreign and
mechanism boundaries, rollback after selection writes, separate Edit/Pose hide
semantics, hidden Rig/collection behavior and foreign/stale/shared-owner refusal.
Formal Original's tested pose matrix delta is zero. Real Action/NLA bindings,
Shape Keys, group locks, edited weights, frame, Auto Key, raw pose and saved
session checkpoints remain exact within the recorded fixture scopes.

The final formal package reuses these 25 native groups through all238 runtime
AST and recursively compiled code-shape equality. Only the version literal is
normalized; body_original_mode differs in CRLF transport, bone_display also has
two fewer empty lines, and README is prose. Nonempty source lines, AST and
recursive behavioral code shapes remain equivalent. The immutable report's
initial CRLF-only prose is corrected by `final_native_inheritance_metadata_note01.json`.
This inheritance analysis is not a fresh native execution. Receipt
`final_native_inheritance01.json` SHA256
`9f6787ee9c4890fabdd117f36871cc7673cc9f469684f2f70968fdb5b92a0096`.
The original native receipts, sources and worker exit evidence remain retained.

The merger's three isolated filesystem checks passed: exact four-file success,
failure at the second atomic replacement with only the owned output rolled back,
and a concurrent selected-file write preserved. These verify the source tool,
not Blender runtime behavior.

Official ZIP `dist/character_designer-0.77.193.zip` SHA256
`71c60808015ad3d582c80cc2ccd7ee1562b0a49a572ded0ad08227c91e63a298`
passed CRC and the exact 270-file payload check.

The final formal191-to193 scoped definition updater passed eight native groups:
warm/cold readers, cold no-session commit without helper resurrection, partial
owner refusal, rollback at four definition fault boundaries, active painting
commit with author/state/checkpoint/function identities retained, zero-write
repeat, and explicit owned rollback followed by actual Finish. The commit changes
four existing code objects, adds five helpers and updates the version: ten writes,
no signature/default/RNA lifecycle change, and no Scene access in the updater.
Receipt `delivery01/final193_01.result.json` SHA256
`1ca3c0aae2e36472d974bd78f7637b99b0bd210d7d5a6662ea6fa478c778ad5b`.
Validation closure `final_validation_closure01.json` SHA256
`28395de33a608748bf7a1d6a003ac595fa4b2e0a62d288717faef0082680c36f`.

Six native selection-wrapper groups also passed with six real mesh copies,
all Action data, raw pose and context protected. Only the independently
reviewed 20 lock values changed. Current-session, hidden-object and changed
target refusals and postcheck rollback passed. Report
`delivery01/selection03.result.json` SHA256
`1ee822d6a1447dfe2486537172cc8f131ab3a8d9e9d640ecf8f53ea03431a9ab`.
The final tool changes only its pinned reader hash after this run; this is
prepared-tool evidence, not current-artist execution.

Official deployment completed to Blender Roaming5.2 and X. The matching check
then reported 270 files / zero differences for each target. Backup:
`C:/Users/Randy/AppData/Local/CodexBackups/addon-deploy/20261111-232001-88d171f4`.

## Actual artist acceptance — 2026-11-12

After explicit user foreground handoff, native preflight found ALREADY193: reviewed definitions exact, zero Root code writes, no refresh-time or initiator claim. The20 visible Body selection locks remained in the current scene.

The synchronous selection transaction passed: exactly20 native hide_select values changed True to False; all other392-bone object/data/collection/hide/shape/selection fields remained exact. Current raw pose, all six mesh hashes, all nine Actions, session/Finish identities and values, frame22, Auto Key Off, active f_index.02.L, no selected bones and dirtyTrue remained exact. Visible locked names after repair: none. No scene or preference save was performed.

Actual report: `delivery01/artist_apply_controls193_01.json`, SHA `7793da4a1097ae7bebab394c7acd5354dcb41c59f17fc0e47523157f7b985005`. Acceptance: `acceptance193_02.json`, SHA `129c55a0b0461ae6e2964409281ee1ea5bf4eb44f53fa378ac6460ce0f542496`. Author protection before/after is identical: `a09c2b4d0ad0d1b2bfff07f6f77a0d60fd4d493eb17e7bb43ec86746c283eefd`.

The new thin entry reuses the immutable native-tested transaction core. Within a single synchronous call, the first callback uses that same call's fresh typed before witness, and the second reads a fresh after witness. All callback-count, source/session ownership, exact20 display-delta and author checks remain inside the existing owned-lock rollback boundary. Two full reads took about271 seconds including the transaction; this is a current protection-read duration, not an FPS measurement. Seven pure mock/AST checks and an independent filesystem audit are retained; no extra artist mode-cycle test was performed.

The lower Graph Editor and its visible frame/cursor were restored. Preexisting unsubmitted189 Console text was preserved and restored without execution. No old recovery, save/open operation, pose change, live brush/Original/painting cycle or physical mouse-selection proof is claimed. The saved X.blend hash remains54f2d3d7…8b76; current selection-lock changes remain in the unsaved artist scene.

The original Animation retry failed Bad Request again. A user-authorized fresh Animation continuation was created and observed performing offline work; it owns the unfinished Animation scope. Controls foreground work is complete and returned. Earlier pending acceptance193_01 remains immutable history.

Retained evidence includes the baseline20-lock failure, candidate01's rejected
overbroad Hair unlock, both candidate02/03 scopes, prior191 failures and all
recovery limits. Merge/overlay preparation failures remain classified as tool
errors: an outer/inner-except slicing error, draft README192 labels, CRLF/raw
line-ending proof preparation errors and the actual-source integration detector
that initially retried an already merged tail. Each refusal preceded its
prohibited next write. The corrected actual-source detector normalizes line
endings only for detection; final publication and segment hashes stay raw exact.

Local source/build work is not a Git commit/push or Unity synchronization. No
scene/preferences save, application restart/full add-on reload, rebuild, reweight,
calibration recapture, Unity write or Git upload is authorized by this release.
Current saved/dirty state, actual artist update and disk deployment must be
reported separately in the final acceptance record.
