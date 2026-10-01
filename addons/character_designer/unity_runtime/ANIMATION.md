# Character Designer: animation exchange and recovery

Unity motion transfers to an independent Blender test Action using the same
character's bind skeleton. It does not create an Avatar, bind a character, modify
a rest pose or replace an Animator Controller.

The single-Action return path and verified Character Designer control-rig support
are accepted for the Character Designer **0.68.0 local release**, dated 2026-10-01.
The linked Cosha Walk completed Blender Sync, Unity Quick View and private
Apply/Restore acceptance. Production Cosha remains on Default and the
demonstration candidate is unapplied. See `docs/animation_roundtrip.md` for the
verification record. Local deployment checks are recorded separately after the
release tools run.

The reference-first-frame correction passed the three-case Blender regression
for native FK, controls and a reloaded rig/Action-only library snapshot. Actual
Unity acceptance of corrected revision 4 also passed.

The initial Unity `Walk_N` packet export passed, but the current saved X has six changed
finger Rest positions compared with the Unity model (maximum fitted mismatch
10.153 mm). Example preparation correctly refused that mismatch. Choosing the
matching model/source route now uses an independent copy of the exact Unity FBX.
Fourteen pure Link tests and the registered Link/Sync UI contract passed. The fresh
linked FBX and packet passed all 217-bone/59-sample checks, a visible forearm edit,
loop endpoint preservation, saved Action/slot/Link persistence and two immutable
revision exports. A suffixed editing rig still exported the correct source root.
Corrected revision 4 exported the linked 217-bone Walk with 60 motion samples,
duration `0.9666666984558105` seconds, reference frame `0` and playable frames
`1…60`. Actual Sync and Quick View succeeded, and the Unity acceptance report
confirmed private Apply/Restore with production, scenes and selection preserved.

## Single Walk Link

Unity Character Tuning creates `character_animation_link.json` beside its
animation packet. In Blender, choose **Link / Import** under **Character Designer
> Animation**, then select that manifest. The linked model path is used when
available; otherwise select the exact Unity character FBX in the next selector.
Blender opens an independent editing scene and imports the target-evaluated
motion with the same strict bind checks. Existing X meshes and controls remain
in their original scene.

Edit the linked Action and choose **Sync to Unity**. This button retains the
association with the Action ID/slot. Each export creates new immutable FBX and
metadata revisions, then atomically updates the owned manifest with their paths
and hashes, incrementing the integer revision only on successful publication.
Unity syncs that candidate for explicit preview and character-specific
Apply. Save the editing `.blend` to retain the Link. A failed or cancelled export
retains the previous candidate; selecting another Action cannot redirect it.

The schema is `randomrealm.animation-link/1`; target GUID, clip GUID/local ID,
source packet hash and optional model hash establish identity. The exact 64-bit
clip local ID is retained. Both writers respect `<manifest>.blender.lock` before
replacing the manifest. No background watcher or animation library is added.

## Unity to Blender

1. In Unity, open **Tools > Character Designer > Animation**. Select the character
   prefab and an existing clip. Cosha.Player and its Walk clip are the local defaults.
2. **Preview on Character** opens an isolated preview. Play/pause and the seconds
   slider evaluate the same character. Drag the image to orbit; scroll to zoom.
   **Restore / Close Preview** disposes the preview without changing the scene.
3. Choose the handoff folder and press **Send Test Animation to Blender**.
4. In Blender, open **Character Designer > Animation**, choose the armature and
   **Import Latest from Unity**. The folder is available under **Files**; the file
   button accepts an explicit `.cdanim.json`.
5. Play/pause or scrub Blender's timeline. **Restore Previous Action** and
   **Cancel Preview** return to the original Action/Slot, NLA state, pose, frame,
   range and playback state. Undo/Redo and save/reopen recovery are supported.

The local handoff folder is
`D:\Blender\Projects\Character\Animation\UnityExports`. Existing legacy JSON,
`Animation.blend`, animation assets and Controllers are retained. Blender local
Kimodo generation remains available in its collapsed section; Unity motion does
not pass through Kimodo scaling or retargeting.

Each import creates a unique `Unity Test · <clip>` Action. Restore retains that
Action for inspection. To run the complete reversible preview again, use Import
Latest/File, rather than assigning the retained Action through Blender's ordinary
Action dropdown.

## Connected joints and recovery

Humanoid evaluation can translate a child joint slightly, including foot joints.
Blender connected bones ignore such translation channels. If a clip requires it,
the preview temporarily assigns a copy of the Armature **data** to the same rig
Object and releases connected flags on that copy. The original data is strongly
referenced by the recovery record and is never edited. Heads, tails, parents,
rest matrices, weights, object identity and Armature modifier targets stay intact.
Restore switches back to the original data ID and removes the unused preview copy.

