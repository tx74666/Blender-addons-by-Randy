# Character Designer0.77.138 — reversible Custom key reduction

Locally packaged and deployed to Blender5.2 and X; both241-file checks report zero differences.

**Simplify Keys** creates a new retained Custom row sharing the same Source.
The old Custom and Source stay intact. The existing Source/Custom columns remain;
creation-time key counts and **Before** / **After** compare the two Custom versions.
The dialog defaults are0.0005 position units,0.1 rotation degrees and0.001 relative
scale. Operators use Blender's standard Undo flag.

Reduction requires the current editable Custom and keeps the existing ownership,
session, Rest, physics-idle and mode guards. It does not exit Original or change
Object/Pose mode to bypass a refusal. Complete synchronized LINEAR transform groups
are eligible. Exactly constant raw groups can reduce in supported rig modes;
approximate changes require eligible FK groups. Unsupported groups are copied
exactly. Original knot values, loop-seam neighbors and marker segments are retained.

The native quality gate checks the full private rig's world heads/tails, rotation
and relative scale at authored knots and quarter/half/three-quarter intervals.
This sampled check does not establish a continuous world-space error guarantee.
Before/After revalidates pair/source contents and refuses pending manual keyed-input
pose edits. The unchanged source pose intentionally preserved by creation is
accepted when it had no pending edits. Edited pair versions remain available through
their normal Custom rows.

Final source is complete137+3changed/2new/236exact,241files/209Python. Projection:
`stage138_02/projection.json`, SHA256
`8c23c97cbbd72208466a2b295d9471c0e35517d3c0c14dfca6847e4c15cba7b3`.
The complete137 protections and unrelated approved functionality remain.

Focused pure56 and private native24 passed on this source. Private ordinary keys
reduced896→364; genuine Current-Rig Body keys reduced630→98. These fixture counts
are examples, not promised reductions for artist clips. Native checks include real
curve/publication/binding fault rollback, quality rejection, retained Actions,
Current-Rig checkpoint ownership and private save/reopen comparison status. The
unchanged UI12 checks are included in pure56. The old UI84 suite's28 stale-mock
errors match approved137; their paired receipt and full logs remain and are not
reported as passing. The138_01 source/UI receipts are retained;138_02 backend
changes and final pins have their own quick source peer.

Delivery records:

- Acceptance and SHA256: `D:/Blender/Projects/Character/X/validation/animation_key_reduce_20261101/acceptance_summary138.json SHA256 c9d07518e8ca1d7390158e31d9085e749b1c1e53e2baafbe8e946e6f21e0b115`.
- Official ZIP, dual deployment/check and independent package peer: `D:/Blender/Projects/Character/X/validation/animation_key_reduce_20261101/peer_package_deploy138_02.json SHA256 3db76571720be28cf3045230d90202f6e4f8485608ca9b1a6bf389101a62ef6d`.
- Final release gate: `D:/Blender/Projects/Character/X/validation/animation_key_reduce_20261101/release_gate138.json SHA256 f7360d050a3299f1dec4efa2d912feba39b2e07f7d05fcc3df228ffc5f1e5fa4`.

No artist process was observed, refreshed or trialed for138. Private fixture saves
do not claim an artist scene or preferences save. No physical GUI-event, full artist
author-array, live Hair/Dress transition, runtime/FPS, Unity or Git operation is
claimed. Existing per-frame export and Unity compression are unchanged. The old
playback-FPS branch remains closed.
