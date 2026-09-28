# Character Designer 0.62.2 — native calibration precision

Apply could roll back a valid shallow arm with “Applied candidate did not pass
calibration recheck.” The preview joint was computed in double precision, while
Blender saved float32 coordinates. Roll still described the unrounded plane.

The candidate now uses native coordinate precision before computing bend direction,
Roll, displacement and actual segment length changes. A float32 error bound reserves
a small margin inside the direction limit for moved joints. Already acceptable
joints remain fixed. Limits and the strict post-Apply check remain active; failure
messages explicitly identify rollback and remaining changes.
Earlier arm/leg previews must be reviewed again because their candidate revision
has changed; the stored record schema stays compatible.

Calibrated IK generation now matches generated controls to the exact saved native
pose inside the builder, before validation. It retains the planned Pole placement
instead of replacing its distance through the ordinary IK/FK snapping path, and
uses more precise plane matching. Body Setup no longer repeats that matching after
converting pose to skin matrices and back. Ordinary IK/FK and legacy rigs retain
their existing behavior. No source Rest or native Pose basis is rewritten by this
new matching path. Existing MCH/ORI compatibility is retained.

Validation and known limits:

- The user's 56-bone hierarchy was reconstructed in a separate factory Blender
  process. The old Apply failure and successful rollback were reproduced; fixed
  Apply succeeds with no remaining changes and no added bones.
- Seven geometry tests pass, including mirrored/scaled shallow arms, direction
  threshold rounding, repeated Apply geometry and explicit bend/length limits.
- 25 Blender calibration integration tests pass. The new shallow-arm case checks
  two Generate/Update/Visual/Remove/rebuild cycles, unchanged original Rest and
  exact native Pose basis preservation during generation. Visual operations add
  no bones. Maximum evaluated test-mesh vertex displacement was 1.040331817e-6
  armature units. Original weights, mesh coordinates and Shape Keys are unchanged.
- Injected failure after installing IK/FK drivers and matching controls rolls back
  the new drivers, controls, Pose and Rest; original data-block identities survive.
- Eleven ordinary/legacy IK/FK and five wrist matching regression cases pass.
- Arms and Legs Apply also pass on a fresh copy of the actual complete character.
  Both retain all 56 bones and preserve weights/Shape Keys. Maximum evaluated
  surface displacement is 5.401542473e-7 armature units for Arms and zero for Legs.
- Full real-character IK generation and animation acceptance remains unverified;
  successful Apply does not replace a user-confirmed palm reference or a visual
  action review. The reported generation displacement above uses a test mesh.

Local release only. No commit or push. Preserve unrelated RR Helper changes.
