# Character Designer 0.77.299 — Neutral Scale channels

Prepared Action cleanup removes redundant Scale F-Curves, including eligible constant and Cycle channels. The stored clip defaults restore unit Scale when the motion is assigned. IK/FK switch and controller-pose keying avoid creating new unanimated unit Scale channels.

Blender 5.2 and the X deployment each match all 329 projected files. On the isolated X file, Scale-only cleanup removed 4,824 curves and 303,787 keys across 27 prepared Actions. Every other F-Curve signature and the frame bounds matched. The maximum tested In Air joint translation difference was 4.43e-6 rig-space units. Native Location & Rotation keying, the controller-limb pose test, and the 14-case IK/FK batch suite passed.

[Delivery receipt](<D:/Blender/Projects/Character/X/validation/animation_scale_channels_20261128/delivery299.json>)

The open X session used the same cleanup routine on all 27 prepared CoshaRig Actions. The cleanup removed 4,824 Scale curves and 303,635 keys, leaving zero Scale curves in those Action slots. Non-Scale curves, frame bounds and the active Action were verified. The Action Editor for `CTRL_hand_IK.L` showed Location, Rotation and `ik_fk` at frame 27. The session uses Location & Rotation keying. The edited scene was left open and unsaved. [Live validation](<D:/Blender/Projects/Character/X/validation/animation_scale_channels_20261128/live_cleanup01.json>) · [Pre-cleanup Actions](<D:/Blender/Projects/Character/X/validation/animation_scale_channels_20261128/scale_actions_before.blend>)