Native FK rigs and verified Character Designer controls use the same strict
bind-skeleton check. The control path validates the existing limb IK/FK, root,
torso, spine, foot and eye records, then solves on a disposable copy. It keys the
necessary native/FK/eye controls and preview switch values without deleting the
original constraints or drivers. Unknown or incomplete control graphs and
incompatible transforms are rejected before attaching an Action. This does not
retarget motion from a different character.

Restore also restores the saved IK/FK and root-scale properties. Missing saved
control values, structural edits during preview, or edits to shared original
Armature data block an unsafe restoration and retain the preview. Recover the
intended data deliberately instead of discarding edits.

## Blender to Unity: one Action

1. Select the intended Action in Blender's Action Editor. An edited Unity test
   Action and a newly authored Action both use this path.
2. In **Character Designer > Animation**, choose **Export Action to Unity**.
   Check the explicit start/end frames, sampling rate and **Loop**, then choose
   a new `.fbx` filename.
3. Wait for completion and review the reported omitted channels. Export creates
   the animation FBX and an adjacent `.animation.json` with timing and provenance.
4. In Unity's **Tools > Character Designer > Animation** window, use the
   **Blender → Unity** section. Choose that FBX, the matching character prefab,
   a new clip name and an existing Assets folder. Review **Loop** and click
   **Import Blender Action**.
5. Use **Preview on Character** to inspect the new clip. Assign it to an Animator
   Controller separately when ready.

Blender writes a disposable snapshot without saving or replacing the user's
working file. It evaluates one selected Action/Object slot with NLA disabled,
then bakes the evaluated bones onto a separate native skeleton. The FBX contains
one take and the unchanged native rest hierarchy. The worker first links and
evaluates the snapshot rig, then captures its reference object transform so the
static hierarchy retains the actual object scale. The first Take frame also
contains true Rest, followed by the real motion samples. It has no meshes or generated
rig controls. Existing FBX and metadata files are never overwritten.

Unity checks the returned static rest skeleton against the selected prefab with
animation import disabled before
using its current Humanoid Avatar through **Copy From Other Avatar**. It creates
a new FBX asset and a separate `.anim` asset with unique paths. The source FBX,
target prefab, Avatar, original clips and controller are retained. The importer
does not update a stale Avatar; use the character's current matching model and
Avatar before returning an animation.

Bones-only **Copy From Other Avatar** validation checks that the persisted
`ModelImporter.sourceAvatar` is the current source Avatar and that the import
produces a real Humanoid animation clip. Unity `6000.5.9f1` was observed to create
no generated Animator or Avatar subasset for this bones-only import. Import-log
errors reject the candidate, and warnings are recorded in the result.

The accepted Cosha Avatar has translation DOF disabled. Unity reports discarded
translation animation on `shin.L`, `foot.L`, `shin.R` and `foot.R` in the returned
FBX's `.meta`. This Humanoid conversion does not preserve every joint translation
losslessly. The existing Avatar configuration is retained; the accepted Walk,
forearm edit, timing, events and Apply/Restore were tested with it.

## Exchange contract

The versioned schema is `cdesigner.animation/1`. Matrices are row-major, Unity
left-handed Y-up, metres. Bone definitions include paths, parent indexes and rest
matrices inferred from actual skin bind poses. Frames contain seconds and bone
matrices in a fixed initial character-root space, **already including root motion**;
the separately recorded root matrix must not be multiplied into them a second time.
Default sampling is 60 Hz and includes the exact clip endpoint.

Blender registers the source bind heads against the target rest skeleton with one
uniform scale and coordinate conversion. It applies evaluated-pose × inverse-bind
to each corresponding Blender rest matrix. This handles FBX bone-axis differences
without a second humanoid retarget. Seconds map to frame/subframe using the existing
Blender FPS and FPS base. Mesh evidence at five sample times is diagnostic and is
not imported as replacement geometry.

Unity packet publication remains explicit and atomic. Cancelling sampling or
failing to publish the latest manifest preserves the previous packet and
manifest. There is no background live synchronization.

The return uses standard FBX, not a second motion-packet format. Its sidecar
records the selected Action, source frame range, exact duration, requested and
effective sample rates, Loop, omitted channels and source hashes. Unity-imported
Actions keep the packet hash captured when they were imported; replacing a file
at that packet path does not change an existing Action's provenance.

