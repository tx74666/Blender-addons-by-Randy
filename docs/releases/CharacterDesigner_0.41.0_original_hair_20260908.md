# Character Designer 0.41.0 — original hair binding

Validated and deployed on 2026-09-08 with Blender 5.2.0 LTS, hash fbe6228777e7.

## User-visible result

The Hair Bones panel now binds the original mesh directly to the character's
existing Armature. It creates one independent deform chain per strand under
Head and assigns the validated cap/root region to Head with weight 1. There is
no generated mesh copy, private hair Armature object, or extra attachment bone.
The source's existing rig takes priority; otherwise a nearest humanoid Head is
selected, with an explicit target override and refusal of equally near choices.

Bind Hair to Character and Remove Hair Binding replace the version-generation
flow. Removal restores the operation's original weights, parenting, Armature
modifier state/order and Mirror settings. The manifest survives save/reopen;
nondeforming artist masks, geometry and shape keys are retained. Geometry,
ownership and external-dependency checks run before destructive edits. Custom
hair-bone animation/constraints must be removed or transferred before removal;
the character's unrelated animation is preserved.

Cleanup Generated Copies is an explicit migration action. It checks collection,
mesh, bone and rig ownership, including private rigs no longer linked to any
collection. Blender ID references protect Geometry Nodes inputs, collection
instances, drivers and other external uses. An interactive cleanup first writes
a recovery blend file. Original sources and the main character rig are excluded
from the deletion set. Legacy data readers remain available for old files.

## Validation

Canonical background suites passed with factory startup, file auto-execution
disabled and `--python-exit-code 1`:

- `test_hair_bones_binding_blender.py`: 8 tests covering no-copy binding,
  cap/root/mask weights, repeated removal, existing bindings, Mirror,
  target resolution, posed Head refusal, dependencies and saved removal.
- `test_hair_bones_existing_mirror_binding_blender.py`: 5 tests for both initial
  Armature/Mirror orders, independent controls and exact failed-build rollback.
- `test_hair_bones_binding_guards_blender.py`: 5 tests for replaced modifiers,
  corrupt restoration data, injected mid-restore failure, body animation
  preservation and refusal to destroy hair animation.
- `test_hair_bones_cleanup_dependencies_blender.py`: 8 tests for external
  Geometry Nodes, collection, material, node-tree and Scene references,
  including an unlinked private rig with a real Scene driver.
- `test_hair_bones_ui_blender.py`: 4 UI journeys including retired RNA/operator
  removal, direct binding/removal, old-entry redirection, source resolution
  through owned pose bones and explicit cleanup wiring.
- `test_hair_bones_legacy_compat_blender.py`: a real 0.40.2 ZIP-generated Grouped
  file remains readable; its native captures bind and unbind on the original
  mesh without creating another version, while old actions/weights/guides stay.
- `test_hair_bones_rig_blender.py`: 9 existing core tests including native
  addon-free reopening.
- `test_addons_together_blender.py`: both registration orders passed.
- `test_forearm_twist_addon_enable_blender.py`: enable, disable, deferred
  cleanup and calibration reload checks passed.

In an unsaved private copy of X, cleanup removed exactly 2 mesh copies, 2 private
rigs and 1 Mirror helper. Original Hair3 then completed two bind/remove cycles:

| Measurement | Result |
| --- | --- |
| Original Hair3 base vertices | 469 |
| Captured half-mesh strands | 7 |
| Full independent chains | 13: 6 left, 6 right, 1 center |
| New bones in CoshaRig | 52, under existing Head |
| Uncaptured validated cap vertices | 162, Head weight 1 |
| Maximum rest bind displacement | 4.86e-7 |
| Maximum Head-follow error | 6.03e-7 |
| Original mesh/Armature object counts during binding | Unchanged |
| Original main bones after removal | All 56 restored/preserved |

## Current X migration

Refreshed the installed plugin in the actual X Blender window, ran the explicit
cleanup action, then bound the original Hair3 to CoshaRig / Head with four bones
per chain. The UI reported 52 selected hair bones out of 108 total character
bones and displayed Remove Hair Binding. Saved the current `X.blend`.

Recovery file created before deletion, including the live unsaved scene:
`D:\Blender\Projects\Character\X\.character_designer_backups\X-before-hair-copy-cleanup-20260908-145318-971885.blend`.

A separate process loaded the saved file using the installed plugin and saved
user preferences. It confirmed version 0.41.0, original Hair1/Hair2/Hair3 present,
zero old version copies, 13 chains/52 bones on CoshaRig, Head weight 1 on all 162
cap vertices, and zero Forearm Twist errors. Other installed polygoniq modules
printed log-rotation file-lock messages because another Blender was running;
Character Designer checks and the process exit succeeded.

A further read-only comparison against the recovery file verified original
Hair1/Hair2/Hair3 coordinates, topology, data names and shape keys, and all 56
original main-bone rest/pose matrices. Removing the binding from the saved
scene in that background process restored original Hair3 weights, no parent,
no Armature modifier and exactly the original main bones. Neither input file
was saved by these verification processes.

Saved X SHA-256:
`c5ef08458b89dbd6110a87f6f96eb5e24ad91e05c86a0a16f9f16b47a3bb7b51`.

## Release

- `dist/character_designer-0.41.0.zip`: 26 files.
- SHA-256: `7e9d5937cd788f40c33cb8e8991ebc657a9e777f4bfafb4c2eaef886d7db1492`.
- Built with `python tools/build_releases.py`.
- Deployed with `python tools/deploy_local.py --project-addons D:\Blender\Projects\Character\X\addons`.
- `--check` found zero differing files in the Blender installation and X
  validation copy. RR Helper 0.2.5 was unchanged.
- Previous plugin files are backed up in
  `C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260908-145216-2a769ad2`.
- `git diff --check` passed. Code/release changes remain local, uncommitted and
  not pushed. Shared procedural hair motion remains a separate next stage.
