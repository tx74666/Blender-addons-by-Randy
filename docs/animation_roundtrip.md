# Single-Action animation return

Status, 2026-10-01: accepted for the Character Designer **0.68.0 local release**.
The linked Cosha Walk edit completed Blender Sync, Unity Quick View and private
Apply/Restore acceptance. Production Cosha remains on Default; the demonstration
candidate is unapplied. Local deployment verification is recorded separately
after the release tools run. This is separate from the older animation-library draft.

The reference-first-frame correction passed the three-case Blender regression
for native FK, controls and a reloaded rig/Action-only library snapshot. The
corrected revision 4 also passed actual Unity acceptance.

The initial September 30 Unity `Walk_N` packet exported successfully, preserving existing assets
and scenes. Preparing the Blender examples then correctly stopped at the strict
bind-skeleton check: six finger Rest positions differ between saved X
(2026-09-30 19:05 local) and the Unity model (07:52 local), with a maximum fitted
residual of 10.153 mm. Evidence is in X's
`task_artifacts/animation_roundtrip/bind_comparison_from_inventory.json`.
The agreed Link route imports the exact Unity model into an independent Blender
editing scene, preserving its rest skeleton and the existing X authoring rig.
The strict guard stays in place. Fourteen pure Link contract tests and the registered
Link/Sync UI contract passed. The fresh linked model imported all 217 bones and 59
motion samples, with a maximum bind residual of 0.000455 mm and source pose matrix
error of 0.00000410. A 35-degree forearm edit preserved both loop endpoints.
Saving and reopening retained the Action reference, slot and complete Link identity.
Two immutable exports advanced revisions 1 and 2; a same-name rig import preserved
the original test rig and exported the correct `CoshaRig` root. Maximum worker bake
matrix error was 0.00000143. Evidence is in X's
`task_artifacts/animation_roundtrip/link_acceptance_20261001/blender_acceptance.json`.
The corrected linked revision 4 exported all 217 bones with 60 motion samples,
authored duration `0.9666666984558105` seconds, reference frame `0` and playable
frames `1…60`. Actual Unity Sync and Quick View succeeded; the verification
report confirms candidate preview, private Apply/Restore and preservation of
production assets, scenes and selection.

## Scope

Return one explicitly selected skeletal Action from Blender as a new Unity
Humanoid clip. Current integration acceptance covers one linked Cosha Walk edit
through Sync, Preview and private Apply/Restore;
the standalone authored-Action exporter retains its existing validation. No animation library, batch system, controller
replacement or general retargeting is part of this change.

The existing Unity packet format remains `cdesigner.animation/1`. Blender checks
the same-character bind skeleton, uses the verified Character Designer control
graph, and keeps the original controls, constraints and drivers. FK switch values
are included in the existing persistent preview-recovery record. Unknown control
graphs fail before applying an Action.

## Linked Walk workflow

1. Prepare a single character/clip Link in Unity Character Tuning.
2. In Blender **Character Designer > Animation**, choose **Link / Import** and
   select the packet's `character_animation_link.json`. If the Link has no model
   path, choose its exact Unity character FBX in the following file selector.
3. Edit the independent character's linked Action, then choose **Sync to Unity**.
4. In Unity, sync the candidate, preview it and explicitly apply the desired
   version to the character's own override. Acceptance exercises Apply and Restore
   on a private test target.
5. Save the independent editing `.blend` to retain the rig/Action association.

Blender retains one Action ID and Object slot on the imported rig. Selecting a
different Action does not redirect the Link. Every Sync creates an immutable FBX
and sidecar revision; the small Link manifest advances only after both outputs
are complete and their source packet still matches. Failed/cancelled exports
leave the previous Link output usable. There is no watcher or batch library.

The Link schema is `randomrealm.animation-link/1`. Source identity is the Link ID,
target GUID, clip GUID, exact signed 64-bit clip local ID and frozen packet hash.
The Link also carries absolute packet, model and current output paths. Blender
retains unknown fields and writes `blendFile`, `sourceAction`, `fbxFile`,
`fbxSha256`, `metadataFile` and `metadataSha256` after publication, advancing
the integer `revision` only after successful atomic publication. Unity and
Blender must respect the adjacent `.blender.lock` when replacing this manifest.
The snapshot worker restores the verified original rig root name, so Blender's
independent-copy suffix does not change Unity bone paths.

