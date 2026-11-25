# Character Designer 0.77.197 — Edit Mode wrist orientation

Select one or both generated IK hands in Pose Mode and press **Alt R** to
recover the palm direction from the character's Edit Mode A-pose. The hand
stays at its current position and scale. Both sides use their own hand-bone
rest orientation in armature space, including on a rotated character. The
current IK/FK mix is preserved. **Reset Target Rotation** uses this reference
for a wrist too. Alt G and Alt S keep their usual meanings.

The preceding release matched the wrist to the current forearm, so twisting
the forearm changed the resulting direction of the palm. In X at frame 12,
clearing the same 57 selected controls left the hands about 32 degrees from
their Edit Mode orientations. The new target uses the Edit Mode orientation
and the evaluated hand's current translation and scale.

The native test reproduced the artist's unsaved pose from a read-only
observation and checked the inputs and evaluated hand and forearm matrices
before running the operators in a private process. The resulting angles of
the hands differed from Edit Mode by less than 0.00003 degrees. Palm normals
computed separately from knuckle landmarks differed by less than 0.0002
degrees. A separate test entered Edit Mode on a duplicate of the X armature
and read the EditBone axes directly. Further tests covered single hands,
mirror, mixed selection, rotation locks, rollback and managed Auto Key on a
rotated rig. Auto Key off is required for a mixed wrist and ordinary selection.

The running X session loaded the new wrist behavior at frame 12. Its pose,
selection, active Action and slot matched before and after activation. This
session's registered tooltip retains the earlier FK wording until the next
normal Blender start. [Activation record](D:/Blender/Projects/Character/X/validation/wrist_rest_20261115/live/artist_01.json).

Reproduction inputs, native results, versioned package and delivery records
are in [the X validation directory](D:/Blender/Projects/Character/X/validation/wrist_rest_20261115).
