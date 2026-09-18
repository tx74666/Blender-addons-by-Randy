# Character Designer: animation preview and recovery

Phase 1 transfers the evaluated motion of an existing Unity character to an
independent Blender test Action. It does not create an Avatar, bind a character,
modify a rest pose or replace an Animator Controller.

## Use

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

Target constraints/drivers or incompatible transforms are rejected before import.
The preview does not delete a character Setup. Structural edits during preview,
or edits to shared original Armature data, block an unsafe restoration and retain
both versions. Recover the intended data deliberately instead of discarding edits.

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

Publishing is explicit and atomic. Cancelling sampling or failing to publish the
latest manifest preserves the previous packet and manifest. No background live
synchronization or new Blender-to-Unity action export is added in this phase.

## Verification and practical limits

`CharacterAnimationValidation` samples the real Cosha.Player Walk in a PreviewScene,
checks repeat seeks, actual skin, cancellation and failed publication, and confirms
scene/selection/dependency preservation. It also writes preview images. It does not
save or reload the user's scene. The local Walk is an in-place clip; this test alone
does not certify a travelling Root Motion clip.

`tests/test_unity_animation_blender.py` covers import and failure rollback, slots,
NLA, scales, joint translation, artist Shape Keys and persistent recovery.
`tests/test_unity_animation_ui_blender.py` runs native operator Undo/Redo and playback.
`tests/validate_unity_animation_character.py` compares all real motion samples with
position tolerance 0.1 mm and rotation tolerance 0.1 degrees, and checks source
preservation. Skin diagnostics must be interpreted separately from this bone test.

Blender skins its control cage before Subdivision; an exported Unity mesh is
usually subdivided before skinning, so ordinary surface deformation can differ.
Changed source topology also invalidates old loop calibration. Such a stale
calibration is not automatically recaptured or overwritten by importing animation.
Corrected and uncorrected skin must be compared separately on matching mesh versions.
