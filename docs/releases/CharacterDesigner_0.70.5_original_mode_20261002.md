# Character Designer 0.70.5 — Original mode

Rig > Body > Body Controls now contains an explicit **Original** button. It
opens native body bones in Pose Mode, temporarily suspends only the validated
Character Designer constraints that control those bones, and temporarily
unlocks their rotation channels. The evaluated pose is preserved using native
parent and inheritance conversions. **Back to Controls** matches the current
native pose to the saved control modes and restores the previous display,
rotation locks and constraint mute states.

**Edit Weights** remains available inside Original. Finish the weight workspace
with **Back to Original**, then return to the body controls. Weight edits remain.
The session is stored on the rig and survives save/reopen, add-on refresh and
Undo/Redo. Structural generation and collection reorganization are blocked
until the direct-pose session has finished.

Returning to controls also hides the registered Foot Auto Align reference
bones, including when an artist collection still contains them.

Native Bone Collection eyes and solo stars now only change native visibility.
The former message-bus/deferred visibility preset switching and its 0.2-second
polling timer are removed. Original posing is operated from Body Controls;
Weight retains its existing display-only Native Bones preset.

This mode does not change Rest bones, parents, names, skinning, mesh topology,
Shape Keys, constraints' configuration, driver expressions or animation curves.
An incompatible legacy rig or a native transform driver is reported before
entry. A current native pose that the saved IK/BLEND configuration cannot
represent cancels return, restores the editable Original session and keeps
the authored pose. It never silently falls back to another control mode.

Validation uses disposable Blender 5.2 scenes: direct native rotation, FK and
mixed IK/FK return, torso/spine/root/toe extensions, actual Weight Paint edits,
save/reopen, Undo/Redo, ownership preservation and injected failure rollback.

All relevant checks passed: 8 Original workspace cases, 6 native visibility
cases, 14 pose/weight workflows, 6 weight workspace recovery cases, 11 display
cases, 4 Refresh lifecycle cases and 6 UI page cases. Refresh's injected enable
failure prints an expected traceback, followed by a successful retry.

Live Cosha verification: 56 Original bones, 23 precisely suspended source
constraints; thigh.L rotated in direct Pose Mode, then the probe rotation was
restored. Returning without edits reproduced the exact saved pose channels
with zero matrix error. Cosha, Clothes, Stocking, Shoes and Hair retained their
mesh/Shape Key/weight/binding fingerprints; native Rest remained identical.
An active Original session survived the final add-on Refresh and another
return/enter roundtrip. The artist's X.blend was saved with Original active;
Blender reported successful saving at 18:36:31 on 2026-10-02.

Source, Blender installation and X validation copy contain 130 matching shipped
files. Local build/deployment only; no Git commit, push or GitHub publication.
Live evidence is in X/validation/original_0704_live.json and
X/validation/original_0705_live.json; the first probe's scene backup is retained
separately in X/validation/X_before_original_0704_20261002.blend.
