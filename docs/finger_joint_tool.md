# Finger Joint Tool

## Scope

The prototype logic lives in `addons/character_designer/finger_joint.py` and is
exposed under `Rig > Body > Fingers > Joint Topology`. The same Fingers panel
also contains the per-finger root guide and placement controls.

The artist selects one closed mesh edge loop in Edit Mode and clicks
`Lock Center Loop`. The tool accepts it only when the loop has two continuous
neighboring quad bands. It keeps the locked loop as the center ring, inserts
one loop into each adjacent band, leaves all three rings selected, and creates
a `CD_FingerJoint_*` empty marker in `Character Designer | Finger Joints`.

The two ratios are measured from the selected loop toward each side. They are
deliberately called Side A and Side B until a finger direction or bone chain
is explicitly supplied; the prototype must not guess which side is proximal.

## Safety contract

- No runtime AI or external service is used.
- The prototype does not edit bones, weights, or Shape Key values.
- Existing Shape Key blocks must remain present and receive the new topology.
- Non-quad, open, branched, disconnected, or interrupted selections are
  rejected before the operation.
- A marker with the same source-loop signature prevents accidental duplicate
  ring generation.
- The operator is undoable. The `.blend` file is not saved by the tool.

## Current implementation notes

`bmesh.ops.subdivide_edges` splits the connector edges of each neighboring
quad band. `edge_percents` controls the two ring positions, so the selected
middle loop remains the artist's reference. The marker stores the source
signature, source vertex IDs, and the two ratios as custom properties.

## Supported first version

- One simple closed loop.
- At least four vertices.
- Two one-face-deep neighboring quad bands.
- A mesh in Edit Mode with no topology interruption beside the loop.

The first root-guide implementation now supports five named finger slots. Each
slot locks one mesh center face and, by default, uses the nearest matching
finger chain from Main Rig as the direction line. A two-vertex manual line is
available as a fallback. Check solves the closest intersection of the face
normal line and the finger centerline, rejects a visibly skew or outward
solution, Preview draws the construction, and Apply places the selected
existing finger chain while preserving its rolls, weights, and hierarchy
settings.

## Surface-defined bending (0.61.19)

`finger_flex.py` adds a separate, persistent scene guide. One connected top quad
row supplies its length through its end cross-edges and an area-weighted top
normal. Single long quads and longitudinal edges with active adjacent faces
are also supported. Blue is the user-confirmed root-to-tip direction, orange
points inward (opposite the projected top normal), and green is a positive bend
arc. Both arrows can be reversed. A bare edge cannot uniquely define a normal.

Given unit T and outward top normal N, B = -normalize(N - T dot(N,T)) and
A = T cross B. Saved schema-1 captures keep their old outward convention;
schema-2 captures explicitly store normal_sign = -1 and all source faces.
Each selected bone uses its own head-to-tail T and the guide's bend side to
construct A. Bone-mode previews put purple hinges and proposed wire bones at
the unchanged bone Heads; red lines show current Local X. Applying aligns
local X to A, including the chain root, and checks
that an actual positive rotation moves toward B. The mesh guide uses base
Edit Mode coordinates; posed/modifier-evaluated surfaces are not sampled.
Source topology changes invalidate capture. Armature rest-axis changes require
neutral affected poses, no constraints on affected bones, no rig animation or
drivers, and a local single-user rig with uniform positive scale. Failure
restores all selected rolls. The tool does not save the user's blend file.

Run `tests/test_finger_flex_blender.py` in factory-startup Blender for rotation,
transform, ambiguity, rollback, persistence, and Shape Key/weight preservation
checks. X's `tests/test_real_x_finger_flex_blender.py` exercises both hands' 30
real bones in a disposable process without saving the source asset.

Still not implemented: automatic anatomical face/direction discovery, weight presets,
pose preview, multi-joint batch creation, and complex finger-root or webbing
topology. These should be added only after real test meshes show a stable need.

## Verification

Run:

```powershell
D:\Blender5.2\blender.exe --background --factory-startup --python tests/test_finger_joint_blender.py
```

The regression test checks three generated cross-sections, asymmetric Side A
and Side B ratios, Shape Key preservation, marker metadata, and duplicate
refusal. Before extending the tool, add a focused failing test for the new
topology case.
