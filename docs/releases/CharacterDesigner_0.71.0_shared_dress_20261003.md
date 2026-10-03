# Character Designer 0.71.0 — Dress in the Main Rig

Date: 2026-10-03 (Asia/Shanghai). Owner: X. Validated and deployed locally;
the artist X scene has been migrated and saved. Execution model: GPT-6.1 Sol,
ultra, verified from this X task's turn-context metadata. No commit, push, PR
or remote publication has been performed.

## Problem and resulting workflow

Dress previously had its own armature object even though Body/Hair used CoshaRig.
Bone Collections belong to an individual armature, so Dress was absent from the
character's native collection list and posing required another object.

Attached Dress setups now use a managed subset of bones inside the Main Rig.
Body/Hair/Dress remain native Bone Collections. Existing independent setups have
one explicit **Use Main Rig** operation after leaving Original mode. This
changes the rig architecture; it is not a measured viewport performance gain.

## Implementation boundaries

- The source mesh owns the record and exact Dress bone subset. The Main Rig is
  never tagged as a whole skirt-owned object or data block.
- Bone names, mesh topology, Shape Keys, UVs and painted weights remain intact.
  Native skin modifiers, constraints, curve hooks and generated driver targets
  move to the character. Existing character animation is retained.
- Rest-space conversion preserves the Hips attachment. Positive uniform
  relative scale requires conversion of bone lengths, pose translations,
  custom-shape frames and Hook inverse matrices together. Nonuniform scale and
  shear remain unsupported. The artist's object transforms are not applied.
  Pose rotation modes require explicit preservation during retargeting.
- A failed migration restores the old rig, references, record, display and
  context. The old object is removed only after validation and record checks.
- Generated skirt cleanup removes only its owned subset and helpers. External
  dependencies must be rejected before removal, and the character remains.
- Body native inventories exclude owned Dress bones. Original return preserves
  Dress pose channels. Weight Paint and native auto-weight calls use the mesh's
  own bone domain and restore temporary Deform flags on every exit path.
- Existing independently animated or physics-enabled skirts and unverified
  custom dependencies remain independent until a compatible migration exists.
  New shared setups use the existing physics/bake tools.
- Migration requires one effective vertex-group skin binding. Bone envelopes,
  disabled skins and duplicate bindings that would change deformation are
  rejected before any mutation.

## Verification evidence

The artist scene was backed up before any migration:
`D:\Blender\Projects\Character\X\Backups\DressSharedRig_20261003\X_before_dress_shared_rig.blend`.
Its SHA-256 matched the saved X.blend:
`8B43873AE5921FB87FF1AC62F067CA6FD9124C9D4C6437CE450223DF0CFEF345`.

Background validation and raw failed attempts are preserved in X's `validation`
directory. The final migration suite passed 12 tests, including uniform scale,
animation/subframes, rollback, Undo/Redo, save/reopen, multiple owned Dresses,
weight-domain isolation and unsupported-binding refusal. The safety suite passed
7 tests / 21 dependency subcases; shared physics passed its two-frame bake,
save/reopen and subset-removal case, retaining artist Bone/PoseBone properties.
Existing Original (18), display (14), attachment (7), weight-domain (7), selected
weights (34) and weight workspace (6) suites passed. Legacy skirt-service,
physics and UI workflow scripts also passed. A workspace test now compares the
saved view layer's snapshot in that layer, while separately protecting the
artist's active alternate layer. All runtime syntax and targeted diff checks
passed.

The actual saved X has Main object scale 0.7823157 and a Dress-to-Main rest-space
scale of 0.9330823. The latter has column spread 2.87e-7 and normalized
orthogonality error 1.94e-7, supporting a positive uniform conversion rather
than treating the Dress frame as rigid. The read-only measurement is in
`validation/shared_dress_real_transform_20261003.log`.

