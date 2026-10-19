# Edit one part and place the character

The Rig navigation contains **Body**, **Hair** and **Skirt**. There is no separate
Fix tab. Open **Rig > Body** for the shared body-part workflow, control visibility
and character placement. Changing the character's height above the ground does
not require rebuilding its skeleton or remembering the hands' positions.

## Already edited bones while Original is active

If native leg, foot or toe geometry was adjusted in **Original** before releasing
controls, use **Controls** to return to the existing controls. The return accepts
verified Direct source geometry changes, matches the saved controls to the
current native pose, and retains the artist's new Rest, mesh coordinates,
weights, existing controller identities and unrelated limbs. Do not restore the
old animation rig or delete the edited native bones to finish this step.

This is a pose-preserving return, not regeneration of reverse-foot pivot spacing.
It keeps existing generated helper Rest geometry. Check Foot Roll and Toe Bend
after changing foot proportions. Use the foot-only selection when only the
heel/ball/tip helpers need rebuilding; choose the leg when its IK and Pole also
need rebuilding around the current native positions. Existing animation recovery
records retain their old Rest baseline and are not silently retargeted.
Structural edits, unowned dependencies and unverifiable recovery owners are
refused while the edited Original state remains available.

## Regional controls

The regional **Edit One Part** workflow uses one **Body Part** selector. The
original six choices are **Both Legs**, **Left Leg**, **Right Leg**, **Both Arms**,
**Left Arm** and **Right Arm**. The same selector also offers **Both Feet**,
**Left Foot** and **Right Foot** for foot controls only.

When the selected part says **Controls Ready**, use **Rebuild Controls** directly.
It rebuilds only that existing part around the current native bone positions,
preserving the current deformation and supported custom control styles. There
is no preliminary Release step. A leg selection includes its leg IK, Pole and
foot controls; an arm selection includes its wrist controls. Shared Root, spine,
eyes and the other limbs stay installed.

Use the secondary **Release to Edit** action only when you still need to change
the selected leg or arm's native Rest geometry. It removes that part's generated
controls while retaining native deformation bones, weights and current Rest.
The native bones appear in a temporary **Edit Left/Right Leg/Arm** bone collection
and can be adjusted in Edit Mode. After editing the bones and corresponding model
proportions, leave Edit Mode and choose **Rebuild Controls**. For a **Released**
part this button completes the saved rebind, without applying whole-body
calibration or re-planing the bones.

The selected part's supported custom-shape mesh geometry, display offsets,
scales, colors and flags are checkpointed when it is released. These display
backups, release receipts and part selection persist through saving and reopening
the file. Rebuilding restores the saved style onto the new controls; it does not
restore old solver frames or freeze the old Pole and foot-pivot positions.
Missing or edited display backups receive a refusal rather than an unverified
replacement. Shared display resources used by other body parts remain protected.

The status reports ready, released, not built, different side states or a review
error. A missing part cannot be rebuilt or released by this workflow; initial
generation remains in the existing setup tools. When the two sides have different
states, select a single side. Complete a released part before using whole-body
Update/Remove or the legacy all-limb maintenance commands.

The foot-only choices rebuild heel, ball and toe helpers at the current foot
pose while preserving the native bones, skin weights and supported custom shapes.
They keep the existing leg IK and **do not rebuild or repair the Pole**. Switch
the selected feet to IK first and display the intended foot pose. Foot-only
choices offer direct rebuilding, not the leg/arm Release to Edit operation.

The current regional solver supports Direct schema 5. Arms include their wrist
control and native hand descendants; legs include feet and native toe descendants.
Head/neck, torso/spine and fingers do not have an independent regional teardown
in this workflow. Their existing tools remain separate. Including native hand
descendants in an arm's protected scope does not provide a finger-control
delete-and-rebuild function. Native names, bone inventory, parents and
connection/deformation flags must remain intact. Hips and unrelated auxiliary
branches are protected. An edited knee/elbow needs a stable small Rest bend.
Mesh weights are retained, not rebound automatically.

Selected authored animation or external dependencies are reported before
deletion. Independent animation on other limbs is retained. An edited arm with
a correction calibrated to its old Rest must have that old correction removed;
rebuild the arm, then capture its correction again. Old animation recovery
records are preserved rather than silently retargeted to new proportions.

## Character placement

Open **Rig > Body**. **Show Body Controls** restores existing
control visibility without toggling it off on a second click. It preserves
custom shapes and does not rebuild the skeleton. Finish Original editing by
choosing Controls before using this display action.

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
