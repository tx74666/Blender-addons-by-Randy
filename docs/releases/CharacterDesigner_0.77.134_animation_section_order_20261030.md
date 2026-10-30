# Character Designer 0.77.134 â€” Animation section order

Original Rigging Inputs appears below Animations on the Animation page. Both
sections retain their existing independent fold state and controls, including
when Animations is collapsed or its content returns early.

This display-only change extends complete133 by animation.py, package version
and README (239 files, 236 inherited exact). The original Animations content is
AST-identical inside a local helper; all Natural Rest, Dress, Foot, Forearm and
Hair behavior is retained.

Focused proof: six actual-UILayout cases, eight checks, clean GUI04; final
single-function updater passed selected-pose-owner, rollback, commit and
already-candidate checks in a private native fixture. Preserve GUI01 draw-time
RNA-write error, GUI02 native C crash (stale fixture RNA references suspected; exact cause unproven), and GUI03
late-poll fixture error; later GUI04 fixes do not overwrite those records.

Approved projection and detailed delivery receipts:
`D:/Blender/Projects/Character/X/validation/original_inputs_below_animations_20261030/`.
Official build and 5.2/X deployments match all239 files. Live update and saved
scene status are recorded separately in the acceptance entry; deployment alone
does not establish a runtime refresh. No scene/preferences save or Git upload.
