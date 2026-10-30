# Pose assets, blending and animation offsets

Character Designer keeps native Pose Action assets. It matches their target
poses to the current generated controls during an explicit operation; it does
not install a playback-time pose-matching service or change the Rest skeleton.

## Blend the current pose

In the Asset Browser, right-click a Pose and choose **Blend Pose** or **Blend
Pose Flipped**. On a controlled character these entries use the same native-pose
compatibility rules as Apply, with a fixed starting pose and a 0–1 strength.

- Move the mouse horizontally; Shift gives finer control.
- Type `0.5` for half strength. Arrow keys adjust by .01, or .001 with Shift.
- Ctrl switches the mirrored target from the same starting state.
- Enter or left-click keeps the result. Esc or right-click restores the start.

Every preview is calculated from the captured start, not the previous preview.
Rotation uses quaternion interpolation. Selections retain partial native-bone
intent; a selected public limb control resolves to its native chain. Assets
already authored directly on free control channels retain Blender's native path.
An incompatible Rest, external pose edit, changed frame, driven destination or
unrepresentable IK match is refused rather than changing the rig's mode silently.

Preview does not insert keys. Confirmation with Auto Key enabled keys once.
Read-only worklist Source animations cannot be keyed. The editable Custom and
Pose-test paths preserve their authenticated Action/slot/recovery relationships;
normal scene edits must still use their intended animation context.
Standard Apply Pose also uses this protected publication path when native-bone
control matching and Auto Key are needed in a worklist or Pose-test context.

The 3D Pose Asset Shelf **context menu** is also routed. Its direct thumbnail
click/drag retains Blender's native routing; use its context menu for controlled
native-bone assets. No existing Shelf RNA type is replaced or re-registered.

## Offset a whole animation

Select the animation's editable **Custom** version, stop playback and leave
temporary Pose/Original/Composition previews. Right-click a Pose and choose
**Offset Animation with Pose**. Set Strength (default .5), Region and Mirrored.
The default Arms region excludes fingers. The operation creates and activates a
new Custom version; its Source and previous Custom stay available.

This applies one fixed **Rest → Pose** local-space change across the clip. It
does not pull every frame toward one fixed pose, so the original swing remains.
The initial implementation requires supported FK animation channels throughout
the affected region. An IK or animated FK/IK mode, mode driver, unsupported
constraint, affected curve modifier or active NLA composition is rejected. Merely
switching the current frame to FK does not convert an IK animation to FK.

Affected channels are sampled at integer frames and their original authored
key times, with continuous quaternion signs and linear output interpolation.
Unaffected curves retain their native settings and handles. The original clip is
kept; exact affected-channel Bezier/subframe equivalence is not promised. Strength
zero makes an exact curve copy without resampling. This is an explicit preparation
step, not a per-frame modifier or an AI-generated replacement animation.
Mirrored offsets from older assets require complete local transforms; partial
legacy captures are refused instead of filling channels from the current frame.

No weights, Shape Keys, Rest data, physics settings or existing FPS optimization
code are changed by these tools. New animation samples can affect file size and
curve-evaluation cost; functionality tests are not a measured FPS claim.
