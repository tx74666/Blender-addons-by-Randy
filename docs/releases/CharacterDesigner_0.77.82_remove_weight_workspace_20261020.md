# Character Designer 0.77.82 — native weight painting

Edit Weights duplicated the user's direct Original → select Mesh → Weight Paint
workflow and could choose the configured Body instead of the intended Mesh.
The dedicated shortcut is removed from Bone Display, Body Setup and the Weight
page, together with its operator, panel, mesh selection, bone mapping and
workspace-creation code.

Use Original to expose the source bones, select the Mesh, and enter Blender's
native Weight Paint mode. Return to Object or Pose Mode before choosing
Controls. Native weight tools and the existing Original / Controls behavior
remain available.

Only recovery compatibility remains in the former module: it cannot start a
new session or register a UI. A one-shot callback after registration/file load
restores an older saved workspace's display and context without replacing any
paint. A failed recovery keeps its record and attempts a rollback; legacy-only
guards protect that record from becoming stale. There is no recurring recovery
poll. The module filename is retained so deployment can overwrite old installed
code rather than leaving a stale operator module behind.

The complete accepted 0.77.81 package is the baseline. The previously paused
body_region candidate remains excluded. Evidence and scoped native checks live
under `D:\Blender\Projects\Character\X\validation\remove_edit_weights_20261020`.
This is a functional simplification, not a performance investigation.

Local changes only; no commit, push or PR. Artist scene and preferences are not
changed by the removal. The installed update loads after a normal plugin refresh
or Blender restart.

Final acceptance: 13 pure UI checks and 10 scoped native checks passed. Both
Blender 5.2 user and X deployment checks matched all 212 files. The first native
run retained a stale UI-label failure; the corrected test passed with unchanged
production bytes. All task-owned Blender processes exited normally.
