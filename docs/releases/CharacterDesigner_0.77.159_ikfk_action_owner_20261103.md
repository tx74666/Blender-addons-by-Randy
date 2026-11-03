# Character Designer 0.77.159 — keyed IK/FK Custom ownership

Keyed IK/FK switching previously copied the active Custom Action without
publishing its replacement in the animation worklist. The rig then used
`Current Rig.003`, while the Custom row and historical checkpoint still owned
the parent Action. Alt+R correctly refused this mismatched owner with
`Select this rig's editable Custom Action and slot; Source remains read-only.`
Ordinary R rotation follows Blender's separate transform path.

Managed single-limb and global keyed switches now share one ownership
transaction: stage one complete Action copy, key the requested limbs, then
publish one new Custom row, exact slot, revision token and independent checkpoint.
The checkpoint explicitly references its current Action so existing wrist
ownership readers can authenticate it. No-key switches and repeated unchanged
mode requests do not create a new Custom version. Source, the original Custom,
old rows and fixed historical checkpoints remain intact.

Explicit recovery admits only the isolated local clone with its verified parent
row, inherited token/checkpoint, exact slot and known users. It copies the
current clone into a newly owned Custom; it does not activate the old Action or
discard subsequent edits. Foreign users, ambiguous ownership and stale
frame/Action/context refuse recovery and retain the recovery IDs.

## Pose and recovery protection

The frozen15704 implementation exposed a real deferred-evaluation regression:
binding a copied Action could later replace 59 unkeyed channel values when
Blender refreshed. It is not an accepted complete release.159 preserves the
current raw overrides through copied-key evaluation and publication, retaining
the fresh post-publication association. The actual private input reports zero
raw-channel and Rest differences after a further native update. All157 failures
and their original results remain retained.

Owned Pole support is limited to captured, strictly authenticated native IK
`pole_angle` scalars, including their finite RNA range and exact paths. It does
not grant arbitrary constraint writes or expand old checkpoints. Rollback
restores the fresh Action/slot, pose, Pole inputs, display and owned outputs;
new row/checkpoint/Action resources are removed only under their ownership
guards. Separate fresh RAM witnesses preserve collection member order and the
rig's display-cache entries across both Controls and Original rollback layers.

The canonical three-dimensional HAND geometry, native FK hand channels and
all approved156 protections are inherited unchanged. Unrelated controller and
offset candidates are excluded.

## Package and validation

Complete 159 extends approved 156: 258 files/226 Python files; five changed files,
one new `limb_ik_fk_action_owner.py`, and 252 exact inherited files. The existing
target/revision increments are the reviewed owned-Pole spans only.

- Projection: `stage159_01/projection.json`, SHA256
  `e6f230ea1acbdcc511f9cf3dba6c519c44ce6e57b1dce4672b39ec172aedd5e4`.
- Official ZIP: `character_designer-0.77.159.zip`, SHA256
  `17680ebdf616f36fc8766eecbfe246669fbaaf934fba5c3f36d23d3edfbd27ec`.
- Official build, ZIP CRC/exact 258 and Blender 5.2/X deployment with matching
  `--check` passed: `package_delivery159_01.json`.
- 19 pure tests, 10 native component methods and 8 narrow owner-leaf adoption
  checks passed separately: `component_acceptance159_01.json`.
- The pinned current-scene private recovery passed full revision/wrist-owner
  admission, complete parent/orphan Action fingerprints, current frame15 raw
  pose and selected Hair4 context preservation, and registered left Alt+R with
  AutoKeyFalse: `actual_recovery_acceptance159_01.json`. AutoKeyTrue is covered
  separately by the native component checks.

The private library readback lost local Action fake-user flags. Its initial
preflight failure remains; fixture rehydration used the exact observed live
flag manifest and checkpoint record hash. This is a documented private input
derivative, not a product permission repair or an artist flag change. Other
original Actions use the stated identity/metadata/slot/curve-ID/count witnesses;
no complete all-Action curve-rescan claim is made for the actual recovery run.

## Completed artist repair and save

Artist36008 adopted only the scoped functions/defaults and owner leaf; package
metadata156 remains. Freshly authenticated current orphan `.003` was copied into
`IK/FK Repair`, with its own row, token and independent checkpoint. Current raw
pose/display inputs and complete motion fingerprints were exact, while original
rows, IDs, Source, parent Action and checkpoint remained intact. The live wrist
owner check passed. The latest Pose/frame15/Hair4 selection and active Hair23.01
were retained. No physical artist wrist reset was performed.

One normal X.blend save returned native FINISHED; the later UI event observed
dirtyFalse and unchanged context. Private readback of that normal mainfile passed
full revision and wrist ownership, naturally retained Action/checkpoint lifetime
flags with zero rehydration, both curved HAND bindings and all four IK curves at
frame14. Frame15 follows its existing curves (left arm IK, other limbs FK); this
does not claim persistence of arbitrary unkeyed mode overrides after reopening.

Saved file SHA256 `59883d4803f96c4959b4ade4ce61f4c68576196b3ea7d7a2ea209b25036c8338`.
Complete acceptance: `D:\Blender\Projects\Character\X\validation\wrist_reset_owner_20261103\acceptance159_01.json`,
SHA256 `9bbca40ccfd3f7cd265b5cc03e1a937034460600489084818293d1430ac901cf`. Saved readback/exit/cleanup:
`D:\Blender\Projects\Character\X\validation\wrist_reset_owner_20261103\artist_saved_readback159_01.json`.

The entry receipt-key assertion failure occurred before helper invocation and
scene writes; its original script and corrected derivative remain. A finalizer
JSON-array assumption also failed before acceptance creation and has a retained
derivative. All earlier feature, fixture and deferred-evaluation failures remain.
Owned test workers exited normally. No preferences save, full reload, application
restart, full Undo lifecycle, whole-animation conversion, performance result,
Unity work or Git upload is claimed. Model and reasoning effort were unrecorded.
