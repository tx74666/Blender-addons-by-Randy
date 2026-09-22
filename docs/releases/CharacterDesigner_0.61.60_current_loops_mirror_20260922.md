# Character Designer 0.61.60 — current loops and explicit mirroring

Mark previously compared the current finger against the whole saved Capture
surface before inspecting the selected loop. Sliding or reshaping valid loops
could therefore request a rebind, despite the displayed guides being frozen.
Mark now checks selected current edge cycles and uses saved direction/range only
as hints. Align Joints derives a fresh local sleeve and closed-tip proof from
the marked loops, rather than old face indices or old surface equivalence.
Current bone identity, interior placement, fixed endpoints, Roll and rollback
remain protected. Mark can succeed even when local geometry is not sufficient
for Align; the latter then retains bones and marks with a brief explanation.

Two compact explicit mirror entries support the manual workflow:

- Basic Setup header: mirror the selected mesh region through the existing
  Mirror Selected Region operator, then synchronize valid fully covered finger
  references and yellow marks. Partial or unavailable references are retained;
  that does not prevent the mesh mirror. The saved difference badge refreshes
  only on this explicit action. Source references and all bones stay unchanged.
- Bone Chain header: mirror the current finger's rest positions and axes/Roll
  to its existing counterpart. No mesh symmetry or Capture topology proof is
  needed. This does not generate bones or modify source/unrelated bones.

Display stays frozen/cached and master-eye-controlled. Neither action adds a
new overlay, timer or dependency-graph listener. Actual mesh replacement retains
attribute/Shape Key/weight/normal checks. Metadata commit failures roll back the
mesh transaction too. Post-commit display errors preserve a successful result
and its Undo entry rather than incorrectly returning cancellation.

Validation: 84 checks passed in serial Blender 5.2.0 runs on disposable fixtures.
Six new editing cases cover Capture followed by sliding, reshaping, inserting
and dissolving rings, re-marking, current local alignment and frozen guides.
Eight region-sync cases cover full and partial regions, target ambiguity, stale
references, metadata rollback and post-commit refresh failure. Nine bone-mirror
cases include arbitrary chain lengths, transformed mirror planes, current-side
scope, rollback and both signs of Local X rotation; mirrored bending agrees with
the existing Roll calibration convention. Remaining checks cover Mark ordering,
cache-only display, capture scope, normal/Shape Key/weight preservation and real
add-on enable/hot reload. No artist project was used for these mutations.
