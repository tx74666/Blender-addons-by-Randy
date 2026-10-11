# Character Designer 0.77.14: verified Unity import recovery

The corrected Cosha FBX and production Avatar are now published and validated
in RandomRealm2's Unity 6000.5.9f1 session. Native Idle/Walk sampling, independent
author-coordinate bind checks and current-model Forearm correction pass. The
export Roll and calibration repairs are documented in
[0.77.12](CharacterDesigner_0.77.12_export_forearm_20261010.md).

This release closes a further import recovery gap. Pending Forearm sidecars are
persisted in SessionState across assembly reload; failed/incomplete work remains
pending until its related import or a reload. The native reload probe found the
queue restored with Ready=1 and scheduled=true, while the editor was idle and
the delayCall had not executed. Scheduling now uses one EditorApplication.update
callback, unsubscribed at Drain entry. Busy editors reschedule; failed assets
do not spin on every idle update. Generated correction data uses its asset
filename as the main-object name. Checksums, ownership, rollback, coordinate
conversion and correction geometry contracts remain unchanged.

## Native acceptance

Character was the sole Unity Assets/editor writer. X changed canonical source,
reviewed static evidence and deployed only the approved add-on projection.
Evidence is retained in
`D:/Unity Projects/RandomRealm2/.codex/task-staging/character-avatar-owner-20261010`.

- Ownership validation passes, including injected rollback and production
  guards. Five retained animation fixtures received private Avatars; a repeat
  repair changes zero fixtures. Production Avatar GUID remains
  `874ce658cb6c4b54a8203998231760cd`.
- `evidence/native-bind-corrected-20261009-211728-4b3c9fec/native.json` passes
  strict actual Animator Idle/Walk sampling on owned Source and Player previews:
  all four samples have correction enabled, initialized and successful.
  Source dependency hash is `e0c06dd9d8e2623c0b10beb60c504980`.
- `evidence/forearm-current-model-final.json` passes bilateral 45/90-degree,
  bent/raised-arm and transformed-root correction checks on the current
  17,349-vertex imported Cosha mesh; original mesh and fingerprint are restored.
- The separate ordinary-skin native provenance check verifies correction-disabled
  sampling uses the actual original mesh, rather than its correction clone.
- `evidence/cosha-runtime-reload-inspect-20261009-214907-b034a82a/report.json`
  passes a genuine domain reload of the same seeded production sidecar. No new
  seed or direct Build masked the stalled queue. Nonce changes, Pending/Ready
  become empty, both importer/probe SessionState entries are erased, one current
  correction remains, and data name is `Cosha.Cosha.Forearm`. All six metadata
  GUIDs and hashes, exact FBX, scene/selection and two pre-existing previews are
  preserved. Saved/current Source hashes remain equal to the strict sample input.
  The later repeated Inspect calls fail because the successful first call erased
  its own probe record; these diagnostic replays are retained separately.

X's independent
`validation/character_export_fix_20261009/unity_native_orientation_review_success_20261010.json`
checks all 217 retained bones on FBX, Source and Player against a fixed authored
coordinate conversion: 651 world-bone and 3,255 mesh-bind comparisons. It also
checks absolute roots and anatomical forward direction during all four native
samples. No fitted common yaw or 180-degree scene rotation is used. Player's
existing small overrides are recorded (maximum axis difference 0.03387 degrees,
translation 0.29845 mm); there is no shared 180-degree bind or sampled-body flip.

## Exact local delivery

The 152-file projection is based on approved 0.77.12 and changes exactly
`__init__.py` (version only) and `unity_runtime/Editor/ForearmCorrectionImporter.cs`.
All Python behavior, the verified export worker and prior Arm Flat/IK repairs
are unchanged. Pending Dress/Cloth and unrelated repository work are excluded.
The unaccepted 0.77.13 candidate remains immutable evidence and was never deployed.

- Version: `0.77.14`; all 130 Python files parse.
- Projection SHA256:
  `17a62f31b1b7c71ced1add9aae18465363420a63942f777b9f51422af851b6eb`.
- Importer SHA256:
  `906fa8c3f9a21e2321cc0fa15d1701091b3b3176dd4a249ffdb8e24be017364d`.
- Official ZIP: `dist/character_designer-0.77.14.zip`, SHA256
  `a13ba79e2736b4b77fb955868c49ce7779d92f9819c7560b887d80a49433e1a4`.
- Official deployment to the verified Blender 5.2 add-on directory and
  `D:/Blender/Projects/Character/X/addons` updates two files at each destination;
  both `--check` results are zero differences.
- Previous files are backed up at
  `C:/Users/Randy/AppData/Local/CodexBackups/addon-deploy/20261010-055114-9aefe2e6`.
- X `validation/character_export_fix_20261009/delivery_07714.json` passes frozen
  and installed inventories/hashes, Unity importer identity, the exact six-file
  export bundle, five production GUIDs and protected X/Adventure disk hashes.
  This streaming disk check does not replace native acceptance.

The already-open Blender Python session remains 0.77.12. Its repaired behavior
is identical to 0.77.14's Python payload, so this importer-only delivery did not
refresh or save the artist again. Prior native protected saves remain FINISHED;
final X SHA256 is
`17a9312f6ec7f3ec0d709ca89fb2bc918aa94856af0fef544c3ce6ca83bded86`.
Unity Adventure remains unsaved with its original handle, 51 roots and disk
SHA256 `b301696228b50e7e2838749a29bfd3b3599db59531489b702eea3ada0477715e`.
No Git commit, push or public release was made.
Character's final native Avatar/source freshness, scene snapshot, retained
diagnostic replay/CrashReporter evidence and normal owned compiler-server
shutdown are recorded in `evidence/DELIVERY-FINAL.md`. The Unity lease is
released, mutations disabled, and Play/compile/import/reload/tests stopped.

## Validation limits

SessionState recovery covers assembly reload in the current Editor, not a
persistent queue across Editor exit. Animated evidence uses disposable previews
with the actual Animator; no live Adventure actor existed during acquisition,
so this does not establish Adventure Play-mode behavior or every animation.
The existing Blender Armature-before-Subdivision versus baked-Subdivision-before-
linear-skin difference remains: combined poses differ by up to 11.67 mm on Cosha
and 11.32 mm on Stocking. It is unchanged by this repair and outside the ordinary
LBS PASS claim. This is correctness validation, not an FPS improvement claim.

Actual model is X/Cosha; Blender is 5.2.0 LTS and Unity is 6000.5.9f1. Agent model
and reasoning effort are unrecorded. Investigate again only for new local
topology/reindexing changes, retained bind differences, Avatar ownership/source
staleness, failed related import/reload, or reproduced deformation in another
animation or an actual scene actor.
