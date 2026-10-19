# Character Designer 0.77.74 — compact export object tree

Unity Export shows Rig above Mesh. Click the group title to expand or collapse
it; the separate checkbox keeps its existing selection behavior. Titles show
only the total number of children, such as `Mesh (6)`. Triangles, selected/total
counts and redundant Main Rig / Accessory Rig suffixes are removed. Compact row
spacing and deeper child indentation make the hierarchy easier to read.

This release overlays only the tree presentation module and version file on
accepted 0.77.73. Update intent, export guards, binding scope, weights and the
partial-update capability gate are unchanged. This layout update does not
enable a pending Unity receiver pipeline.

Evidence is stored in
`D:\Blender\Projects\Character\X\validation\unity_selection_layout_20261019`:
the existing native selection suite, an isolated six-mesh visual fixture and
the final deployment receipt. The artist scene and real preferences are not
used for these checks. Live artist refresh is recorded separately in the
acceptance summary.

Local source/build/deployment only; no commit or push.
