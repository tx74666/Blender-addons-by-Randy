# Character Designer 0.77.12: export bind axes and Forearm Twist

The saved real Cosha character reproduced two independent defects: control-bone
filtering decomposed retained EditBone matrices and reversed four right-side
Rest rolls by pi; unrelated mesh connectivity edits invalidated an otherwise
unchanged Forearm Twist support region. The exporter now preserves native
head/tail/roll directly. Calibration validates exact support vertex IDs and
incident connectivity, accepts proven unrelated edits, and rejects local edits,
reindexing, and stale legacy records without a valid proof. Setup can perform a
cancellable recapture; Export validates copied metadata without rewriting the
artist record.

## Published local projection

The exact 152-file projection is based on approved 0.77.11 and changes eight
files: the version, six Forearm modules/UI files, and the narrow export Roll fix.
Pending Dress/Cloth, evaluation-copy cleanup, other animation work and RR Helper
changes are excluded. Prior Arm Flat/IK repairs remain included.

- Projection SHA256: `2c6e63bfe3919a2ed224da69aede0456315fcde35c49db187fcdf5872b8e34a6`.
- Worker SHA256: `fa04069126b3dd327b4f3d892f83cd8d2fc483348a0a1f13151bf2c7be5f4e3b`.
- Official ZIP: `dist/character_designer-0.77.12.zip`.
- Official deployment to Blender 5.2 and X/addons completed; both checks report
  zero differences. Artist runtime was refreshed through the normal protected
  add-on operation and confirmed as 0.77.12.

## Real-model evidence

Evidence is owned by X under
`D:/Blender/Projects/Character/X/validation/character_export_fix_20261009` and
`D:/Blender/Projects/Character/X/validation/forearm_twist_fix_20261009`.

- Old-worker exact saved-model reproduction fails: forearm.R, hand.R, thigh.R,
  shin.R rolls reverse by pi; additional connected-finger geometry and small
  hair-axis differences are also recorded. The reconstructed minimal fixture
  passes and is not evidence of the real failure.
- New-worker cleanup and actual Export preserve all 217 retained native bones.
- Native affected Forearm scope/setup/recovery/cache/export/follow/ownership
  suites pass. Fresh calibration on the actual model passes preview cancel,
  commit/reentry, bilateral 45/90 degree tests and independent forward skin
  checks. Original artist keys, geometry, weights, Rest, raw pose, constraints
  and actions remain protected.
- Current artist pair was recaptured using the verified current UI profile;
  all four old orphan keys remain intact. The new `.002` keys are record-owned.
  `live_fix_receipt.json` and `layout_restore_receipt.json` both report native
  save `FINISHED`. The previous Shader pane and artist context were restored.
- Final X SHA256:
  `17a9312f6ec7f3ec0d709ca89fb2bc918aa94856af0fef544c3ce6ca83bded86`.
  Disposable background re-open verification passes with matching full artist
  protection fingerprints (`saved_actual_verification.json`).
- Final staged FBX SHA256:
  `0e88a931b3943aef7ae1b59e12b1340479afc71dcd47c690e88cb574c3a9342c`.
  Its paired Forearm sidecar has 64 source samples, 428 subdivision stencils
  and a self-check error of 1.62e-7, below 3e-6. All 11 artist shapes, including
  the four old orphan keys, remain exported; only the two current owned keys
  are excluded. See `final_export_07712/forearm_sidecar_verification.json`.
- Independent binary bind/weight/shape and native FBX-reimport ordinary skin
  checks pass on the final export: 217 bones, five meshes, 11 artist shapes and
  19 poses. See `final_fbx_roundtrip_07712.json/.log`. Correction runtime is
  explicitly disabled for this ordinary skin gate. Earlier validation-only
  initialization/pose-parent failures are retained separately, with thresholds
  unchanged.
- The existing Unity production FBX independently shows approximately 180 degree
  X/Z bind-axis differences on forearm.L, hand.R, thigh.L and shin.R relative
  to the captured author Rest (`production_bind_comparison.json`). This is a
  separate historical input from the old-worker reproduction above.
- The final complete publication bundle is X
  `validation/character_export_fix_20261009/published_export_07712`.
  The shipped 0.77.12 `unity_export._publish` ran natively with exit 0, added
  `Cosha.cdesigner.json`, and verified normal inventory, ownership and Hair
  sidecar gates. Manifest SHA256 is
  `eeb8ff545f9c2d954d8ba1ecc355309c4e0f2f3bc3c40da5cd35c4f604ad9bec`;
  FBX and all original inputs/worker outputs remain unchanged.

## Unity status and limits

Character has now published the complete corrected bundle and Unity ownership
repairs into Assets under sole editor ownership. Native ownership/injected
rollback tests pass; five shared QA fixtures have private Avatars and repeat
repair changes zero. The production Avatar retains its original GUID.
Actual Animator Idle/Walk on Source and Player passes all four strict
correction-enabled samples, with matching saved/current Source dependency
hash. Separate correction-disabled provenance and actual current-model Forearm
checks pass. Fixed author-coordinate world/bind/absolute-front comparison
excludes a common 180-degree workaround. No live Adventure actor existed during
acquisition; animated evidence is from owned disposable actual-Animator previews.
Adventure's dirty state, handle, 51 roots and two original previews remain
intact. This native validation supersedes the earlier offline-only status.

The final importer-only 0.77.14 release additionally passes genuine reload
recovery of the same pending production sidecar, generated data naming and six
metadata identity/hash protection. It is built and deployed to verified Blender
5.2 and X, with zero differences at both destinations. The already-open Blender
Python session remains 0.77.12 with identical Python behavior. See
[0.77.14 delivery and native receipts](CharacterDesigner_0.77.14_unity_import_recovery_20261010.md).

The combined-pose modifier-order diagnostic records an existing fidelity limit:
linear Armature followed by Subdivision in Blender differs from baked Subdivision
followed by normalized linear skin by up to 11.67 mm on Cosha and 11.32 mm on
Stocking. This diagnostic is outside the exported ordinary-LBS PASS and must
remain visible alongside the actual Unity animation comparison.

This is a correctness repair, not a new performance measurement. Blender is
5.2.0 LTS, the scene is actual X/Cosha; agent model/reasoning effort are
unrecorded. Follow-up triggers are local forearm edits/reindexing, export Rest
differences, Avatar ownership/source staleness, or a reproduced Unity
deformation in a different animation or actual scene actor.
