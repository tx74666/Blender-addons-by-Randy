# Character Designer 0.70.2: Native Topology Mirror repair

Refine Symmetry reads Blender's actual Topology Mirror table through isolated
native Select Mirror calls. It replaces the custom correspondence algorithm
for this tool. Coordinate repair supports X/Y/Z, with Left to Right as the
default. The compact panel keeps axis, direction, selected scope, Preview and
Refine visible; Analyze and tolerance controls are under Advanced. Unmatched
vertices remain unchanged and their count stays visible in results.

Directional repair copies the source coordinate with the selected axis
negated. Average minimizes movement of both selected vertices. Every Shape
Key retains its old Basis-relative delta. Topology, indices, UVs, weights,
binding relationships and unselected coordinates remain protected. Native
lookup IDs are disposable, and the small cache retains only Python indices
keyed by full connectivity. No persistent helper assets are generated.

## Verification

- 10 native correspondence/repair regressions pass, including X/Y/Z oracle
  comparisons, read-only unsynchronized Edit buffers, immediate native
  transforms with Topology Mirror disabled, cache invalidation, temporary
  cleanup and failure rollback.
- 14 original repair tests pass, including Shape Keys, protected mesh data,
  selected scope, Preview and Undo.
- Four refresh lifecycle scenarios and six UI page checks pass on 0.70.2.
- A read-only snapshot of the current unsaved Cosha selection was copied into
  an isolated factory scene; the artist blend file was neither loaded nor
  saved. Selected indices 608 and 1779 are reciprocal native topology pairs.
  Their initial Basis mirror error is 0.0002447888236 local units. Native
  Topology Mirror finds them in both directions; ordinary mirror initially
  finds neither. All three repair modes produce zero pair error, exact
  preservation of all 12 Shape Key deltas, and unchanged coordinates outside
  the affected one/two vertices. Ordinary Select Mirror and immediate G X
  succeed in both directions in the same Edit Mode with Topology Mirror off.

The old custom mapper also found this specific pair. The snapshot establishes
its current geometric drift and the new repair's behavior; it cannot establish
why the earlier interactive repair did not remain effective. Across this
snapshot the native method finds 1167 cross-plane pairs, versus 1160 for the
custom mapper. Native unmatched regions are not silently guessed or rebuilt.

Evidence remains local under X/validation: cosha_live_refine_native_check_20261002.json,
refine_native_pair_tests_20261002.log, refine_symmetry_tests_20261002.log,
character_designer_refresh_0702_20261002.log and
character_designer_ui_pages_0702_20261002.log. Snapshot SHA-256:
ce37fd6b8fd21c5cbab8edbe577eb485f6a8815fcada4199cb37791f1548d004.

Release and deployment are local. The owner handles GitHub Desktop Commit
and Push.

## Deployment and live read-only check

The 0.70.2 package contains 129 files. Canonical source, Blender's installed
add-on and X's validation copy match, with zero different files. The current
X session refreshed to 0.70.2 with no refresh error. Native Analyze reports
one matched, one misaligned and zero unmatched vertices for the retained
two-point selection; Left to Right plans only vertex 1779.

The complete protected-model/selection fingerprint matches before and after
refresh, Analyze and planning. Object/mesh/scene counts and artist mirror
settings also match, proving temporary lookup cleanup in the live session.
The current model was not refined or saved; the two selected points were
left for the artist's chosen repair direction. Evidence:
X/validation/refine_0702_live_refresh_20261002.json.
