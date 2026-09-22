# Character Designer 0.61.48 — Cached overlay visibility

Closing the master eye previously discarded reference caches and joint drawings.
Reopening repeated full source validation; if the prepared source had changed,
the joint preview could remain absent even though its eye was enabled. Visibility
now retains and restores the same line buffers, GPU batches and reference frames.
The joint eye and warmed L/R views use the same bounded in-memory cache. The wide
Preview L/R button is replaced by a square double-arrow and a side-aware tooltip.

Actual mesh, rig and reference changes invalidate retained drawings, including
while hidden. Parameters are part of the cache identity. Undo/load/reload clear
the cache. No mesh copies or persistent bake are added; at most 24 drawings are
retained. Source validation and final-surface planning still run for cold/changed
views and all geometry commits retain their independent checks.

If preparation no longer matches the current artist data, saved target markers
remain visible in orange with an explicit "Reference only (not current)" label.
They are comparison references, not current evaluated subdivision contours. This
does not rebase or overwrite new weights, Shape Keys or mesh edits; Apply still
refuses an unvalidated source.

## Validation

Targeted serial Blender 5.2 suites pass: visibility cache (6), master eye (8),
joint eye/monitoring (11), single-side preview (7), combined preview (6), reference
cache (5), multi-selection (5), and modifier preview (5): 53 cases total.
The new suite exercises repeated master/child/side toggles, exact retained-buffer
identity, hidden weight edits, hidden parameter edits, invalidation and cold
changed-source markers. It forbids source proofs and plan builds on warm toggles
and verifies that stale reference drawings cannot authorize Apply.

## Current X session measurements

Blender 5.2, Cosha in Edit Mode, 3,424 vertices and 10 Shape Keys. The initial
Ring preparation already differed from the live model. Before the change,
reopening the master took 714.86 ms and still left no ring drawing. After reload,
the two joint references remain visible, explicitly marked as stale.

Twenty repetitions of each operation after its initial build:

| Operation | Median | Maximum |
| --- | ---: | ---: |
| Master off + on | 5.738 ms | 6.532 ms |
| Joint eye off + on | 0.478 ms | 1.018 ms |
| One warmed side switch | 0.403 ms | 0.769 ms |

These measure operator callbacks plus a view-layer update and pending refresh
flush, not input-to-screen latency or viewport frame rate. Each operation left
the rings visible; master/child cycles restored the exact same drawing object.
All warm groups recorded zero source-proof and topology-planner calls. Full mesh
fingerprint and rest-bone matrices match before/after; the original RING.L
selection was restored. No blend save or geometry application was performed.
Local raw reports: X/outputs/eye_before_20260921.json and
X/outputs/eye_after_20260921.json.

Changes are local; no commit or push was performed.

## Deployment

Built and verified immutable `dist/character_designer-0.61.48.zip` (108 files).
The Blender 5.2 installation and X validation copy both pass
`tools/deploy_local.py --check` with zero different files. The existing live
Blender session reports version 0.61.48. Whitespace validation passes.
