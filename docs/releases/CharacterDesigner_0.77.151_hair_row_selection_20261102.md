# Character Designer 0.77.151 — Hair row selection

Hair Strands uses highlighted row buttons instead of checkboxes. A plain click
selects that entire logical entry and its native bones in one step. Ctrl toggles
one entry, Shift selects the visible anchor-to-row range, and Ctrl+Shift adds
that range. A plain click replaces hidden selections; additive clicks retain
them. The panel reports selected entries hidden by the category filter.

Check Selected Bones remains available. It imports the strands containing the
current native bone selection into the list without selecting additional bones
or changing the active bone, mode or visibility. This preserves the user's
single-bone workflow. Select Checked is removed from the panel; its existing
operator API remains registered for compatibility. Category and Rebuild Selected
Strands use the same explicit, nonempty, authenticated logical-entry scope.

Paired L/R entries remain whole units. Range anchors reset on source/session or
filter changes. Empty Ctrl-toggle selection never means all Hair. Fresh source,
binding, physical settings and complete-entry proofs still gate every action.
Selection failure restores the affected native selection, active object/bone,
mode and visibility together with the transient list scope and anchor. Edit
Mode and multi-object Pose Mode refuse row selection before native writes.

The package extends complete approved149: three changed modules (including
version metadata), one new pure helper, 251 inherited files exact; 255 total.
Pending Animation2 controller changes and its private150 are excluded. Future
releases must extend this complete151 package rather than a plain canonical
tree or the old149 baseline.

Ten pure checks and thirteen private Blender5.2 native checks passed, covering
selection/ranges, paired scope, native single-bone import, author preservation,
stale input refusal and native rollback. Native01's four witness failures are
retained: the original generic rest-author witness included PoseBone.select,
which explicitly changes during selection. The identified native02 derivative
excludes that single field from its generic author witness and separately
asserts exact native selection; all rest/pose/Action/physics fields remain exact.

Official ZIP CRC/bytes and both Blender5.2/X disk deployments match the complete
projection. Disk installation is separate from live adoption. No artist input,
live refresh, scene/preferences save, Git upload, Unity operation or FPS work
was performed. Actual mouse/modifier events, native Undo/Redo and visual GUI
acceptance remain pending under Animation's foreground priority. The new row
operator requests native Undo; this is not a claim that Undo was exercised.

Evidence: X `validation/hair_row_selection_20261102/acceptance151_01.json`.
