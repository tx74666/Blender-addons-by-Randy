# Character Designer 0.77.50 · Rebuild Foot Controls

After changing the native foot bones, **Rig > Fix > Rebuild Foot Controls**
rebuilds the existing foot-control setup for **Both**, **Left** or **Right**.
It fits the foot pivots to the current native foot and toe geometry without
requiring a whole-body regeneration or a new skin bind.

The operation preserves the native bones and their Rest, model geometry and
weights, finished hand setup, and the unselected side. It restores the custom
foot widgets' geometry, display names, bindings, colors, materials, display
transforms and saved fit backup. The rebuilt Foot widget objects and meshes
receive new native IDs; their old datablock identities are not retained.

The base leg IK target and Pole are not rebuilt, and their raw animator inputs
are restored. This operation does not choose a new knee Pole plane or repair
native bone roll. A reversed right-leg bend still requires a separate diagnosis.
Moving the whole character to the ground remains a separate **Ground Character**
action; choose the intended standing pose before grounding.

## Approved release input

Runtime changes are developed in the canonical repository. The release is a
complete frozen projection of accepted 0.77.49 with four changed runtime files:
`foot_controls_rebuild.py`, `rig_fix_ui.py`, `body_setup_transaction.py` and
package registration/version changes in `__init__.py`: **201 shipped files**.
The paused 0.77.48 source-watch, regional-proof and display-check optimizations
remain excluded. This release makes no playback-performance claim.

Evidence and reproduction:
`D:/Blender/Projects/Character/X/validation/foot_rebuild_20261018/`.
Build, deployment and `--check` must use the same
`release02/release_projection.json`, SHA256
`e89d963bcfd499d54a396fe10e10e3a651f0060af4385d9bad506c3153327c96`.
`RELEASE_PROJECTION_MATCH` means equality with that complete frozen package,
not the canonical working tree containing the paused changes.

## Validation

Validation uses isolated Blender 5.2 scenes and the exact frozen `release02`
package. The earlier source-override probe remains intermediate evidence;
the final current-model result is `artist_frozen_report.json`.

The exact frozen `release02` package passes nine native fixture tests in
14.8 seconds, including deterministic Object, Mesh and Collection RNA lifetime
checks and rollback after a partially completed two-foot rebuild. Ten separate
shared-transaction checks pass in 6.9 seconds, and 14 pure UI checks pass.
An additional public-operation Undo/Redo check passes, making ten focused
foot-rebuild native checks in total.

The first frozen candidate exposed a rollback failure when Blender reused a
native pointer for a newly created resource. A pointer-only presence check could
mistake that resource for the removed original, skip cleanup and then fail to
recover an occupied collection. The transaction now retains strong references
to the original RNA resources and checks that those original references remain
valid. The initial failed frozen package, manifest SHA256
`633bb1a6df31692c113c7298d6c1fd3666b18f0ddaadb2921ea4355f19a5b6b6`,
and its failure logs remain preserved separately.

The formal current-model run passes rebuilding, small Foot Roll/Toe motions on
both sides with opposite-foot isolation, a repeated public operation, and
saving/reopening its disposable result. Maximum foot-anchor error changes from
approximately 0.0141563 m before rebuilding to 0.000025431 m after rebuilding
and reopening. Maximum evaluated surface difference is 0.000042537212 m,
within the 0.0001 m check limit. Raw weights, Shape Keys, native Rest, protected
hand state, custom widget geometry/display settings and historical Action
fingerprints remain unchanged.

The isolated saved result is `verified_foot_rebuild.blend`, SHA256
`96dbcbf02388acaed88367569eeef5384e338c6c95e389c019d42924849aaf39`.
The artist file remains unchanged, with before/after SHA256
`8d25e5ef2dc158b42acbeeb41ccdba7ed4aaff068cd55b22fd57118ebb792581`.
These checks establish local foot-control behavior; they do not establish a
full gait/contact acceptance or a right-leg Pole repair. Evidence summaries
retain comparisons and input/version pins without repeating complete mesh or
forearm author arrays.

## Local delivery

The built `dist/character_designer-0.77.50.zip` has SHA256
`e174a6105cbba94812692236ed8a8932fd97f1baed65efea103ef6abd48203d6`.
The approved package is deployed to the verified Blender 5.2 installation and
X's add-on copy: 201 files, four updated files per target, with the same
projection's final `--check` reporting zero differences for both targets.
Previous files and the deployment receipt are retained at
`C:/Users/Randy/AppData/Local/CodexBackups/addon-deploy/20261018-041348-87043245`.

The live artist window, PID45600, was not refreshed, modified or saved by this
delivery. Use **Refresh Add-on** or reopen Blender to load the installed code;
installation does not apply a foot rebuild or grounding to the artist scene.
Changes are local only. No Git commit, push or public release was performed.
