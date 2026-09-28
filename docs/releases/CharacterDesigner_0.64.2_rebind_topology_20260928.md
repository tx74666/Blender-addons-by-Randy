# Character Designer 0.64.2 — Rebind after topology edits

Local delivery, 2026-09-28. No commit or push. Existing unrelated changes preserved.

## Problem and correction

Rebind Weights incorrectly called the old weight backup's topology guard before
calculating fresh weights. A changed vertex count or connectivity therefore
blocked recalculation with a Restore error. The live Stocking read-only probe
found 552 current base vertices versus 594 in its first backup; Main Rig,
deform-bone names and current binding validation matched/passed.

Rebind now calculates on the current topology while retaining the original
backup byte-for-byte. Restore Previous Binding still requires matching topology.
The successful-bind message explains when that retained backup belongs to older
topology. Blender Undo restores the state before the latest rebind; solver/write
failure rollback likewise preserves current edited state.

Changes from immutable 0.64.1: quick_bind.py, character_setup.py, version in
__init__.py, and README.md. No changes to solver algorithms or restore guards.

## Validation and delivery

Serial Blender 5.2.0 LTS tests passed:

- Existing Quick Bind suite: 9 scenarios.
- Existing remove/reconnect suite: 8 tests.
- New topology/rebind suite: 3 tests with subdivision, equal-count diagonal
  rewiring, artist geometry/UV/shape-key/mask/modifier preservation, backup
  retention, restore rejection, 6 injected failure cases, and operator Undo/Redo.
- Function-only update from registered 0.64.1 in an isolated background process,
  followed by the new regression suite: passed.
- Independent read-only review: no blockers.

Built dist/character_designer-0.64.2.zip. Scoped deployment updated four files
in both the Blender 5.2 user installation and X validation copy. Subsequent
--check reported all 113 shipped files identical to canonical sources.

The current Blender session was updated by replacing only the two changed
function code objects after exact old-code and deployed-file checks. Runtime
version is 0.64.2. Scene-data/context capture matched before and after; the
existing dirty state remained true. No classes were re-registered. No scene
operator, weight calculation, scene save or application restart was performed
on the user's current scene. Real Stocking weighting results remain for the
user's next Rebind operation; only its binding prerequisites were probed here.

Evidence: D:\Blender\Projects\Character\X\task_artifacts\quick_bind_rebind_20260928.
Previous deployed files: C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260928-103317-57680e49.
