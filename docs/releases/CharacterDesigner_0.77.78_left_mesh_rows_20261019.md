# Character Designer 0.77.78 — left-aligned mesh selection rows

Custom Vertex and Parts display names at the left of each row. Lock, Solo and
visibility icons remain at the right edge. Flat menu selection styling removes
the inset name-button border and shadow. A single expandable layout slot keeps
the name left aligned without changing the user's theme.

Shift ranges, Ctrl toggles, Select/Deselect unions and double-click renaming are
preserved. These actions apply when clicking the name or its icon. The blank
space between the name and right actions retains native UIList click behavior.

The complete release inherits 0.77.77 (which inherits accepted 0.77.76 Weight
Cleanup). Only the two list draw methods and the version field are overlaid.
No mesh, weights, bones, scene or user preferences are changed by this styling.

Evidence: `D:\Blender\Projects\Character\X\validation\flat_mesh_selection_rows_20261019`.
Eight mouse checks in an isolated native Blender process cover both catalogs,
including Shift/Ctrl and double-click rename; authored weights and Shape Keys
are unchanged. Native screenshots were visually reviewed. The final acceptance
summary records the core tests, complete projection, deployment and the actual
live-refresh state separately. Installation on disk does not refresh an already
running artist session.

Custom themes may give menu selections different colors from list selections.
Local source/build/deployment only; no commit or push.
