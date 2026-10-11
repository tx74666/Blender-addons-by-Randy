# Composition weapon display validation — 2026-10-11

Canonical implementation: `addons/character_designer/animation_composition_weapon.py`
and `animation_composition_weapon_geometry.py`. This is source-only handoff
evidence. No new Blender native result, deployment or artist save is claimed.

The prepared composition seals the actual Unity weapon-context file SHA. The
Blender reader verifies the FBX, prefab, both importer metadata files and every
declared physical source dependency against its captured path, GUID and SHA.
Unity owns complete AssetDatabase dependency inventory recomputation; the
Blender reader checks the immutable inventory and physical files. Both source
roots are mandatory. Actual Assets files cannot be declared virtual exclusions.

Enabled prefab renderers must refer to the captured actual FBX mesh source.
Their triangle-referenced positions and renderer-local-to-prefab matrices
exclude the prefab root TRS. A bounded surface comparison measures the raw
Blender FBX import against this actual Unity geometry. It requires a unique
orthogonal unit/axis conversion, checks both directions, and rejects ambiguous
symmetry or foreign/nonuniform geometry. It does not infer a forward axis from
a filename or mount a guessed placeholder.

The mounted local chain is native-bone authored-Rest conversion, declared
weapon-local matrix, captured spawned-prefab matrix and measured import
conversion. The real prefab scale is applied once. A second direct surface
comparison measures placed geometry in metres without fitting away a bad
mount or repeated prefab scale. Scene unit system and scale are captured,
restored, and checked immediately after FBX import before the geometry proof.
Original Rest, evaluated pose, Action contents/slot and timeline are protected.

Actual authored grip targets, if present, display relative to their declared
packet parent bone. Missing named grip targets remain an explicit limitation;
this display installs no arm IK or automatic hand correction. Supported FK
controls remain the authoring inputs.

Cleanup requires a generated UUID4 ownership identity, explicit matching tags
on every native object/collection/asset, original Scene and collection links,
exact object set, and no external users. It removes only the captured owned
objects and proved new zero-user imported assets. Shared assets remain. There
is no global orphan purge. Import failure rolls back its own new data and
restores the working context.

Pure coverage currently includes nine geometry tests, thirteen native-ID mock
ownership tests and eight physical-source dependency tests. These tests do
not prove real Blender FBX import, native ID-property persistence, collection
user counts, Full Stack Tweak compatibility or actual Blaster appearance.
Those require the dedicated saved-artist-copy acceptance fixture, freshly
captured Unity context, explicit shared idle window and current memory read.

The ownership/unit suite is saved as
`tests/test_character_composition_weapon_ownership.py`. It exercises the actual
canonical guards with mocked native ID boundaries, including missing ownership
tags, Scene relinking, an injected artist zero-user asset, exact unit restoration,
and importer unit mutations rejected before geometry measurement. Import failure
retains prior objects and disposes only the newly imported IDs in these fixtures.

Source/runtime target: 0.77.25, Blender 5.2.0 LTS. Model and reasoning effort
are unrecorded. The completed 0.77.24 original-Rig return and artist save remain
separate verified evidence. Git commit and push remain with the user.
