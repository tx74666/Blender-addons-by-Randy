# Character Designer 0.77.57 · Move to Floor

2026-10-18. Release validation is **pending**. Candidate01 passed 39 native
fixtures and the canonical UI checks passed 4 + 13, but the real-model planar
outline check failed. Candidate02 is now frozen with planar display orientation
fitting and must pass its own checks before packaging, deployment or live artist
application. The version remains .57 because it has not been deployed.

## Behavior

The placement area in **Rig > Body** exposes one **Move to Floor** button.
It calculates the lowest currently visible evaluated character mesh point
and moves the recognized character to world Z=0. The distance field, manual
Move button and placement helper text are no longer drawn. Existing script
APIs remain available. Rebuild Controls remains a separate operation and does
not automatically move the character.

The same action places existing planar Foot IK and Master custom-shape
outlines horizontally at Z=0. It changes only their live
`custom_shape_rotation_euler` and `custom_shape_translation`: functional bones,
original Rest/local pose inputs, weights, mesh coordinates, custom-shape
geometry, bindings, scale, colors and saved visual defaults are retained.
Already horizontal displays keep their existing Euler settings. New Foot
outline fitting also omits the former sole-clearance offset.

Root movement and display fitting share one recovery boundary. Display fitting
is followed by the existing character world-transform and mesh-shape checks;
failure restores display rotation/offsets and original root channels. Animated or
driven display settings, modified/keyed/animated widget geometry, external
display transforms, and singular or non-finite displays are refused before
placement. Scene scope, orphan-subtree handling and cross-scene restrictions
remain as accepted in .56.

An optional `align_planes` service flag defaults to false for existing direct
callers; Move to Floor explicitly enables it. Planar widgets rotate as a rigid
display without rewriting vertices, preserving their projected in-plane
heading. Nonplanar artist widgets keep their geometry and rotation and receive
bottom alignment only; their upper points may remain above Z=0. The real-model
check must separately report the current sole/Master outlines' minimum and
maximum world Z, with the original `1e-5` all-vertices limit unchanged.
Exact authored-state and bounded mesh placement
remain the acceptance scope; invariant evaluated IK matrices or FPS gains are
not claimed.

## Frozen source and pending validation

The base is accepted `.56` ZIP, SHA256
`3b6090682cde3d773c477d7031be1e116a8a4e06f6014c96263a46a2e63a7b5b`.
Only five files differ: initializer version, `character_ground.py`,
`rig_fix_ui.py`, the `_foot_widget_fits` function in `limb_ik.py`, and new
`floor_widgets.py`. The other 198 base files retain their accepted bytes.
Paused .48 watcher/provenance/display optimizations remain excluded.

The preserved failed candidate01 manifest SHA256 is:
`653d665a15727d362f6fe437000ca7a01e120eeb8ef02aadb4869733ff345960`.
Its real-model first Ground passed all 19 authored-state/world/surface checks,
and the shoe bottom reached approximately `-3.11e-7` world Z. The Foot outlines
still spanned `0.008324486` and `0.008331951` world Z; their lowest points alone
were at zero. Master was already horizontal. `saved_floor_report.json` remains
failed and unchanged, SHA256
`042c8ff8292d5452009fa2a74ba14f2f369bf9c3a1cd48e008d9e88500d6e218`.
Candidate01 source, projector and native report remain preserved. Candidate02
contains 203 files under manifest SHA256
`32cfac84f0788a8e13670540257c58dc2e3d2be2f3f9eb1ba16c1de329818f94`.
Its saved-model result remains pending; the plane threshold is not relaxed.
The projector checks full-module AST equality outside Ground's `_translate`,
`ground`, `draw` and operator label/description, rig UI's
`draw_body_adjustments`, and limb fitting's one allowed function.

The isolated model input is `input_before_floor.blend`, 34,546,749 bytes,
SHA256 `3f4862be7ad39fcd0bbda62c8d25c2b37be3ecb03004120ad04380addf58689d`.
Its pin records a saved disk copy, not later unsaved artist edits. No artist
Move to Floor operation is recorded by this freeze.

The original ten focused native fixtures cover display-only fitting,
transformed parents, nonplanar shapes, preflight refusals, protected inputs and
failure recovery; their source is retained unchanged. New orientation fixtures
are separate. Candidate02's 44 native checks (10 original display, 5 orientation,
24 placement and 5 scope), saved-model proof, final ZIP/check results,
artist application/save receipt and final concise acceptance receipt must be
added after actual completion. No Git commit, push or publication is claimed.

Evidence: `D:\Blender\Projects\Character\X\validation\move_to_floor_20261018`.
