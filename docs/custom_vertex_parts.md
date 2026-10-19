# Mesh Selections: Custom Vertex and Parts

Character Designer 0.77.72 provides two separate lists under **Modeling > Mesh
Selections**:

- **Custom Vertex** keeps the existing `Custom_` selections, including Strap1,
  Strap2 and Strap3. Its **+** creates an empty Group.
- **Parts** lists only its own `Part_` selections. In Mesh Edit Mode, select
  visible vertices and press **+** to capture them as a new Part. An empty
  selection is refused. Double-click the row name to open its rename field.

Nothing is copied, renamed, deleted or migrated between the lists. Each list
has its own active row, locks, order and management menu. Assign, Remove,
Select and Deselect operate on that list's active set. A lock protects
membership from Assign, Remove and Clear; it still permits selection,
renaming, reordering and deletion. Bulk menu actions affect only their own list.

Click a row's name or icon to choose it. **Shift-click** chooses the continuous
range from the anchor row to the clicked row, in the visible filtered/sorted
order. **Ctrl-click** adds or removes one row; **Ctrl+Shift-click** adds a range.
Plain and Ctrl clicks set the anchor; repeated Shift clicks keep that anchor.
Chosen rows highlight together, including their icons. Double-click a name to
rename it in a small dialog. **Select** selects the visible vertex union of
the chosen rows; Shift-click Select to add that union to the current vertices.
**Deselect** removes that union and keeps other selected vertices. List choices
are temporary, independent between tabs, and never change painted weights.
Assign, Remove, rename, delete and reorder still use the active row.

To build a Part from existing Custom Vertex selections, choose their icons
and press Select, switch to Parts and press **+**. This
creates a separate membership set; later changes do not synchronize the lists.

- The row name and selection icon choose entries; Shift selects ranges and Ctrl toggles entries.
- The eye hides or shows the set. Hidden sets combine, so another hidden set
  can keep shared vertices hidden.
- The star solos a set. Multiple stars show their union. While Solo is active,
  it overrides the eyes; clearing the last star restores the eye configuration.
- **Restore Visibility** clears only Parts eyes and stars.
- **Restore Previous Visibility** appears only when older Custom Vertex
  visibility requests exist. It clears those previous requests and preserves
  current Parts eyes and stars.

Both kinds of visibility requests use one shared manual-hide baseline. The
last request cleared restores that baseline; the two restore buttons do not
silently clear each other's requests. Parts eyes and stars remain usable when
its membership is locked.

These are selections inside one mesh. They do not split objects, nest weight
groups, change weights or alter Shape Keys. Members and visibility metadata
use native mesh custom data, save with the mesh, and follow native Edit Mode
Undo/Redo. There is no observer, polling timer or background visibility update.
Visibility operations and new-Part capture require one editable, single-user
mesh in Edit Mode.

Manual H/Alt-H changes are reconciled at the next explicit Parts operation.
This compares current flags with the previous output; it cannot recover an
intermediate sequence that returns to the same flags before that operation.
Native topology editing propagates membership/custom data using Blender's
normal rules; new topology does not infer artist intent beyond those rules.

Deleting a set removes only its membership and owned metadata. If visibility
is active, delete in Edit Mode so the feature can restore the current mesh.
Malformed or conflicting metadata is refused before expected writes.

Validation sources: `tests/test_custom_vertex_groups_blender.py`,
`tests/test_custom_vertex_visibility_blender.py` and
`tests/test_custom_vertex_parts_independence_blender.py`. Run native checks
through the trusted validation launcher with a disposable configuration.
