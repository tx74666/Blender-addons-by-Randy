# Character Designer 0.77.19: animation scene feedback

The animation panel now displays the current Scene name. Import Prepared's
tooltip explains that it uses the workspace's existing animation rig when
available, or creates a separate animation Scene and imports the character.
When an import batch completes, its message retains the actual successful/total
animation count and adds the actual Scene name, identifying whether the batch
created or used that Scene.

The created/used label follows what the first successful import actually did.
In particular, reusing a legacy owned rig is reported as using its Scene even
when the saved workspace rig reference was initially unset. A batch with no
successful imports does not claim that it created or used a Scene. A removed
Scene cannot provide a name, but the completion path still performs its existing
queue cleanup.

## Workflow

Import Prepared continues to create or reuse an animation Scene automatically
through its existing import route. There are no new Create or Remove controls.
Existing Custom edits and the active animation retain the existing protection;
this change does not migrate them to a different Source, rebuild cached Sources,
or change the import/data lifecycle.

Artists may delete a Scene through Blender's native Scene controls. Native data
ownership and lifecycle still apply. This release adds no Scene deletion,
orphan-data purge, workspace-link cleanup, or automatic reimport feature.

The bone-length recovery introduced in 0.77.17 remains present, along with the
0.77.18 Shoes Quick Bind fix. Bone-length recovery still requires a fresh CD
export with its adjacent `.cdesigner.json` and a new CD Source import; existing
cached Scenes are not retroactively repaired.

## Validation and limits

The frozen release input passed 142 tests with zero failures or errors. Evidence
is in X's `validation/animation_scene_feedback_20261010/tests.json` and
`tests.log`, with the reviewed release delta in `changes.diff`.

These checks cover the panel/tooltip contract and collection completion
feedback, including Scene creation/reuse and successful import counts. No actual
Unity FBX import or Unity Editor roundtrip has been performed for this release.
This feedback change does not establish new correctness evidence for character
imports or change the prior validation limits of 0.77.17 and 0.77.18.

The 153-file archive was built with `tools/build_releases.py --projection` and
deployed with `tools/deploy_local.py --projection` to Blender 5.2 and X's add-on
copy. Both targets passed the final `--check` with zero different files. The
deployment updated three files and preserved the other baseline files.

A disposable Blender 5.2.0 LTS factory session enabled the installed 0.77.19,
verified the registered Import Prepared description, drew the Scene label,
checked the existing bone-length restore hook, and disabled the add-on normally.
The process exited successfully. See `installed_addon_smoke.json` and
`installed_smoke.log` in X's evidence directory.

Refreshing the already-open artist session was not confirmed: the shared
Computer Use helper reported an active request, and a later read-only screenshot
showed another application over Blender. No further input was issued after that
observation. The installed runtime check does not establish that the open
session has loaded this version; use the add-on's Refresh Add-on control if
offered, or reopen Blender to load the installed version.

No artist Scene data operation or save was performed. X.blend retained its
pre-task disk SHA256 `9b0ed027e3f7ac89a17f654190ffc842ec8e862db2188a658a4b7e0c074b202c`.

## Release scope

The complete 153-file projection starts from the verified 0.77.18 release and
changes exactly three files:

- `__init__.py`: the version declaration only, to 0.77.19.
- `animation_worklist_ui.py`: current Scene label and Import Prepared tooltip.
- `animation_worklist_collection.py`: actual created/used Scene completion
  feedback alongside the existing import count.

The two worklist files were developed in the canonical add-on source. Before
this task's edits, both matched the deployed 0.77.18 bytes. The release freezes
those two reviewed canonical files while retaining every other baseline file.
It excludes unrelated pending Dress/Skirt, animation export, Unity export and
material-matching changes in the shared repository. Canonical `__init__.py`
contains unrelated pending changes, so the release uses the baseline file with
only its version updated.

- Baseline manifest:
  `D:/Blender/Projects/Character/X/validation/shoes_binding_20261010/release_07718/release_projection_07718.json`.
- Baseline manifest SHA256:
  `6ab500acf3fe43528df763a73b4c20c53b9e7598de91f01fbeebd358c1fdbd7b`.
- New manifest:
  `D:/Blender/Projects/Character/X/validation/animation_scene_feedback_20261010/release_07719/release_projection_07719.json`.
- New manifest SHA256:
  `1614071a766cb9d5d9a4e8565ef549012e353546b463f2e9f2118cceb81f368e`.

The local release archive is
`D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.19.zip`.
No Git commit, push, PR, or public release was performed.
