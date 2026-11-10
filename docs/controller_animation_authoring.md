# Controller animation authoring

This workflow converts a complete imported body motion into editable controller
channels on the character's existing rig. It applies to supported motions by
their mapping and control graph; Walk is a validation example, not a special
case in the converter.

The worklist Source remains the read-only comparison baseline. Ordinary new
imports populate an independent controller Custom after the Source is fixed.
For an existing editable Custom, **Generate Controller Custom** creates another
retained Custom and keeps the previous version.

Installed arms and legs receive complete IK target, pole, orientation and switch
trajectories. The native FK channels required by this rig remain inputs. Other
body controls and their existing animation are retained. Authenticated Hair and
Dress branches stay in the Action and are isolated from the private body solve.

## Editing a motion

1. Select the motion's **Custom** and return to Animation, Controls and Pose
   Position. Finish and key any pending pose edits. For an older Custom, click
   **Generate Controller Custom** first. Pause any running Hair Physics preview
   before conversion.
2. Select an IK hand or foot target in Pose Mode. Its location curves now
   describe the complete motion, including frames where the source used FK.
3. Edit and insert ordinary keys when the change should be local in time.

**Automatically Simplify Keys** starts enabled. It removes extra controller
keys only after playback verification. Disabling it keeps the dense editable
grid; it does not bypass playback checks. **Playback Tolerance** exposes limits
that can only be stricter than the default 1 mm and 0.25 degrees. The panel shows
the actual generated key count and reduction afterward, rather than asking for
a target percentage.

To move a hand slightly outward throughout a walk, leave Auto Key off, move the
selected target, then click **Apply Pending Move to Whole Clip** before inserting a key.
The operation measures that unkeyed move in the target's local channel space and
applies it to the complete location curves. It preserves their times,
interpolation and handles and publishes another Custom. Select both targets for
a two-handed adjustment. The previous Custom and Source stay available.

Applying a move across the clip is a constant offset of its existing trajectory.
It is separate from authoring a time-varying sparse correction layer. A normal
single-frame Insert remains a normal keyframe operation.

## IK/FK display

The daily **All IK / All FK** buttons switch all installed limbs at the current
time. The current-frame status describes the selected limb or limbs; without a
limb selection it describes all installed limbs. Mixed states list the relevant
left/right arm/leg states, including intermediate blend weights. The Body Part
dropdown controls the separate build/rebuild operation.

The new controller Custom begins with full-clip IK mode curves. A current-frame
mode switch is not a full-clip conversion and does not create missing target
trajectories by itself.

## Saving and continuing

Record intended pose changes with ordinary Insert or Auto Key, then save the
editing blend normally. A moved but unkeyed target is not a recorded timeline
edit. The generated Custom belongs to this character and retains its Action,
slot, worklist row and recovery/checkpoint data; the Source and previous Custom
remain available. Keep any packaged editing dependencies alongside the blend.

The four generation-panel preferences are transient and reset when reopening.
Each generated Action separately stores the options actually used, its playback
budget and key-reduction result. Resetting a panel preference does not discard
that Action or its keys. Clip duration follows its actual frame range and FPS;
an export sampling rate of 60 Hz does not imply 60 frames. A compatible Unity
target must be established explicitly before a local Custom can replace a Unity
animation through the existing return workflow.

## Representation and preservation

Native FK controls in this rig are the original bones with their control
display. IK targets and poles are separate generated controls. Both contribute
to one evaluated skeleton through the owned control graph.

The retained native FK input curves must not be described as a newly sampled
post-IK bone result. A second complete, editable bone-curve projection with
automatic reverse synchronization is a separate authoring feature; the present
converter does not claim that behavior.

The solver uses disposable scene/object/Armature copies, samples evaluated input
channels at the same time as each desired pose, and validates actual new-Action
playback at samples and subframes. Reduction is accepted only after that native
comparison; otherwise the complete samples remain. The editable author Action,
its Source and unrelated slots are preserved through retained publication and
owned rollback.

Each limb keeps its first solved native Pole angle throughout the clip and all
adaptive passes. The Pole's position compensates for the changing bend and roll;
the complete body is checked again afterward. This avoids interpolating across
the native angle's positive/negative 180-degree boundary. A rig with an active
driver on that owned Pole angle retains its original motion without conversion.

The default **EDITABLE** playback budget follows the user's selected allowance:
1 mm for world body-bone positions and 0.25 degrees for their orientations.
Scene length units are included. Scale and rotation-free stretch differences
remain limited to 0.0002 relative error. Every matched sample keeps the stricter
native solve and written-Action checks, including samples removed by reduction;
the selected world budget applies to the additional interval probes. Explicit
`quality='STRICT'` keeps the original native matrix/TRS playback gates. These are
sampled body checks, rather than a continuous-time or mesh-vertex bound.

Current-Custom conversion reauthenticates the active row, Action and slot.
It also compares the current keyed body inputs with their evaluated curve
values before creating a result. If there are unkeyed changes, conversion
refuses and identifies affected bones so those changes can be keyed first.
This execution check does not run while drawing the panel. Explicit imported
Action conversion does not compare against a different animation held on the
host rig.
Private frame evaluation also refuses an active, unpaused Hair preview before
touching its session. Paused previews stay paused. Ordinary imports perform the
same check before creating or switching an editing scene.
Active NLA composition, weighted Actions, captured Rigging Inputs and pose-test
drafts retain their dedicated workflows. Explicit imported-Action sampling does
not require or modify the host rig's current AnimData. The same-character Unity
mapping rules continue to apply.

New channels change the export input schema. Conversion clears only the copied
Action's owned Export Base Pose record; capture its new export base when the
export workflow requests it. No scene save, preferences save, live reload or Git
publication is implied by constructing a controller Custom.
