# Character Designer 0.61.37 — Rig context before subcategories

The Body / Hair / Skirt selector was drawn inside the main panel ahead of the
separate Character Setup and Bone Display panels. Reordering the body tools
could not move that selector below the common prerequisites.

Character Setup, Bone Display and a headerless Rig selector are now ordered
sibling children of the main Character Designer panel (0, 1, 2). The existing
top-level page grid remains first. Both common panels retain their own native
collapse controls and identifiers; the selector is not inside either collapsible
section. Body/Hair/Skirt tool routing and data settings are unchanged. Shared
Setup/Display still appear on Weight, without the Rig selector. The former
selector draw was removed, so only one row appears.

Validation:

- Existing `test_ui_pages_blender.py`: four page/routing checks passed.
- `test_rig_hierarchy_blender.py`: structural ordering, all subroutes/shared
  context with no data mutation, and repeated register/unregister passed.
- `test_rig_hierarchy_gui.py`: isolated Blender GUI using a synthetic saved
  fixture, actual panel rendering on Body/Hair/Skirt, and native header clicks.
  Screenshots were inspected for placement with both common panels collapsed
  and Character Setup expanded. Selector remains below them and visible.
- Additional Bone Display regression: 10 of 11 test methods passed; the foot
  helper hiding test failed in both its ROLL_DECOUPLED/L and DIRECT_PREROLL/R
  subcases at `tests/test_bone_display_blender.py:189`. Re-running the same suite
  with the still-installed, unmodified 0.61.36 produced the identical two
  failures. This pre-existing behavior is not changed by the panel-only update.

No character assets, mesh, rig, weights or live Blender session are edited by
the layout change or these tests. This is a local deployment, not a Git push.
