# Unreleased: compile channel capture metadata once per bake

Based on the approved Character Designer 0.77.87 projection. Canonical change:
`addons/character_designer/unity_animation_controls.py` only. This is a
validated source patch, not an installed 0.77.88 release.

A long clip previously rebuilt identical FCurve paths, rotation modes,
connected flags and driver destinations for every sample. The bake now
compiles that metadata once, within the existing try/finally and only for that
invocation. Sampling, solver updates, native exactness checks, finite/shear
refusals, continuity and cleanup remain unchanged.

Eight focused old/new tests plus four runtime guard checks pass. A tiny
factory-only Blender 5.2 native run compares all six Euler modes with exact
outputs and clean failure recovery. Capture-only wall time in one bounded
AB/BA microbenchmark was 11.47→6.65ms/100 samples. This does not measure complete
Idle import, dependency-graph cost or FPS.

The accompanying independent workflow snapshot tool has 18 passing tests and
avoids full chat-history payloads during routine status checks.

Release integration must start from the complete approved 0.77.87 projection,
add only this validated runtime file, and include the ordinary version metadata
change. Preserve canonical paused body_region development. Build, deploy with
the explicit projection and --check, and refresh the artist only in the
owner-coordinated window. Existing artist data and 0.77.87 signatures were not
modified by this task.

Evidence and pins: D:/Blender/Projects/Character/X/validation/workflow_speed_20261022/.
No stage/commit/push. Full import speedup remains unmeasured.

