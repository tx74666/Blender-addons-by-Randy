# Character Designer 0.77.120 — Pose assets

Native-bone Pose assets now blend through generated controls from a fixed captured
start. Asset Browser Blend Pose accepts `0.5`, Enter confirms and Esc restores.
Auto Key on supported Custom/Pose-test edits publishes an authenticated new
version. Standard Apply uses the same owner-aware publication when matching is
needed, preserving recovery tokens, old drafts and worklist associations.

Offset Animation with Pose applies a fixed Rest-to-Pose delta to a new Custom,
retaining the original swing, Source, old Custom and current-Rig history. It
requires supported native FK. Affected channels use integer-frame plus original
key-time sampling and linear interpolation; exact Bezier/subframe equivalence is
not promised. Legacy mirrored offsets require complete local transforms.

Asset Browser and Shelf context menus are adapted. Shelf thumbnail activation
and dragging retain their native route. No playback handler, polling timer or
runtime AI dependency was added. Approved118 physics/Forearm/FPS code is retained.

Complete228 files/196 Python extends118 by four changed and three new files;
221 inherited files are byte exact. Projection SHA256:
`5a427443fb8a9c6f751ac0daf4695b6f836d0f2b42da25c96685abe64361a7a7`.
The official ZIP and Blender5.2/X deployments match all228 files. The earlier119
ZIP remains an unreleased candidate; immutable same-version packaging was kept.

Validation:53 pure tests,32 native Core/Apply cases and29 offset cases, with
function/file inheritance explicitly recorded. Private GUI07 proves actual Asset
Browser menu/context invocation, numeric preview, cancel and native Undo/Redo.
Private120_04 reached three requested injected faults and restored exactly, then
completed successfully. Earlier failures and false fault-label booleans remain
documented separately; later success does not relabel them.

Artist14840 updated118→120 through six retained function objects, three new pure
modules and two new operators. All4602 existing callable bindings and333 existing
RNA identities were retained; synchronous context, native pose/Rest/collections,
bindings and physics state matched. The main view and Outliner were restored.
No artist Pose/offset/physics action, scene/preferences save, full reload, Unity
or Git upload occurred. Full artist author arrays, dynamics and FPS were not
accepted or benchmarked.

[Usage](../pose_asset_workflow.md) ·
[Final evidence](D:/Blender/Projects/Character/X/validation/pose_assets_blend_20261029/acceptance_summary120.json)