For `N` sampling intervals, the single FBX Take spans `0…N+1`: frame `0` is the
Rest reference and frames `1…N+1` contain the `N+1` motion samples. The sidecar
sets `has_reference_frame: true`, `reference_frame: 0`,
`playable_first_frame: 1` and `playable_last_frame: N+1`. Its `samples` count
excludes the reference frame. Unity validates the full Take, excludes frame `0`
using the playable range, and verifies the final clip duration. The reference
pose must not become part of the playing or looping motion.

`duration` remains `N / effective_sample_rate`; the full FBX Take is one sampling
interval longer. `fps` and `effective_sample_rate` describe the exported rate.
`frame_start`, `frame_end` and `frameRange` retain the original Blender source
range rather than the shifted FBX range. Importers must use the sidecar's
playable range to remove the reference frame.

Return export samples both requested endpoints exactly. Fractional source FPS
and frame ranges can require a slightly different effective sampling rate to
preserve duration. The snapshot's rig-object translation is subtracted once,
matching model export. Model and animation snapshots must share the same object
reference transform; taking snapshots at different animated object positions
can shift the animation offset. Root motion already present in bone matrices
is never added again.

This path exports skeletal motion only. It omits Shape Keys, material animation,
cloth/accessory simulation, cameras, audio and animation events. Detected related
channels appear in the completion message and metadata. External animated
objects driving the rig are rejected; bake their contribution into the selected
rig Action first. Unkeyed controls retain the snapshot values. An animation
library, batch export and general humanoid retargeting are outside this change.

## Verification and practical limits

`CharacterAnimationValidation` samples the real Cosha.Player Walk in a PreviewScene,
checks repeat seeks, actual skin, cancellation and failed publication, and confirms
scene/selection/dependency preservation. It also writes preview images. It does not
save or reload the user's scene. The local Walk is an in-place clip; this test alone
does not certify a travelling Root Motion clip.

`tests/test_unity_animation_blender.py` covers import and failure rollback, slots,
NLA, scales, joint translation, artist Shape Keys and persistent recovery.
`tests/test_animation_export_blender.py` checks native FBX rest/sample roundtrips,
constraint/driver evaluation, fractional timing, feet and root motion, live
snapshot preservation, omitted channels, provenance and existing-file refusal.
Its positive-reference-frame regression passed all three native FK, controls
and reloaded-library-snapshot cases, each with 16 motion samples. Maximum
reference-frame matrix component error was `2.2351742e-6`; maximum motion matrix
component error was `2.3841858e-6`, with the existing tolerances unchanged.
`tests/test_animation_export_ui_blender.py` passed registered-RNA/draw, captured
Action, busy/cancel/poll, timer cleanup and Kimodo-result separation checks with
a mocked exporter. This background check does not exercise a real file picker.
The current-X control test passed with 184 native bones, including both eyes, and
verified recovery and source preservation. The accepted real workflow is one
linked Cosha Walk edit through **Sync → Quick View → private Apply/Restore**.
The candidate changed the forearm's local rotation by `30.0121689°` and world
rotation by `39.0805168°`, with `repeatMatrixError = 0`. Private Apply assigned
the same owned override to the Player and exact Setup while preserving an
unrelated Custom override. Restore changed only Walk, including after the linked
source identity/hash became stale. Both footstep events retained their parameters
and normalized phases; loop, root-motion, offset and mirror policies survived.
Repeated Sync retained the existing candidate, and an invalid manifest retained
the previous candidate and Link bytes.

The report at
`D:\Unity Projects\RandomRealm2\Logs\CharacterTuning\AnimationLink\verification.json`
records `passed`, `productionPreserved`, `scenesPreserved` and
`selectionPreserved` as `true`. Production Cosha remains on Default, with the
demonstration candidate unapplied; private validation assets were moved to Trash.
A newly authored Action example remains an optional developer fixture, not an
additional release gate. This accepted single-Walk scope does not establish
general retargeting or acceptance for every authored Action.
`tests/test_unity_animation_ui_blender.py` runs native operator Undo/Redo and playback.
`tests/validate_unity_animation_character.py` compares all real motion samples with
position tolerance 0.1 mm and rotation tolerance 0.1 degrees, and checks source
preservation. Skin diagnostics must be interpreted separately from this bone test.

Blender skins its control cage before Subdivision; an exported Unity mesh is
usually subdivided before skinning, so ordinary surface deformation can differ.
Changed source topology also invalidates old loop calibration. Such a stale
calibration is not automatically recaptured or overwritten by importing animation.
Corrected and uncorrected skin must be compared separately on matching mesh versions.
