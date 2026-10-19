# Character Designer 0.77.66 — native custom-group icon

Custom Vertex Groups now displays Blender's GROUP_VERTEX icon, matching the
native vertex-group list. Group storage and behavior are unchanged.

The release is accepted .65 plus only the icon constant and version tuple;
203 of 205 shipped files remain byte-identical. Normalized full AST equality,
official build and Blender 5.2 / X deployment checks pass with zero differences.
No additional behavior tests or new native Blender processes were needed.

The live callback received only the icon-constant change while the artist
remained in Edit Mode. Coordinates, vertex selection, native integer layers,
weights and the six current groups were exact during application; the new
icon was observed in the native UI. No add-on lifecycle reset or scene-data
write was performed. Source-signature metadata was adopted only after the
complete installed package matched its manifest. The artist continued editing;
input protection withheld both save requests, so this task does not claim a
new scene save. The persistent installed source and live icon are updated.

Evidence: D:/Blender/Projects/Character/X/validation/custom_vertex_group_icon_20261019/acceptance_summary.json
Local change only; no staging, commit, push or publication.
