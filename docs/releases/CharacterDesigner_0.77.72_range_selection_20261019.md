# Character Designer 0.77.72 — list range selection

Custom Vertex and Parts previously handled multi-selection only on the small
left icon, while clicking a name reverted to native single-row selection.
Shift also toggled one entry rather than selecting a continuous range.

Names and icons now share an event-aware row button. Plain click selects one
row; Shift selects the visible range from a stable anchor; Ctrl adds or removes
one row. Ctrl+Shift adds a range. Filtering, alphabetical ordering and reverse
ordering determine the range, without including filtered-out entries. Select
and Deselect use the chosen rows' visible vertex union.

Chosen name/icon buttons highlight together. There is no separate icon button
plate; unselected rows remain flat. Double-click opens a focused rename field
in a small native dialog, so Ctrl-click is available for multi-selection rather
than being intercepted by native inline name editing. Scripted name setters
and existing explicit rename operators remain compatible.

Only transient list state is added. Membership, painted deform weights,
geometry, Shape Keys and the separate catalogs retain their existing ownership.
The full release is based on accepted 0.77.71, with only the two list modules,
their shared selection helper and version file overlaid.

Evidence: `D:\Blender\Projects\Character\X\validation\mesh_selection_range_20261019`.
Core native tests and actual own-process mouse-event checks are recorded there;
the artist scene and real preferences are outside those fixtures. See the final
acceptance summary for the accepted projection, deployment and live-refresh state.

This is a local change and deployment, without a Git commit or push.