## Standalone Action workflow

1. Send a current character clip from Unity and import its test Action in Blender.
2. Edit that Action, or select a newly authored Action in the Action Editor.
3. In Character Designer's Animation page, choose **Export Action to Unity**.
   Review the explicit frame range and **Loop**, and choose a new `.fbx` filename.
4. In Unity's existing **Tools > Character Designer > Animation** window, choose
   the exported FBX under **Blender → Unity**, review the name, Loop and existing
   Assets folder, then use **Import Blender Action**.
5. The new clip becomes selected for **Preview on Character**. Assigning a clip
   to the production controller is a separate, explicit action.

Export writes a disposable snapshot without saving the user's Blender file.
The selected Action/slot is sampled with NLA disabled. Evaluated control motion
is baked onto a separate native skeleton. The worker links the snapshot rig into
its evaluation scene and updates it before capturing the reference object
transform, including its scale. The real rest pose is preserved in the FBX static
hierarchy and at the first Take frame. Export does not replace existing FBX or metadata files.
Unity verifies the static skeleton with animation import disabled before using the current Avatar and creates unique
FBX and `.anim` assets. The original Avatar, controller and animations stay intact.

For the bones-only **Copy From Other Avatar** import, validation checks the
persisted `ModelImporter.sourceAvatar` identity against the current source Avatar
and requires a real Humanoid animation clip. Unity `6000.5.9f1` was observed to
produce no generated Animator or Avatar subasset for this bones-only import.
Import-log errors reject the candidate; warnings are retained in the result.

The accepted Cosha Avatar has translation DOF disabled. Unity's native animation
import warning identifies discarded translation tracks on `shin.L`, `foot.L`,
`shin.R` and `foot.R`; the detailed warning is in the returned FBX's `.meta`.
This Humanoid conversion is therefore not a lossless joint-translation roundtrip.
The current Avatar configuration is retained. Walk playback, the forearm edit,
timing, events and Apply/Restore were verified under that configuration.

## Timing, coordinates and limitations

For `N` sampling intervals, the FBX contains one Take spanning frames `0…N+1`.
Frame `0` is the true Rest reference; the `N+1` real motion samples occupy frames
`1…N+1`. This lets Unity establish the reference skeleton before evaluating
animated joint translations. Unity excludes frame `0` from the playable clip.

The adjacent `.animation.json` records the range explicitly:

| Field | Value |
| --- | --- |
| `has_reference_frame` | `true` |
| `reference_frame` | `0` |
| `playable_first_frame` | `1` |
| `playable_last_frame` | `N+1` |
| `samples` | `N+1`, counting motion samples only |

`duration` remains the authored motion duration, `N / effective_sample_rate`.
The full FBX Take is one sample interval longer. Unity validates that full range,
applies the playable first/last frames, and verifies the resulting clip duration.
`frame_start`, `frame_end` and `frameRange` remain the original Blender source
range; they are not the shifted FBX frame range. `fps` and
`effective_sample_rate` describe the exported sampling rate.

- Incoming bone matrices already include root motion. Do not multiply their
  recorded root matrix into them again.
- The requested endpoints are sampled exactly. For a fractional frame range,
  effective sampling FPS can differ slightly from the requested rate to retain
  the exact duration; both rates are recorded in `.animation.json`.
- Animation and model exports must use the same rig object reference transform.
  The animation snapshot's object translation is subtracted once, matching the
  model exporter. Different animated object positions at snapshot time can shift
  the clip's offset; they do not establish a new model rest transform.
- One Action and one Object slot are exported. Unkeyed controls retain their
  snapshot values. External animated dependencies are rejected.
- This is skeletal animation only. Shape Keys, materials, accessory physics,
  cameras, audio and animation events are not transferred. Detected omitted
  channels are shown in the export result and recorded beside the FBX.
- The sidecar is provenance and export settings, not another animation format.
  Unity-imported Actions retain the packet hash captured at import time; older
  Actions without that record have an unknown source hash.

## Verification

Passed reference-first-frame verification:

- `tests/test_animation_export_blender.py`: all three native FK, controls and
  reloaded-library-snapshot cases passed with 16 motion samples each. Maximum
  reference-frame matrix component error was `2.2351742e-6`; maximum motion
  matrix component error was `2.3841858e-6`. The checks retain the existing
  tolerances and cover the static root/Rest, animated reference, shifted motion
  samples, playable duration, feet and root motion.
