# Character Designer 0.77.68 — independent Parts list

Parts previously displayed the same Custom Vertex selections, so detailed
Strap selections also appeared in the Parts list. The lists now have separate
native catalogs: existing `Custom_` selections remain unchanged, while Parts
uses new `Part_` selections. There is no automatic migration or duplication.

Parts **+** captures the current visible selection in Mesh Edit Mode and
refuses an empty selection. Names, membership, locks, active rows, ordering,
search and menu operations belong to their own list. Weights and Shape Keys
are outside this feature's write scope.

Visibility retains one shared native baseline for compatibility. Parts
**Restore Visibility** clears only Parts requests. A conditional **Restore
Previous Visibility** entry can clear older Custom Vertex requests without
clearing Parts requests. Native metadata remains compatible with the previous
version; no second visibility engine or baseline is introduced.

Native Blender 5.2.0 LTS (`fbe6228777e7`) validation passed: 42 background
regressions, 10 GUI event-loop Undo/Redo tests, and full register/idempotent
register/unregister/re-register checks. Tests use generated fixtures and
isolated preferences, including exact weights, geometry and Shape Keys checks.

History validation also exposed an older visibility issue: the native selection
setter ignores hidden elements. Visibility now writes selection before the
complete hide plan, using scalar snapshots that survive native layer changes.
Rollback uses the same ordered writer. A separate native control confirms that
Blender's active attribute index does not follow BMesh layer reorder Undo;
membership, locks and visibility restore exactly, while active-row restoration
is promised for explicit catalog switches, not native index history.

Accepted evidence: X `validation/independent_mesh_parts_20261019/candidate04`.
The complete 207-file projection uses accepted 0.77.67 plus the four reviewed
feature files; unrelated pending source changes are excluded. The 5.2 user
installation and X validation copy both pass `deploy_local.py --check` with
zero differences. Artist UI refresh and visual interaction remain deferred;
this task did not operate or save the artist scene or preferences.

Local source and installation only; no staging, commit, push or publication.
