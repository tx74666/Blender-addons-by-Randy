# Character Designer 0.77.196 — Controller transform shortcuts

In Pose Mode, Alt G, Alt R and Alt S clear the selected controls' location,
rotation and scale respectively. Position an elbow or knee direction arrow
with G and reset it with Alt G. Use Reset Selected Chains for an entire limb.

## Changes

- Alt R handles ordinary controls selected together with generated IK hands
  on the same armature when Auto Key is off. Blender clears the ordinary
  rotations and their native Bendy Bone rotation inputs, respecting locks and
  Pose Mirror. The wrist solver matches each hand to its neutral orientation
  under its current forearm. A failed match rolls back the companion edits.
- With Auto Key off and no configured worklist, a local working Action bound
  to the current isolated rig and Object slot admits a pose-only wrist reset.
  It checks the current binding again before applying. Linked or protected
  Actions, active editing sessions and retained managed bindings keep their
  existing restrictions. The operation only writes pose channels. Managed
  worklist editing, restoration and keyed wrist ownership retain their own
  admission rules.
- A partially blended wrist in the unkeyed local workflow can reach its
  evaluated neutral while retaining the animated IK/FK value and FK inputs.
  The solver checks the actual hand, forearm and target before accepting the
  result. An unreachable pose restores the original inputs.
- G and S keep Blender's original keymaps and transform locks. A wrist-only
  selection in an editable managed Custom Action retains its existing Auto
  Key behavior. Mixed wrist and ordinary selections require Auto Key off.

## Saved X reproduction

An isolated Blender 5.2 loaded the saved X at frame 24 with just
`CTRL_hand_IK.L` selected. The local Action was bound to CoshaRig, the
worklist had no rows, Auto Key was off and IK influence was 0.96102774.
Release 195 refused at the worklist check. A first candidate then reached
the separate full-IK restriction. With both issues addressed, the hand
returned to within 0.00006 degrees of its independent neutral reference
from an initial 38.61 degrees. Its world position and the controller's
location, scale and blend were preserved. The only changed raw pose channel
belonged to the selected wrist. The test compared animation, Rest, mesh and
weight witnesses before and after and did not save the scene.

The full evidence, including retained pre-fix failures and deployment records,
is in [the X validation directory](D:/Blender/Projects/Character/X/validation/control_clear_20261115).