- Linked revision 4: the real 217-bone Walk export completed with 60 motion
  samples and the reference/playable range recorded above.

Earlier passed verification, before the reference-first-frame correction:

- `tests/test_animation_export_blender.py`: native FBX reimport of rest and every
  animation sample; fractional timing, root/foot movement, isolated source
  preservation, omitted channels, source provenance and no-overwrite behavior.
- `tests/test_unity_animation_blender.py`: existing native preview/recovery suite,
  plus frozen source hash and Loop propagation; all seven native tests passed
  again after the bind-error message was updated to name the affected bones.
- `tests/test_unity_animation_controls_blender.py`: current saved X control rig,
  184 native bones, 52 controls, 30 drivers and two eyes; maximum world-matrix
  component error `3.4943223e-6`. Recovery, control graph, Armature data, weights,
  Shape Keys and source-file preservation passed.
- Unity: the scoped companion changes compiled successfully.
- Unity: fresh current-Avatar `Walk_N` packet export passed, with original asset
  and scene preservation.
- `tests/test_animation_export_ui_blender.py`: bounded registered-RNA, draw,
  captured-Action, busy/cancel/poll and result-separation checks with a mocked
  exporter passed; no worker process or native playback was used.

Accepted Unity revision 4 workflow:

- Actual **Sync → Quick View** succeeded for the linked Cosha Walk. Candidate
  preview changed the forearm by `30.0121689°` locally and `39.0805168°` in world
  rotation; repeat sampling had `repeatMatrixError = 0`.
- Private Apply assigned the same owned override to the Player and exact Setup,
  preserving the unrelated Custom override sentinel. Restore changed only Walk,
  including after the linked source identity/hash became stale.
- Both footstep events retained their parameters and normalized phases. Loop,
  root-motion, offset and mirror policies were retained.
- Repeated Sync kept the existing candidate. An invalid manifest retained the
  last usable candidate and saved Link bytes.
- `passed`, `productionPreserved`, `scenesPreserved` and `selectionPreserved`
  are all `true`. Production Cosha remains on Default and the demonstration
  candidate has not been applied to production.

The acceptance evidence is
`D:\Unity Projects\RandomRealm2\Logs\CharacterTuning\AnimationLink\verification.json`.
Private validation assets were moved to Trash after verification. The standalone
menu harness `tests/unity/CharacterAnimationReturnValidation.cs` remains outside
the shipped add-on.

`tests/prepare_animation_link_example.py --prepare-only` prepares an independent
saved `.blend` and one worker job from a fresh Link. It checks every source-motion
sample, applies the same visible forearm edit and retains both endpoints. Run the
worker serially, then call its `finalize_prepared` helper to publish through the
production Link contract. This preparation does not establish Unity acceptance.

As an optional developer fixture, `tests/prepare_animation_roundtrip_examples.py` prepares two examples
without saving X or starting child processes. The Walk example adds a smooth
left-forearm edit only during the middle of the clip and checks that both original
endpoints stay unchanged. The new one-second forearm wave is authored in the
native rest frames, returns to rest at both endpoints, and checks every authored
sample through the existing controls. Preparation alone does not establish FBX
or Unity acceptance. The newly authored Action example is outside the agreed
single linked Walk acceptance scope and is not an additional release gate.

## Character Designer 0.68.0 local release notes

- Return one selected Blender Action as a new Unity Humanoid clip through the
  existing Animation window. Original clips, controllers, the character prefab
  and its current Avatar are preserved.
- Import matching Unity motion through verified Character Designer controls,
  including eyes, without removing the control graph. Preview recovery restores
  the prior Action, NLA, pose and keyed control properties.
- Preserve the native rest skeleton, exact clip duration and one application of
  root motion when baking an Action to FBX. Export records omitted channels and
  source provenance and refuses to overwrite existing outputs.

Acceptance is complete for the single linked Walk workflow.
`character_designer-0.68.0.zip` contains 121 shipped files. Module-only deployment
checks passed with zero mismatches for Blender 5.2 and X. The open unsaved
Blender session was preserved; save normally and reopen Blender to load the new
buttons. Package hash and deployment details are in
`docs/releases/CharacterDesigner_0.68.0_animation_link_20261001.md`.
Git commit and push remain user-managed; this local release does not publish to GitHub.
