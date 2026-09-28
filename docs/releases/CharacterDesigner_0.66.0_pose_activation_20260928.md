# Character Designer 0.66.0 — Pose activation and mirroring

Double-left-click applies a Pose asset; Shift-double-left-click applies it to
the mirrored side. The Asset Browser context menu also includes explicit
Character Designer normal and mirrored commands.

## Cause and fix

The live X rig has generated body controls but no `character_designer_pose_rest_v1`
baseline. Its Fist asset keys 15 original left-finger bones. The prior double-click
router required a baseline even for those unconstrained fingers, while native
Apply Pose could apply them normally. The existing shortcut was registered and
its poll passed; this was an application rejection, not an absent mouse binding.

Unconstrained native target bones now delegate to Blender's native asset operator.
Constrained native targets retain rest validation and generated-control matching;
the missing baseline is not silently created or bypassed for that path. Mirroring
for the matching path uses Blender's rest-aware Action flip on an unattached,
temporary Action, with cleanup on success and failure.

Native mirror application originally did nothing when only the source fingers
were selected. If no destination-side asset bone is selected, the wrapper now
temporarily mirrors the selected source subset for the native call, preserving
unrelated/center selection and restoring the artist's selection afterward.
Already selected destinations retain Blender's selection behavior.

The feature adds two event-driven keymap entries and no polling or gesture timer.
The existing one-shot official-keymap routing remains, and unregister restores
the official default double-click binding.

## Validation

Blender 5.2.0, disposable processes run serially:

- 6 shortcut lifecycle/delegation tests.
- 13 activation, constrained guard, selection restoration and menu tests.
- 9 mirrored transform, rest-roll, rotation-mode, partial-channel, cleanup and
  rollback/autokey tests.
- 14 existing control-pose/weight workflow tests.

Independent GUI fixture: generated-control marker, no compatibility baseline,
one selected left finger and a local Pose asset. Actual mouse double-click applied
the left pose; clicking the Character Designer mirror menu applied only the right
pose and preserved left selection. Both operations could be individually undone
with Ctrl-Z in the 3D View. Measured local fixture operator calls were about 2.4 ms
normal / 1.0 ms mirrored; these are not timings for external libraries or full-body
controller matching. The Shift binding and its `flipped=True` property were checked
in RNA and the GUI menu; the automation API cannot hold Shift during a mouse click,
so a physical Shift-double-click was not simulated.

GUI setup initially hit a Blender file-browser draw crash when changing editor
type before UI initialization. Deferring fixture setup until after startup resolved
it; this fixture setup is not shipped in the addon. Artist Blender instances were
not used for pose mutation tests.

Evidence: `D:\Blender\Projects\Character\X\task_artifacts\pose_activation_20260928`.
Release package and both local deployment copies match (114 shipped files,
zero differences). Only the pose router was hot-reloaded into the running X
instance. Before/after checks preserve mode, active object, action/object lists,
all CoshaRig pose bases and the absent baseline. Live Fist preflight confirmed all
15 left fingers and mirrored right destinations use the native route, taking
about 26 ms including external asset inspection. No pose was applied to the artist
scene during this verification. All owned GUI/background test processes exited.
Source and release are local; no commit or push is included.
