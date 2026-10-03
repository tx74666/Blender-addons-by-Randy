# Character Designer 0.69.3: Readable generated names

Local source change, packaging and deployment on 2026-10-02.
No Git commit, push or published release.

## Behavior

Generated foot, torso, eye, spine, root, FK, head/neck and body-detail widgets
use `WGT_<character>_<function>` with side labels and sequential torso FK names.
Skirt objects, data, new bones and weight groups use `SK_<source>_<function>`.
Examples: `WGT_CoshaRig_Foot_Roll.L`, `WGT_CoshaRig_Torso_FK_01`,
`SK_Dress_Wire_01`, `SK_Dress_Hem_01_Shape`.
The skirt helper collection names its source instead of displaying an ID.
Ownership UUIDs remain in internal properties and recovery records.

Blender collision-resolved names are written back immediately. Widget creation
never reuses an artist collection merely because its name matches. Automatic
numeric suffixes resolve generated names and collections without replacing
artist resources. Source vertex-group prefix collisions reserve a numbered
skirt prefix before creating bones or assigning weights.

On registration and file load, verified legacy widget/skirt object, data and
collection names are cleaned atomically with their typed JSON references.
Failures roll back names and records. Cleanup keeps object/data identities,
visibility, links, geometry, weights, Shape Keys, animation and pose state.
Linked/shared data, edited ownership and manually named resources are preserved.
Existing bones and constraints keep their names to protect animation paths.
Body IK's already readable `WGT_Randy_*` resources are unchanged.

## Validation

- 7 targeted native name-migration checks pass: Unicode/name budgets, artist
  vertex-group reservation, multi-module migration, artist name collisions,
  edited Root and Foot mesh ownership, creation collisions and exact rollback.
- Existing Root (7), FK (6), head/neck (4), body-detail (5), widget-collection (5)
  and UI pages (6) checks pass. These include removal, rollback, save/reopen and
  unchanged page routing. The skirt service integration checkpoint also passes.
- Saved X.blend: 117 object/data/collection names cleaned in memory, across
  10 widget resource groups and one skirt. Object/data identities, scene links,
  transforms, rest/pose state, constraints, geometry, weights, Shape Keys,
  Actions/slots/NLA/drivers are unchanged. Typed resource references validate;
  a second cleanup is empty. The input is never saved. SHA-256 before/after:
  `12f15f34b94c408c8d2f11b87027b0a8cf5cdfc44c4b4d1d90d9c425da90ad84`.
  Evidence: X project's `validation/generated_names_20261002.json`.

## Local release

`dist/character_designer-0.69.3.zip` contains 125 files. SHA-256:
`64a468e578b93daa2c2dd49475cf0a85efd734d2a82009594fd7153e69cc2c47`.
Blender 5.2's installed add-on and X's validation copy match all 125 canonical
files; module-scoped deployment `--check` reports zero differences.
Background validation processes exited; the two existing user Blender sessions
remain open. Reload the add-on to apply the new naming and legacy cleanup there.
