# Character Designer 0.62.5 — paired wrist planes

Hands now offers one linked A-pose plane for both existing hands. Palm tilt is
mirrored across Armature Local X, without a Left/Right selector. The horizontal
reference is projected onto each native hand's longitudinal axis, preserving
its A-pose endpoints. A selected mesh palm on either side can instead supply both
references. Missing or incompatible counterparts are reported, never created.
Existing independently saved palm references remain readable in AUTO; replacing
them with a linked plane/reference is an explicit action.
Mesh mode requires actual shared reference evidence before claiming linked hands;
the mode button alone cannot turn old independent references into a linked pair.
Use Saved References returns to the retained references without recapturing.

Read-only viewport graphics show the wrist plane and rotation directions:
X up/down, Y longitudinal twist, Z sideways. The actual Forearm Twist axis is
drawn separately. Roll cannot make differing hand/forearm long axes coincident;
their angle is available in Details. Graphics create no bones, Empty or mesh.
Selecting a new mesh reference blocks Apply/Confirm until Use for Both Hands
accepts it, keeping visual candidates and stored calibration consistent.

Hands Apply writes hand Roll only; source endpoints remain protected. The
existing Forearm Twist entry resolves the explicitly configured body bound to
the active rig, preserving an ongoing test's mesh before considering that setting.
It remains disabled in armature Edit Mode. Existing saved Twist ring profiles
survive hand Roll calibration. No correction or twist test starts automatically.

The existing Fingers tool panel is shown when Setup > Fingers is selected for
the active character, also when switching to its bound mesh in Edit Mode. All
existing finger operators, saved references and per-side rules are retained.
Details now separates actual elbow/knee bend from bend-plane direction error;
an approximately 25-degree plane error does not mean a 25-degree bent elbow.

Real-character testing also exposed shallow-chain float32 reach amplification
after IK takes over. Calibrated Direct generation now tests only tiny Target/Pole
corrections, within four parts per million of chain length for the requested
target-distance offset. It retains the best evaluated complete limb pose and
keeps all original Rest, skin and surface checks. Native Rest/Pose inputs are
not edited by the refinement. Normal IK/FK and legacy MCH/ORI behavior is unchanged.

Removing calibrated controls retains existing native Pose inputs when removing
constraints alone already meets Generate's skin limit. This avoids baking tiny
solver noise into an otherwise neutral pose and blocking the next Generate.
The evaluated surface check remains mandatory; actual posed motion still uses
the original skin-preserving bake when needed. Legacy removal is unchanged.

Validation:

- 9 paired Hands Blender tests: mirrored planes, both reference sources, pending
  input, legacy asymmetric references, state invalidation, Fingers routing,
  existing Twist profiles and body/active-session resolution.
- 28 calibration integration tests including a parented shallow A-pose fixture,
  nonzero bounded reach search, failure injection during search, native-basis
  preservation, and legacy switching that must never call the new refinement.
  Both neutral-pose preservation and actual IK-motion baking on Remove are tested.
- Existing Forearm Twist, UI pages, 11 IK/FK and 5 wrist IK/FK regression cases pass.
- Disposable GUI fixture: native Apply undo/redo, handler registration, selective
  UI refresh and no objects/bones added by graphics; screenshots retained.
- Actual complete character copy: Arms/Legs need no additional changes. Hands
  Apply preserves endpoints and produces a maximum evaluated surface difference
  of 5.059816493e-7 rig units. Original weights, mesh coordinates and Shape Keys
  are unchanged. Other native axis differences are at most 2.051617177e-7 from
  Blender's Edit Mode float round trip.
- With Fingers explicitly excluded in the disposable copy, Generate/Update/Remove
  pass with maximum measured surface difference 1.396661761e-5 rig units.
  Generate preserves native Pose basis exactly; Remove returns to zero measured
  surface difference. Rebuild, moving the left hand target, and restoring it
  also pass (elbow moves 0.01472049 units, restored surface difference 1.39666e-5).
- The original copy already fails finger-reference validation before Hands Apply
  (protected finger root changed). Complete checklist generation and artistic
  motion acceptance with those references remain unverified. No automatic
  recapture, root rebinding or confirmation bypass is applied to the user's file.

Local release only. The original X.blend is not overwritten. No commit or push.
