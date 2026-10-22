# Character Designer 0.77.81 — inspection and control display

Bone influence inspection previously scanned every reference vertex separately
for each target bone's usage count, then scanned it again to find islands.
Larger bone sets now share a positive-weight index within one inspection of
the current validated snapshots. Targets using four or fewer bones keep the
original reference-scan path. This preserves the small Shoes case observed in
X while removing repeated scans for larger sets such as Cosha's 56 used bones.

The index is local to one call. Full input validation, strictly positive tiny
memberships, per-mesh deform sets, result ordering, main-island decisions,
distances, warnings and error behavior remain intact. Fill, Clear, donor search
and application/rollback algorithms are unchanged.

Show Controls and native bone isolation also reuse the main rig's successful
validation within the same uninterrupted read-only preparation. Empty saved
views avoid a redundant restore call. Every new operation still validates live
data; active-view restoration retains its own reference and view-layer checks
and rollback. In the no-active-view native fixture, main validation calls fall
from three to one. Fixture timings were mixed, so this is a verified reduction
in repeated work, not a general latency or FPS claim.

Validation and paired saved-X measurements are indexed in
`D:\Blender\Projects\Character\X\validation\optimization_20261020\README.md`.
The final acceptance summary records exact inputs, all source pins, raw timing
samples, deployment and process cleanup. Initial mixed measurements are retained;
they motivated keeping the original small-set scan.

The release inherits the complete accepted 0.77.80 package and changes only
`weight_cleanup_core.py`, `bone_display.py`, version and package README. The
previously paused `body_region.py` candidate remains excluded. Sources remain
canonical in this repository; the complete approved projection is used by the
official build and deployment tools.

Local release and deployment only. The open artist session is not refreshed,
edited or saved by this task; the installed update loads on the next normal
Blender restart. No commit, push, PR, Unity change or published release.

Saved-X final observation (four alternating pairs): Cosha inspector wall median
7488.31 → 6371.85 ms;
process CPU 7125.00 → 6171.88 ms.
Shoes kept the baseline small-set path and had identical median process CPU
5351.56 ms. All results were exact.
44 existing pure, 7 new, 28 native weight tests, 14 native public display tests
and 18 paired/guard cases passed. Both deployment checks reported 212 files,
zero differences. Shared-load function measurements do not establish UI FPS.
