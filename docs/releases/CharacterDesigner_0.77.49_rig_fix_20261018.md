# Character Designer 0.77.49 · Rig > Fix

Rig now has a **Fix** section alongside Body, Hair and Skirt. It gathers small
placement and display adjustments without requiring a rebuild:

- **Ground Character** moves the recognized character's currently visible,
  evaluated mesh bottom to world Z=0. Meshes, rig and controls move together.
- **Vertical Distance / Move Character** applies a signed world-Z translation.
- **Show Body Controls** reveals the existing Body controls and custom shapes.
  It always shows rather than toggles, so clicking again does not hide them.

Placement has moved out of Body. Regional Release/Rebuild stays in Body.
The display action uses the existing strict display backend and preserves the
pose, original bones, skin weights and custom widget geometry. It is disabled
during Original editing; choose Controls there first. Navigation is session-only
and does not change the scene.

Grounding uses the displayed pose and lowest visible character geometry,
including clothing and footwear; choose the intended standing pose first.
These tools do not reposition foot-roll pivots, choose a knee Pole plane or
rebuild a leg. Moving a shoe mesh, moving an IK target and editing native Rest
bones have different effects. A future foot adaptation needs its own transaction
that preserves the selected leg's custom control shapes.

## Approved release and validation

Runtime development is canonical-first. This release uses the documented
approved-projection workflow because a previously paused optimization remains
in the shared working tree. The base is the accepted 0.77.47 ZIP, SHA256
`a2e0e6f21f7ce64dbb7bfc98e47a179533d4b6fc0cdcb6c4f624e62d8922a15f`.
Only `__init__.py` registration/version/help text, `ui_constants.py`,
`body_setup_ui.py` and new `rig_fix_ui.py` differ: 200 shipped files total.
The complete AST of canonical registration is checked against the approved
feature after excluding only the named paused watcher changes. All other
runtime files, including `body_region.py` and `bone_display.py`, remain exact
0.77.47 bytes. This is not a performance release.

Evidence and reproduction:
`D:/Blender/Projects/Character/X/validation/rig_fix_20261018/`.
Use its same `release_projection.json` for build, deployment and `--check`.
`RELEASE_PROJECTION_MATCH` means equality with that complete frozen payload,
not the entire working tree containing the paused changes.

- Pure UI: new Fix 10 tests, existing placement UI 4 and regional UI 9 pass.
- Actual frozen Blender 5.2.0 LTS candidate: 24 placement checks pass.
- Three existing page-routing/hierarchy check groups and repeated registration
  pass, including old routes and the new Fix enum.
- Public Show Body Controls passes an isolated customized-leg fixture:
  Rest, raw pose, meshes, weights, widget assignments/geometry and display
  offsets remain exact. Repeated Show is idempotent; an Original session is
  not implicitly exited.
- The older broad UI-page script remains failed at its unrelated Limb IK field
  order assertion (`selected_limb` versus `show_body_setup_advanced`). Its
  unchanged Limb IK source is exact baseline; the failed report is retained.
  It is not counted as a full-suite pass. Relevant route groups were run
  separately without changing that assertion.

The artist scene is not used as a test fixture. Installation does not perform
grounding, show/hide the artist's bones or fit foot controls. Reload the installed
add-on to expose the new registered section. No Git commit or push is performed.
