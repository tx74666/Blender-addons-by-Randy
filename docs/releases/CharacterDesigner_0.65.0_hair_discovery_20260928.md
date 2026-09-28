# Character Designer 0.65.0 — Whole-hair discovery and terminal caps

Local delivery, 2026-09-28. No commit or push. Existing unrelated changes preserved.

## Result

- Select All Hair Strands searches all visible geometry despite an existing
  partial selection. From Selected Tips is the explicit local alternative.
- Discovery builds one shared edge index, skips repeated rails/partitions,
  validates identical core partitions once and stops point growth at the first
  irregular layer. Existing strict validators and selected-only API behavior
  remain; `respect_selection=False` opts into global discovery.
- Small terminal surface disks may accompany a regular closed strand core.
  They require a single owner, complete distal-ring boundary, manifold disk,
  finite coordinates and bounded distal extension. The regular layers,
  signatures and old capture schema stay unchanged.
- Terminal patch vertices are selected and receive the final source-chain
  bone's weight. Head controls the head cap and roots, with the existing root
  blend into each independent chain. No additional head bone is generated.
- Patches are revalidated at bind time; locked deform groups, malformed ends,
  bridges and uncaptured strands still block mutation. Terminal writes are
  covered by the existing binding transaction and reversible removal.
- Seams remain optional and unused as segmentation evidence in this release.

## Real geometry evidence

Read-only live snapshot: Hair, 5,548 vertices, 10,711 edges, 5,177 faces, 70 seams,
42 selected vertices. Three saved captures were present but a subsequent live
probe found them stale after topology edits. The user's existing capture data
was not cleared. Use Recapture Strands then Select All Hair Strands before
binding the changed source. Tests reconstruct disposable geometry from JSON;
they do not load, save or bind the artist's current X.blend.

Same snapshot/Blender 5.2.0 LTS, two threads, sequential runs:

- Immutable 0.64.2 discovery: 130.496 seconds, 32 cores, 3,749 core vertices;
  the 42-vertex existing selection would retain only one strand.
- Optimized discovery: 1.038 seconds with exactly the same 32 core partitions.
  Quad-band calls: 8,191 to 840; strict-plan calls: 520 to 91.
- Full capture/operator plus metadata validation: 1.312 seconds.
- Complete binding: 1.333 seconds; 32 chains / 128 generated bones, 1,782
  head-cap vertices and 17 terminal vertices in five patches (1+1+1+7+7).
  All 5,548 vertices have normalized deform weights.
- Removing all 70 seam markers leaves the same partition. Head translation
  follows across the full mesh with max error 1.19e-7. Removal restores original
  weights exactly; artist geometry, a Shape Key and mask survive.

Timing is one controlled snapshot benchmark, not a universal latency promise.
The technique recognizes regular surface cores and conservative terminal disks;
arbitrary retopology or semantic hairstyle recognition is not claimed.

## Checks and deployment

Serial suites passed: topology (4), whole-hair discovery (8, including 2 reused
checks), terminal patches (6), UI (4), groups/persistence (7), binding guards (5),
mirror controls (8), and focused binding regressions (7). These cover actual
deformation, selection, hidden geometry, artist data, lock refusal, injected
post-terminal-write rollback, save/reopen and reversible removal. The unchanged
legacy generated-copy cleanup subprocess was not rerun.

An isolated registered 0.64.2 runtime was patched to 0.65.0 and passed the new
discovery suite. Independent topology review found no blocker. Test fixture
index-table errors and an initial expected terminal-count arithmetic error were
corrected before final passing runs; terminal disk validation retains manifold
link multiplicity to support inserted valence-two vertices in the real caps.

Built dist/character_designer-0.65.0.zip, SHA256
2216bdaf3752a8c737cc0932ced9081c8bda12a8f180f3a2f0ce0169846e1b58.
Five shipped files changed from 0.64.2: __init__.py, hair_bones.py,
hair_bones_topology.py, hair_bones_binding.py and README.md. Scoped deployment
updated both Blender's installation and X validation copy; --check reported
all 113 files matching canonical sources.

Guarded live update replaced nine function implementations, added three helper
functions and re-registered only the selection operator to add its local-scope
property. Current runtime is 0.65.0. Before/after artist-data and context capture
matched; dirty state was false both times. No scene selection, weight, bone,
mode or file save was performed by the updater. The user continued editing and
saving during this task; historical snapshot values are not current-state claims.

Evidence: D:\Blender\Projects\Character\X\task_artifacts\hair_strands_20260928.
Previous installation backup: C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260928-112539-8ca1f5a0.
