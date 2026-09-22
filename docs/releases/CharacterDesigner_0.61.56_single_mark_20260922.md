# Character Designer 0.61.56 — one Mark action

The two numbered, color-specific controls are replaced by one **Mark** button.
Both joint loops are gold. Select either loop first, or select both together:
their internal order is assigned automatically from finger root to tip. A
complete pair shows a check on Mark. Marking changes only saved guide metadata.

Re-marking an existing loop is idempotent. With two marks already present, a
new single loop replaces the nearest mark along the finger. If both are equally
close, a warning asks for both intended loops. Selecting two loops replaces the
pair after both have passed validation. Other fingers' marks are retained.
Clear follows X Mirror: both sides when enabled, current side when disabled,
so clearing a mirrored target cannot leave its source guide visibly unchanged.

Both loops share one retained GPU batch per finger. Display refresh parses the
saved metadata once; panel redraws reuse immutable, bounded cached status.
Mirrored paths transform each vertex once before expanding to line segments.
The Capture Detection master eye and side switch reuse buffers without mesh
reads. There are no new timers or dependency-graph listeners.

Validation on Blender 5.2.0 uses disposable synthetic fixtures with Shape Keys
and weights. Seven automatic-mark cases, eight alignment/preservation cases,
six cache/lifecycle cases and four real enable/reload cases cover this release.
The cache check runs 30 hide/show/side cycles with geometry access forbidden,
2,001 draw callbacks with one upload, and 2,000 panel redraws with JSON parsing
and mesh reads forbidden. GPU calls are mocked in this test; its CPU dispatch
timing is not a measurement of real viewport frame rate.

The retired topology/weight generation workflow remains removed. Align keeps
the existing straight-finger and curved-thumb behavior, fixed root/tip/Roll,
paired X Mirror validation and atomic rollback.
