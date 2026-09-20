# Character Designer 0.61.39 — further bounded preview optimization

## Change

The previous packed equality proof treated INT16_2D as an unfamiliar attribute.
On saved X this meant reading 13,352 packed custom-normal values individually,
constructing Python tuples, then decimal text, on every drag refresh. These
integer vectors now use exact int32 bulk reads. Boolean RNA buffers use byte
arrays; UV layers use the direct vector collection with the old API as fallback.
Other unfamiliar types retain their existing conservative fallback.

Only the transient read-only equality representation changes. Persistent
fingerprints, complete-data coverage, write-time checks, geometry algorithms,
preview invalidation and workflow controls remain unchanged.

## Verification

- 36 focused headless cases passed: proof/performance 6, eye/lifecycle 8,
  workflow 12, preview-cache 5, multi-selection 5.
- New regression covers signed short-vector extremes and single-unit changes,
  bounds per-element Python reads to attribute count (not corner count), and
  verifies hidden/seam/sharp/smooth/boolean and Object/Edit Mode UV changes.
  Existing tests retain custom-normal, weight, shape-layer and full-write guards.
- Two alternating no-save background benchmark comparisons on saved X:
  installed 0.61.38 median 135.84 -> candidate 101.22 ms, and 119.69 -> 88.23 ms.
  This is approximately 25-26% lower CPU time. Each runs the same 30 updates.
  Load varies, so these are paired same-round comparisons, not a claim that
  absolute timing must be below the prior session's 90.35 ms.
- Every measured frame hash remained
  `1af3e518ecb3f138cbb19037c886975baa3a6ba5339a154b50f44830808a1abf`.
  Mesh fingerprint and original X.blend SHA were unchanged. Embedded scripts
  remained disabled, and the user's working window was never modified.
- Isolated synthetic Blender GUI native mouse drag test: PASS, 138 drawn frames,
  zero blank joint frames, zero redundant Basic Setup rechecks; 24 rebuilds
  averaging 8.74 ms (7.95-10.57 ms). Screenshot inspected with both ring markers
  and the Basic Setup axis visible. This is not the real-X GUI or display latency.
  Initial test missed the widgets because it assumed x=800 on a 1100px window;
  the harness now uses the actual sidebar region, then passed native drags.
- Release package built; both runtime and X validation deployments match;
  local deployment only, no Git commit/push. Existing backups retained.

The remaining complete-model data/snapshot cost is not removed by weakening
validation or introducing a speculative new caching scheme in this round.
