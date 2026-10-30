# Character Designer 0.77.124 — preserve current values during Hair sampling

Hair resegmentation samples animation by changing Scene time. Version 123 kept
the original curves and frame, but its transform-family snapshot missed temporary
current values on other objects and animated custom properties or constraints.
Those edits could be lost after a successful create or a reached failure rollback.
The independent native reproduction retains this failure and its exact inputs.

The sampling transaction now checkpoints supported author components found through
current Action/NLA slot bindings. It restores and verifies them after evaluation,
including external objects, Shape Keys, custom properties, constraint influences
and delta transforms. Driver-only outputs remain driven. Unprovable paths,
read-only author channels and conflicting author/driver components refuse before
the version is created. Original Actions, slots, NLA and drivers remain unchanged.

Version creation, switching and Original return retain temporary edits. Optional
cleanup refuses unresolved temporary animated values because native deletion Undo
re-evaluates those channels. The retained versions remain available on refusal.

The complete candidate inherits approved 122 with explicit reviewed increments.
It retains the validated Physics Off fix, Hair segment versions and regional Relax
Pose compatibility from the frozen 123 candidate. Approved Body/Forearm and
Animation UI code remain unchanged. The 123 ZIP, deployment and failed author-value
reproduction are retained; 123 did not receive final whole-release acceptance.

Final source pins, relevant component checks, integration, official package and
matching Blender 5.2/X deployment checks are recorded in
`D:/Blender/Projects/Character/X/validation/reassigned_work_20261029/acceptance_summary124.json`.
Artist scene and preferences are not saved, no artist Hair count is changed, and
no live Pose or physics action, Unity change or Git upload is performed.