The existing Original-to-Controls step, before migration, produced a maximum
Dress evaluated-mesh difference of 0.084127 mm. Keep that baseline separate
from migration results. In synthetic migration probes, cage positions differed
by less than 7.2e-7 world units, whereas Spline IK could amplify the changed
floating-point inputs to approximately 0.000218 world units in the evaluated
mesh. Raw meshes, Shape Keys and pose channels remained exact. Unchanged Edit
round trips and ordinary updates produced zero drift; these bounds are specific
to the migration and do not imply that every reevaluation drifts. Evidence:
`shared_dress_stability_probe_20261003.log` and
`shared_dress_bounds_20261003.log`. Final runtime bounds and real-model results
are verified separately below.

Runtime validation retains exact artist mesh inputs, Body/Hair rest and pose
channels, and mapped Dress channels. It bounds cage positions to 2e-6 of source
extent and control endpoints to 3e-6 of extent, with control axes within 1e-5
radians. Spline IK output is checked separately: evaluated mesh/endpoints must
stay within 5e-4 of source extent and normalized axes within 1.2e-3 radians;
relative solver scale stays within 7e-4. These bounds cover the measured real
Cosha and 4-chain/3-segment numerical cases; they are not a guarantee of bitwise
identical evaluated poses on every Blender version.

The real saved Cosha verification passed: 236 character bones + 115 Dress bones
became 351 on CoshaRig. Body native inventory stayed 56, Hair 128 and Dress 33.
All raw mesh/Shape Key/UV/index/group-weight content and existing Body/Hair rest
bones and channels matched exactly. Other character meshes had zero evaluated
geometry change. Dress evaluated mesh changed by at most 0.102361 mm; control
endpoints differed by 0.0002404 mm, and cage differences remained below that
control bound. All 48 Hooks and 32 generated driver paths were rewired. The
candidate saved and reopened with valid ID references.

Cleaned legacy and shared export skeletons both retained 184 character + 33
Dress bones, with identical names and parents. Export rest-matrix difference was
at most 1.87755e-5. No Unity assets or production FBX were overwritten.

Primary evidence: `validation/shared_dress_real_verification_20261003.json`,
`shared_dress_real_attempt6_20261003.log`,
`shared_dress_migration_tests_attempt11_20261003.log`,
`shared_dress_safety_tests_attempt1_20261003.log`,
`shared_dress_physics_tests_attempt4_20261003.log`, and
`shared_dress_skirt_ui_attempt2_20261003.log`.

## Deployment and artist save

The 0.71.0 local zip contains 131 files, 980720 bytes, SHA-256
`DA7777A086BDF9FB65BFA0AFBA17EFA891E552ADF4C916D446977054911B2149`.
At 02:30:51, canonical code was deployed to the Blender user installation and
X's validation add-on copy. Both `--check` results had zero differing files.
Deployment backup: `C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20261003-023051-a35ec634`.

After the user paused input, the current X window refreshed successfully,
returned to Controls and ran **Use Main Rig**. The UI displayed 351 bones,
removed the independent SK_Dress_Rig object and showed native Dress collection.
Dress off/on and Original -> Controls -> Original were checked in the current
window. Final state is Original, Body/Hair/Dress visible, CoshaRig in Pose Mode.
Blender confirmed Saved "X.blend" and the title's unsaved star disappeared.

`D:\Blender\Projects\Character\X\X.blend` saved at **02:36:31**, 32270786 bytes,
SHA-256 `DD862ECE54CFB86640B218A88FC71E067E6797663905B4FD4E43E5C2FFFFF899`.
The pre-migration backup remains available. This is an architecture/correctness
change; no new viewport FPS or GUI switching-time gain has been measured.

An isolated readback of the actual artist save also passed:
`validation/shared_dress_saved_readback_20261003.json` and its log. It compared
the pre-migration backup against X.blend, found all raw meshes/Shape Keys/UVs/
vertex indices/group-weight mappings and all existing 236 rest bones exactly
equal, and reported zero changed pose channels for Body native, Hair native and
every other existing bone. The saved shared record resolves to CoshaRig with
115 owned Dress bones, 33 native skin bones and Original active. The check did
not alter either blend file or the current artist window.

The RandomRealm2 Character chat confirmed that previous model exports already
merged the 33 Dress skin bones into CoshaRig. Compare the actual cleaned legacy
and shared skeletons before assuming Unity Transform paths changed. Source-rig
provenance in sidecars changes; existing FBX and Unity assets remain untouched
during Blender migration.
