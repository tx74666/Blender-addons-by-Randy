# Character Designer 0.77.125: selected Hair strands

A segment change can now affect one bound strand or an explicitly selected pair.
In Pose Mode, select any bone in each target strand, choose **Change Selected
Strands**, and set **New Bones per Strand**. Other strands retain their counts,
paint, Rest, native animation and settings. After creation, only the changed
chains are selected, allowing a different strand to be adjusted next.

**Selected Strands** is the default; **All Strands in This Mesh** is explicit.
The dialog shows current counts and fixes source/rig identity and target chains.
It refuses empty, non-Hair, mixed-source or changed selections. Native Mirror
pairs sharing source geometry must be selected together. Separate physical
strands can be adjusted independently. Original versions remain available.

Partial versions copy native Action slots/NLA/constraints/drivers and remove only
the selected old-bone channels. Only new selected transforms are sampled. Current
source and copied author values, compatible manual pair proofs, group settings
and overrides are preserved. Unsafe reverse dependencies or complex active Action
blending refuse before mutation. Changed bending/motion remains an approximation;
cleanup retains its temporary-animation and ownership guards.

The complete approved124 baseline is retained with four changed files and two
new modules: 234 files, 202 Python. Validation includes 9 selection cases,
13 selected native cases with original driver/constraint witnesses, 13 existing
whole-mesh native cases, and five GUI Undo/Redo operations. Early fixture failures
and pinned derivatives remain. Runtime files in the final125 projection exactly
match the tested private stage. Official build, both deployments and check passed;
an independent peer compared every ZIP member and both targets byte-for-byte.

This release did not refresh or mutate the open artist session, save its scene
or preferences, run Unity, or stage/commit/push Git. Disk deployment does not
prove the user's current Blender has loaded125. No artist dynamics, collision,
loop or FPS acceptance is claimed.

[Acceptance and raw evidence](D:/Blender/Projects/Character/X/validation/hair_selected_strands_20261029/acceptance_summary125.json)

[Approved complete projection](D:/Blender/Projects/Character/X/validation/hair_selected_strands_20261029/stage125_01/projection.json)
