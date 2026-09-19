# Finger Joint Tool

## Two-joint layout (0.61.22)

The main Mesh Edit Mode entry is now **Rig > Body > Fingers > Finger Ring Layout**.
`finger_layout.py` owns capture, surface sampling, staged topology and data
verification; `finger_layout_ui.py` owns persistent scene state and lazy overlays.
The old one-loop operator remains available via F3 **Finger Joint Rings**, with
its original Side A/B spacing settings in the confirmation dialog.

1. Select one longitudinal row of at least two top quads on the finger body.
   Capture expands each band around the circumference, stopping with an error
   at incompatible ring sizes, hidden faces, open boundaries or branches. It
   never silently includes palm webbing or rebuilds a fingertip cap. A valid
   previous `finger_flex` top-strip guide can be reused when no faces are selected.
2. The captured section's ends remain fixed. Centerline arc-length fractions
   specify the two joint positions; original per-vertex longitudinal rails
   interpolate the real non-circular cross-sections. A valid flex guide supplies
   root-to-tip order, otherwise the artist verifies the labelled boundaries and
   can reverse them. Coral/cyan numbered rings mark joint centers; support and
   between-joint rings are muted grey. Numeric sliders update preview only.
3. Three-ring width is a half-width on either side of each center, separately
   adjustable per joint. Between-joint count excludes those support rings and
   all original artist rings. Roots, tips and **all original vertex coordinates**
   remain unchanged. Original shape loops are never dissolved; adding rings to
   non-planar quads/Subdivision can still affect evaluated shading/surface shape.
4. Generate / Update stages a fresh copy of the captured mesh, subdivides only
   the matching longitudinal connectors and interpolates loop/point data. Source
   data is stored as a private Mesh referenced by the Scene recipe (one current
   layout). Save/reopen keeps the update workflow. Repeated update is idempotent;
   changing counts rebuilds from the same source, not from the last result.
5. A full current-data fingerprint prevents overwriting intervening geometry,
   UV/attribute, Shape Key, transform or weight edits. Because Edit Mode hides
   some RNA attribute buffers, fingerprinting uses an unlinked synchronized
   snapshot. The real scene is not switched or edited during preview/capture.

Shape Keys remain present with original coordinates, values and relationships;
new coordinates and weights are interpolated. No bone or group definitions are
rewritten. Custom normals use a temporary float-vector loop layer for interpolation;
unaffected vertices retain original packed normals to avoid re-quantization of
the rest of the character. The affected normal-space encode permits a 0.005
vector tolerance, while unaffected normal vectors are checked at 1e-6. Supported
original attributes/UVs, faces, seam/sharp flags, weights and Shape Keys are
verified before swapping meshes. Commit exceptions restore the old mesh/recipe.

Source capture and previews require the Basis key and one local single-user mesh.
Animated/driven/absolute/locked keys, Skin data, index-parented children,
Multires levels, baked or index-dependent modifiers and unsupported attribute
types are rejected. Armature/Subdivision/Mirror modifiers are retained, not
applied; guide lines refer to base Edit Mode coordinates, not posed evaluation.
Hidden preview is not cancellation of applied geometry; use Undo to revert, or
X to release the editable recipe without deleting geometry. Load/Undo/Redo clear
transient drawing handlers' cached geometry. No H/S/Shift keymap is installed.

Not implemented here: direct viewport ring dragging, removal/relocation of original
shape rings, asymmetric bend-side spacing, automatic bone placement, reweighting,
or 45/90-degree deformation acceptance. These are separate from retaining and
interpolating the character's existing weights. This scope intentionally keeps
the first geometry workflow predictable.

Tests: `tests/test_finger_layout_blender.py`, `tests/test_finger_layout_gui.py`.
X integration: `X/tests/test_real_x_finger_layout_blender.py`, run against the saved
X.blend only in a disposable Blender process. All ten real fingers pass with six
top quads and 48 added vertices each; the file hash and rig remain unchanged.

## Earlier single-loop prototype

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
pose preview, and complex finger-root or webbing
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
