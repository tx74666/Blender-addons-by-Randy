# Edit one part and place the character

The Body panel keeps regional editing separate from character placement.
Changing the character's height above the ground does not require rebuilding
its skeleton or remembering the hands' positions.

## Already edited bones while Original is active

If native leg, foot or toe geometry was adjusted in **Original** before releasing
controls, use **Controls** to return to the existing controls. The return accepts
verified Direct source geometry changes, matches the saved controls to the
current native pose, and retains the artist's new Rest, mesh coordinates,
weights, existing controller identities and unrelated limbs. Do not restore the
old animation rig or delete the edited native bones to finish this step.

This is a pose-preserving return, not regeneration of reverse-foot pivot spacing.
It keeps existing generated helper Rest geometry. Check Foot Roll and Toe Bend
after changing foot proportions; a deliberately different heel/ball/tip layout
requires rebuilding that leg through the regional workflow. Existing animation
recovery records retain their old Rest baseline and are not silently retargeted.
Structural edits, unowned dependencies and unverifiable recovery owners are
refused while the edited Original state remains available.

## Regional controls

Under **Edit One Part**, choose a left/right leg or arm, or both legs/arms.
**Release Controls** removes the selected generated limb controls and related
foot controls while retaining native deformation bones, weights and the current
native Rest. Shared Root, spine, eyes and the other limbs stay installed.
The released native bones appear in a temporary **Edit Left/Right Leg/Arm**
bone collection and can be adjusted directly in Edit Mode.

After editing the native bones and the corresponding model proportions,
leave Edit Mode and use **Rebuild Controls**. This accepts the edited Rest
without automatically applying whole-body calibration or re-planing the bones.
Release receipts and part selection persist when the file is saved and reopened.
The status reports bound, released, missing, mixed sides or an error. A missing
part has no release operation; initial generation remains in the existing setup
workflow. Complete a released part before using whole-body Update/Remove or the
legacy all-limb maintenance commands.

The current regional solver supports Direct schema 5. Arms include their wrist
control and native hand descendants; legs include feet and native toe descendants.
Head/neck and torso/spine do not yet have an independent regional teardown in
this version. Native names, bone inventory, parents and connection/deformation
flags must remain intact. Hips and unrelated auxiliary branches are protected.
An edited knee/elbow needs a stable small Rest bend. Mesh weights are retained,
not rebound automatically.

Selected authored animation or external dependencies are reported before
deletion. Independent animation on other limbs is retained. An edited arm with
a correction calibrated to its old Rest must have that old correction removed;
rebuild the arm, then capture its correction again. Old animation recovery
records are preserved rather than silently retargeted to new proportions.

## Character placement

**Vertical Distance** is a signed distance along world Z. Positive moves up;
negative moves down. **Move Character** applies that distance to the recognized
character once. **Ground Character** computes the lowest evaluated vertex of
the visible character models, including footwear, and moves it to world Z=0.
Grounding uses the currently displayed pose. Use the intended Rest display when
placing a newly proportioned character.

Placement changes only the outermost character objects' translation. Parented
meshes, bone attachments, skeletons and their control bones follow together;
detached meshes bound to the recognized rigs move with them. It does not reparent
objects, modify mesh/Shape Key coordinates or weights, edit native Rest bones,
change the local hand pose, or rebuild controls. Hips has no special exclusion:
its local pose values can stay at zero while its world position follows the rig.

Widgets and backup/reference geometry are excluded from floor detection.
Ambiguous attachments, active model-editing recovery sessions and transform
dependencies that cannot safely move as a unit receive a specific refusal.
Object and evaluated geometry checks must confirm the requested translation;
failed placement restores the original object channels. Both operations support
Blender Undo. The two placement buttons do not create animation keys.

Hidden bound models move too, but do not determine the floor. Hidden world-space
or unknown modifiers and driven inputs require showing that model first so its
evaluated shape can be checked; the operation does not change visibility itself.

Native evaluated geometry is not promised to be bit-for-bit identical after
translation. Blender's float32 Hook/Spline IK evaluation can amplify tiny Curve
rounding changes even when the authored curve, original bones and pose channels
are unchanged. Each mesh uses an explicit world-coordinate component error limit
of `2e-5 + world_bbox_diagonal * 1e-4`; the lowest-point check uses the largest
limit among the originally visible meshes. The placement receipt records each
mesh's bounding-box diagonal, limit and measured maximum error, plus the expected
and measured floor height. This numerical allowance applies only to evaluated
mesh output and floor height. Object matrix checks and exact authored Rest/local
channel checks remain in force. Larger deformation still cancels and rolls back
the operation. Child-object animation and drivers may remain intact; animated or
driven transforms on the outermost placement objects are refused.

Use normal Blender saving after completing scene work. Deploying the add-on
does not apply proportion changes or a placement offset to an artist file.
