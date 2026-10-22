# Character Designer 0.77.80 — consistent flat row selection

In 0.77.78/79, the left-aligned name button covered only its text. Clicking the
blank space after it reached Blender's native single-selection row and reset
the multi-selection anchor. A separate menu-selected color could also show as
a dark blue box inside the active row. Both were known limits of the earlier
layout and are now addressed.

Names, remaining row space and the small selected-row checkmark route to the
same event-aware operator. Shift selects the visible range, Ctrl toggles one
row, and Ctrl+Shift adds a range. Names remain left aligned; lock, Solo and Eye
remain separate actions at the right. Double-click still invokes rename.

All three pick regions use `emboss=False`, with no depressed menu background.
Only the native active row has a blue background; a small checkmark identifies
each chosen row, including non-active rows. This avoids requiring menu and
list selection colors to match. The list's outer padding keeps native behavior.

The release inherits the complete accepted 0.77.79 package. Only the two list
draw methods, a shared drawing helper, version and this package's README change.
Selection algorithms, rename timers, native attributes, topology and authored
weights remain unchanged.

Evidence: `D:\Blender\Projects\Character\X\validation\flat_mesh_selection_rows_20261020`.
The old .79 failure is reproduced with actual native whitespace clicks. New
native Edit Mode checks cover both catalogs, mixed name/blank ranges, Ctrl,
actual Select/Deselect union behavior and double-click rename, with deliberately
different menu/list theme colors. The final acceptance summary records exact
checks, deployment and artist refresh state. No theme or preference is saved.

Local changes only; no commit or push.
