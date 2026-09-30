# RR Helper 0.2.32 — Empty origin target selection

Modeling > Origin > Apply to Selection now supports selecting Empty objects
first and one mesh/curve/other non-Empty target last in Object Mode. The Empty
origins move to the active target's world origin. The target is read-only;
children keep their world transforms, geometry, parenting and local channels.
The target can itself be a child of an Empty being repositioned.

Selecting only Empty objects continues to use the 3D Cursor. Mesh and curve
Edit Mode selection and the separate Bottom rule retain their existing behavior.
The panel stays compact and the button tooltip explains both Empty workflows.
Reversed or ambiguous mixed selections do not move anything.

The existing preflight and rollback also protect an independently selected
target. Constraints, drivers or modifier dependencies that prevent keeping
parts fixed are rejected before writes. Geometry Nodes modifiers without
ID-property storage still undergo node-tree dependency checks.

Validation on Blender 5.2.0 LTS in isolated factory scenes:

- 39 Empty/mesh/curve origin checks passed, including nested transforms,
  negative scale, dependent targets, shared geometry, animation channels,
  selection preservation, rollback and Undo.
- 63 registration/reload/unregister lifecycle checks passed.

Local package: `dist/rr_helper-0.2.32.zip`. Program add-ons, user add-ons and
Unity Tools/AssetPipeline/Blender/addons copies pass deployment `--check`
with zero differences. Refreshed the running Builder6 add-on through its
Refresh Add-on control; no origin operation was applied to the user's scene.
No Git commit or push.
