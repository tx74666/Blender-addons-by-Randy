# RR Helper 0.2.37 — Ring Stack and Arc layers

Texture now has a collapsible Ring Stack section and a Rings visibility toggle.
It targets the current material, respecting pinned Shader Editors and allowing a
nested group view. The active object's material is used when no Shader Editor
is open. The panel names the target material and does not change its graph until
Add Ring is invoked.

Each Material stores named layers with stable IDs, radius, width, radial softness,
enabled state, source Material and Ring/Arc shape. The highest list row wins.
Add, duplicate, delete and move actions maintain one managed Ring Stack group;
the original Base Shader remains outside it and is restored when the final layer
is deleted. Existing Volume, Displacement and unrelated nodes are preserved.

The current-file Ring Mask is reused unchanged. Source materials reuse the
existing Material-to-Shader extraction and shared images/nested groups. Arc adds
UV-space Start Angle and Sweep Angle in degrees: zero points along +U, positive
is counterclockwise, 0 sweep is hidden and 360 is a full ring. Arc endpoints are
hard; Softness retains Ring Mask's radial meaning. See [the user guide](../ring_stack.md).

Updates are staged before commit. Errors retain the last working graph and show
"Changes not applied." in the panel. Self/indirect recursion, manual rewiring,
read-only and animated node trees are guarded. Material copies detach their
managed stack before an edit; unrelated users remain unchanged. No per-frame
scanning or automatic source-material refreshing was added.

Validation on Blender 5.2.0 LTS:

- 20 Ring Stack tests passed (17 initial plus 3 added lifecycle/resource tests).
  The final ownership guard was followed by rerunning both affected tests.
- 18 actual CPU shader samples passed against the unchanged Ring Mask asset,
  covering overlap, Base fallback, disabled layers, softness, zero width,
  clockwise exclusion, negative start, wraparound and 0/360-degree sweeps.
- Native Add Ring Undo/Redo restored both material-layer data and Surface wiring.
  Save/reopen and unregister/register retained the layer IDs and connections.
- Mid-commit and preparation fault injection preserved the prior graph and data.
  Fifty parameter updates kept the generated group count bounded.
- All 45 existing Material-to-Shader tests passed.
- All 63 real addon_utils registration/refresh lifecycle checks passed against
  the canonical candidate, including restricted registration and rollback.
- The required eight-suite Unity AssetPipeline exporter runner passed after
  deployment; Sync-RRHelperAddon -Check passed.
- Node library verification passed: no existing Ring Mask source, binary asset,
  catalog or metadata changes were made.

The local archive is `dist/rr_helper-0.2.37.zip` (17 files). Blender addons_core,
the Blender user add-on directory and the Unity AssetPipeline copy were deployed
and checked with zero differing files. The running Builder6 session was refreshed
and its Texture > Ring Stack panel was expanded, showing Hub_Base and Add Ring.
No Ring layers were inserted into the user's material for UI checking; the live
working blend remains unsaved. Detailed test logs are under
`D:/Blender/Projects/Build/Recovery/RingStack_20260930/`.

Local source and deployment only; no Git commit, push or release publication.
