# Character Designer 0.61.47 — One preview side, bilateral application

All Finger Setup viewport guides now use the active captured side. Preview L/R
switches reference axes, joint rings, bone destinations and bend guides together.
Highlighted digits and paired parameters survive the switch. Camera movement
does not select sides or regenerate geometry. The hidden side is filtered before
reference evaluation and joint/subdivision planning, not merely hidden at draw.

Both geometry stages still validate and apply both sides. Invalid hidden-side
bindings do not disable a valid visible-side reference, but still block writing
an incomplete pair. Master/child eye intent is preserved; a pending bend refresh
cannot resurrect a preview explicitly switched off.

## Validation

54 cases passed across seven serial Blender 5.2 suites: single-side scope (7),
reference cache (5), multi-selection/save-reopen (5), combined preview (6), joint
eye/monitoring (11), master eye (8), and two-stage geometry/Undo (12).
The new scope test confirms both sides' four rest junctions move in Step 1 and
both sides receive new support loops in Step 2, with artist Shape Keys and
unrelated weights preserved. Warm side switches do not reevaluate references;
1,000 cached visibility queries per side do not rebuild plans.

On the 640-vertex synthetic benchmark, warm reference switches had a median of
0.49 ms, combined joint drags 6.89 ms and idle checks 0.13 ms. Warm drags/idle
performed zero reference rechecks. These are fixture timings, not real-model
frame-rate claims. Both benchmarks ran immediately before the version bump.

Changes are local; no commit or push was performed.

## Live deployment

Built the immutable `dist/character_designer-0.61.47.zip` (108 files). The Blender
installation and X validation copy both pass `deploy_local.py --check` with zero
different files. Reloaded the existing X session without recapturing definitions.
Its 3,424-vertex, 10-Shape-Key Index preview contains only `INDEX.L` when L is
active and only `INDEX.R` when R is active, including evaluated level-1 contours.
Both preview statuses are empty. First explicit rebuilds took approximately
0.86/1.05 seconds including full source proof and cold caches; this is not a
per-frame cost. Mesh identity/full fingerprint, rest bones, key values, paired
parameters, recipes and confirmed results are unchanged. No blend save or
geometry generation was performed. The original left-side selection was restored.
The existing geometry/binding limitations documented in 0.61.46 remain.
