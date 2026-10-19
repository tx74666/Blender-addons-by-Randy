# Character Designer 0.77.77 — flat mesh selection rows

Custom Vertex and Parts no longer draw an embossed selected name button inside
the native list-row selection. The name uses Blender's flat menu selection
style, retaining blue multi-selection feedback without the inset button edge
and shadow. Lock, Solo and visibility icons retain their flat presentation.

Shift ranges, Ctrl toggles, Select/Deselect unions and double-click renaming
are unchanged. The full release inherits accepted 0.77.76, including its Weight
Cleanup tools; only the two list draw methods and version file are overlaid.

Evidence:
`D:\Blender\Projects\Character\X\validation\flat_mesh_selection_rows_20261019`.
The existing 22 native core tests pass; eight own-process mouse checks exercise
both catalogs, including rename. Native screenshots were visually reviewed.
Live adoption, when successful, changes only the two draw function bodies and
retains registered classes, scene data and the loaded addon lifecycle. See
`live_display_receipt.json` and the final acceptance summary for its actual state.

The default theme has transparent menu outlines and the same blue selection
color as native list items. Custom themes can define different menu colors;
this update does not edit the user's theme or preferences.

Local source/build/deployment only; no commit or push.
