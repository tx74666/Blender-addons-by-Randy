# Character Designer 0.77.69 — Shift row selection

Custom Vertex and Parts now support multiple chosen rows without adding a
second list or checkbox column. Click the existing left row icon to choose one
row. Shift-click another icon to add or remove it. Chosen icons stay highlighted
together; the name field retains Blender's native double-click rename behavior.

The bottom **Select** button selects the visible vertex union of chosen rows.
Shift-click Select to add that union to the current vertex selection.
**Deselect** removes that union while preserving selected vertices outside it.
Locked memberships remain selectable. Hidden vertices and all hide flags stay
unchanged. With no chosen row, both buttons are disabled.

Multiple row choices stay separate between Custom Vertex and Parts. They follow
names through rename/reorder and are discarded on a different mesh or reload.
They are temporary UI state, not new mesh membership or weight data. Assign,
Remove, rename, delete and reorder continue to use the active row. Existing
scripted single-row Select/Deselect calls keep their default behavior; the UI
opts into batch operations with `use_rows=True`.

Blender 5.2's public UIList API exposes a single active row and no Shift row
event hook. Shift multi-selection therefore uses the existing icon buttons;
Shift-clicking arbitrary row text does not add a second active row. This avoids
global keyboard observers and preserves the native editable name field.

Validation in native Blender 5.2.0 LTS (`fbe6228777e7`): 52 background tests,
12 native GUI-event-loop history tests, and six full registration lifecycle
checks passed. Generated nine-vertex fixtures protect exact geometry, painted
weights, Shape Keys and catalog membership. GUI history covers batch Select
and Deselect in both lists; it does not claim transient row choices are Undo
data or that desktop mouse interaction was exercised.

Accepted evidence is X `validation/mesh_selection_multiselect_20261019/candidate03`.
The complete 208-file release uses accepted 0.77.68 plus the four reviewed files
for this feature, excluding unrelated pending sources. Installed-source checks
and current artist UI refresh status are recorded in the acceptance summary.
This task does not operate or save the artist scene or real preferences.

Local source and installation only; no staging, commit, push or publication.
