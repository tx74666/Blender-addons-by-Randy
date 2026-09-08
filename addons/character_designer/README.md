# Character Designer 0.43.2

Character Designer is Randy's personal Blender add-on. It stays separate from
RR Helper and focuses on character-modeling tools.

Workflow principle: generated bindings and setups should remain editable and
provide an explicit remove/restore path. Preserve the artist's original state
and unrelated data; support saving/reopening where restoration depends on a backup.

Version 0.43.2 simplifies binding around the selected mesh. **Character Setup**
shows Main Rig and, on the Weight page, Body Weight Source. There is no clothing
inventory or role registration step. Select a mesh, choose **Surface Transfer**
(Nearest Face Interpolated, the default) or **Automatic Weights**, then click
**Bind Weights**. Hair and Skirt keep their source and custom binding controls in
their dedicated pages. Previously saved references and restore records remain valid.

Version 0.43.1 added **Character Setup** and **Weight > Quick Bind** with a saved
**Restore Previous Binding** action. The first binding keeps the prior deform
weights and binding state on the mesh object, including across saving/reopening.
Repeated Quick Bind retains that first baseline. Restore returns an originally
unbound mesh to an unbound state while retaining its modeling/helper groups.
Vertex positions can still be edited; topology/index changes require restoring
before remeshing because old per-vertex weights cannot be mapped safely by index.

Version 0.43.0 introduced **Character Setup** and **Weight > Quick Bind**.
Set **Main Rig** and the already weighted **Body Weight Source** once. References
and previously remembered meshes are saved in the blend file and survive object
renaming. Hair and Skirt use the saved main rig when their explicit override is
empty and remember their sources through their dedicated setup tools.

In Object Mode, select the mesh to bind and choose **Surface Transfer**
to transfer body weights, or **Automatic Weights** for Blender's bone heat solver.
Both operate on the active mesh, replace only the main rig's deform groups,
normalize weights, retain modeling/helper groups, and add or reuse its Armature
modifier. Geometry, UVs, shape keys, parent transforms and existing modifiers remain
intact. New Armature modifiers go after Mirror and before Subdivision. Calculation
uses Basis geometry and enabled Mirrors, independent of the current pose and shape
key values; other geometric modifiers are not sampled. Enabled Mirrors must have
Vertex Groups enabled to flip left/right weights correctly.

Binding supports Undo and restores previous weights on calculation/write failure.
Locked deform groups, conflicting armatures, disabled or masked Armature modifiers,
shared target meshes and incomplete weight coverage are reported before committing.
Registered Hair and Skirt meshes use their dedicated binding tools. Automatic
Weights is a starting point and can fail on complex footwear; use nearest face
transfer when the body is a suitable source, then paint the desired ankle/toe blend.

Version 0.42.3 adds **Rig > Limb IK > Simplify Bone Collections** for the selected
armature. Run it once to replace the old subdivisions with **Original**, **Controls**,
and **Animation**. Animation is the default visible group: each built IK limb uses
its controls and visible Pole guides; unbuilt limbs, torso, fingers, eyes and toes
keep their original bones. Build, Rebuild and Remove refresh this choice automatically.
The internal helpers retain their rig-owned visibility and selection rules.
Hair uses one **Hair** group, and an attached independent skirt armature uses one
**Skirt** group with its internal bones hidden. Armatures are not merged.
Organization supports Undo and preserves bone transforms, animation, constraints,
weights and shape keys. The layout survives saving/reopening without a runtime handler.
New artist-created groups are preserved during later automatic refreshes; conflicting
group names require explicit organization again rather than silent replacement.

Version 0.42.2 enables **Auto Align** by default on newly built Limb IK rigs,
for both Stable and Direct methods. Saving/reopening and Rebuild preserve the
existing mode, including explicitly selected Manual mode. Existing rigs that
have Auto Align disabled can be enabled once and saved to retain that choice.

Version 0.42.1 consistently highlights removal actions in red: **Remove Skirt
Setup**, forearm calibration **Remove**, and legacy hair **Cleanup Generated
Copies**, alongside the existing Hair Binding and Limb/Spline IK removal buttons.
The red style identifies removal of generated work; existing removal checks and
confirmation behavior remain in place.

Version 0.42.0 adds **Animation** to the second row of page tabs. It connects
to a separately installed, free local Kimodo runtime. Model weights and Python
dependencies are not bundled in the add-on ZIP. No paid Blender plug-in,
subscription, hosted generation, or paid API is used.

1. Choose **Animation**, check **Character Rig** (the selected armature or
   CoshaRig is used when the field is empty), and describe a short motion in English.
2. Start with **2 seconds**, then **Generate Local Motion**. Generation runs
   outside Blender and can be cancelled. A new prompt first loads the local text
   encoder, saves its embedding, and exits before loading the motion model.
   Reusing the same prompt skips that encoder stage.
3. **Import Preview** creates a separate BVH armature, preserving scene FPS and
   playback range. **Apply as New Action** transfers body motion to the character.
   **Restore Previous Action** switches back; the generated Action remains available.
4. Under **Local Setup**, choose the configured Kimodo folder or reopen a previously
   generated BVH via **Motion File**. **Open Job Folder** contains the result and logs.

This first integration supports text-to-motion and conservative FK body transfer.
Anatomical direction calibration accounts for SOMA's T Pose and CoshaRig's A Pose
before applying motion, preserving the character's proportions and bone rolls.
Pose anchors, paths, and automatic foot-contact correction are not exposed yet.
Mapped bones with active constraints or drivers require a compatible retargeting
workflow; this tool does not disable them. Hair and skirt bones are not keyed by
the body transfer. Clothing simulation needs its normal playback/bake workflow.
The previous Action and original unanimated pose are recorded on the new Action
and can be restored after saving/reopening; Apply/Import also support Blender Undo.

The local setup uses NVIDIA's SOMA motion model and the community NF4 text encoder.
On 6GB GPUs, generating a new prompt can require closing other GPU-heavy apps.
The runtime checks available memory before loading; setup alone does not guarantee
that generation fits alongside all currently open projects. See
[local runtime notes](../../docs/kimodo-local.md).

Version 0.41.3 removes **Weight Flow**. The **Weight** page contains
**Weight Tools** and **Weight Symmetry**. Use Blender's native weight-smoothing
tools for local weight transitions.

Version 0.41.2 combines Modeling and Reference in **Miscellaneous**. The
modeling panel and its setup button are named **Build Symmetry**, with a
short centerline/center-band selection hint. Its pairing and movement behavior
are unchanged. The page grid is **Hair / Weight / Rig**, then
**Clothing / Miscellaneous**.

Version 0.41.1 highlights **Remove Hair Binding** in Blender's red alert style
so the removal action is easy to locate.

Version 0.41.0 binds the **original hair mesh** directly to the character's
Armature. Each captured strand gets an independent chain parented to Head.
No extra mesh, private hair rig, or attachment bone is created. The old
Generate New Version workflow is removed from the panel. Shared procedural
motion remains a future stage, not part of this release.

The target defaults to an existing source binding, or the nearest recognizable
humanoid Head when unbound. The panel shows the chosen rig and Head; an explicit
Armature override is available. Equally near candidates require an explicit
choice. Head and influencing bones must be at rest when binding; the tool
never changes the character pose or its animation to force a bind.

The complete strand capture is checked against the mesh before assigning the
remaining root-connected cap region to Head with weight 1. Root rings are also
Head 1; farther vertices blend through their own strand bones. Uncaptured long
strands and ambiguous residual regions stop the operation. Nondeforming artist
masks are preserved.

Mirror remains native and precedes Armature. Both side chains are created,
while a strand joined along the mirror seam uses one central chain. X Hair3's
seven half-mesh captures give 13 full chains, or 52 hair bones at four per chain,
directly under the existing Head. Initial bindings with an existing Armature
modifier support either original Mirror order, restoring it on removal.

Version 0.40.1 clarifies the skirt controls: use G / R / S on the three master
rings, and G on the local side points. Each side point Hooks one curve point at
its own origin, so rotating or scaling that point alone does not reshape the
wire. Existing rigs and animations need no rebuild for this wording correction.

Version 0.40.0 adds **Clothing > Skirt Setup**. Select an unbound skirt mesh in
Object Mode and press **Create Skirt Setup**. The tool recognizes regular quad
rings, fits a smooth tapered elliptical cage without changing the source mesh,
and creates a separate native rig attached to the character's Hips. The default
eight directions and four bones per direction can be changed before creating.
If the scene contains several possible rigs, choose the character and pelvis
under Attachment. With no character rig, the skirt can be controlled on its own.

The three large rings control **Waist, Mid, and Hem**. Move, rotate, or scale
them in Pose Mode; the small diamonds shape individual wire ribs. The visible
wire follows those native controls. Skin weights blend down the skirt and
between adjacent directions, while the top ring remains attached to the waist.
Repeated Create selects the existing controls; changing source geometry or bone
counts requires removing and recreating the owned setup.

**Remove Skirt Setup** reverses this tool's binding without relying on Undo:
it restores the recorded original parent and transforms, removes the generated
Armature modifier and weight groups, and retains the source mesh, Shape Keys,
original weight groups, and other original modifiers. The record survives saving
and reopening. It does not undo later mesh edits. Generated controls and their
animation are removed after confirmation; independent baked copies remain.
Custom external constraints or drivers linked to the generated rig need to be
handled before removal; this skirt cleanup does not yet scan every external
dependency. It is restoration of the recorded source binding, not a full scene snapshot.

**Physics** creates one connected low-resolution cloth cage, hidden physics
bones, and closed convex collision proxies for the pelvis and identifiable
left/right thighs. The Physics slider adds simulated sway to manual shaping.
Use **Colliders / Select Colliders** to inspect and adjust the approximation.
The proxies are an initial fit, and motion with large leg bends may need manual
collision fitting or control animation. This control cage does not model every
fold or guarantee collision-free final clothing. Its self-collision is disabled.

Use **Bake Physics** before seeking freely through an animation. It evaluates
every frame in the chosen scene range, then seals that cloth cache. After
changing controls or colliders, clear and rebake. **Bake Animation Copy** creates
a separate parentless deform rig and skirt mesh, sampling final bone transforms
and character motion into quaternion keyframes on every frame. Its LINEAR keys
avoid interpolation overshoot; the copy uses native skinning without simulation
or add-on handlers. **Show Baked Copy / Return to Controls** compares the two in
the viewport. The baked collection starts hidden for viewport and rendering so
it does not overlap the editable setup. Enable its render visibility and hide
the editable skirt when intentionally rendering the copy. Bake operations show
progress and can be canceled with Esc; incomplete animation copies are removed.

The first version accepts a complete, single, regular quad skirt surface with
two closed boundaries. Slits, disconnected layers, existing skin/Cloth setups,
and un-applied construction modifiers require an unbound prepared copy. Static
Shape Keys and artist groups remain intact; animated/driven Shape Keys are not
included in a bone-only animation copy. Generated controls and physics work
after saving and reopening using native Blender data. Bake result quality still
depends on collision fitting, the input animation, and the selected frame range.

The implementation learns from Randy's Elaina Ex1 and ProfessionalEx scenes;
their original scripts, objects, weights, and caches are not executed or edited.

**Hair > Hair Bones** creates an independent chain for each captured strand.
The default is four bones per chain, adjustable before binding.

1. Enter Mesh Edit Mode on the original hair and press **Select Hair Strands**.
   Deselect everything first to discover all visible strands, or select tips
   to limit discovery, then capture every strand before binding the whole mesh.
2. Set **Bones per Chain**, check the displayed character/Head, and press
   **Bind Hair to Character**. The original mesh remains the same object and
   uses the character's own Armature. Rotate the selected hair bones in Pose Mode.
3. **Remove Hair Binding** removes only recorded hair bones and their binding
   changes, restoring pre-bind weights, parenting, Mirror settings and Armature
   modifier order. A pre-existing binding is restored rather than erased. The
   original geometry and shape keys remain. The record survives save/reopen.
4. Remove before changing chain counts or topology, then use **Recapture
   Strands** and bind again. Recapture retains old capture/guide infrastructure
   required to read earlier files; it does not delete guide objects.

Old version-copy scenes remain readable, but no new copies are made by the UI.
**Cleanup Generated Copies** deletes only verified plugin-owned version meshes,
private rigs and Mirror helpers for the selected original source. It preserves
and reveals the original hair. In an interactive Blender session, cleanup first
writes a recovery file under `.character_designer_backups` beside the current
blend file. Foreign objects in version collections, shared data and outside
dependencies stop cleanup before deletion.

Removal also stops if topology/ownership changed, or if other meshes, objects,
constraints or drivers depend on the hair bones. Transfer or remove hair-bone
animation/custom constraints before removing the binding; the character's
unrelated animation is preserved. The generated native deformation works
without the add-on; the add-on is needed for its reversible removal action.

Version 0.38.0 originally added **Hair > Hair Bones**, next to the existing hair centerline
workflow. It finds long, regular strips in a joined hair mesh and builds native
connected FK bone chains with local, smoothly varying skin weights.
The following 0.38 notes are historical; use the reversible original-mesh
workflow above for current binding and removal.

1. Select the hair mesh and enter **Edit Mode**. Deselect everything to search
   the visible mesh, or select a hair tip/vertices to limit the search.
2. Press **Select Hair Strands** and inspect the highlighted geometry.
3. Set **Bones per Strand** (default 4) and press **Generate Hair Bones**.
   Blender enters Pose Mode with the new hair bones selected. Rotate a bone
   to bend its strand; the chains appear in the **Hair** collection.

Discovery follows complete surface bands and stops at irregular welded root
junctions. Closed tube sections and open hair cards are supported; the number
of vertices in a section is not hard-coded. A collapsed tip sets the direction;
otherwise endpoint height in world Z, then relative width, helps identify the
root. This is a geometric aid, not semantic recognition of every hairstyle.
Ambiguous or irregular regions remain outside the generated chains. You can
also select complete regular strips or longitudinal guide edges manually,
using the existing mesh-to-centerline selection rules, and generate directly.

When possible, chains attach to the existing rig's Head bone (including X's
`CoshaRig / spine.006`, identified by its paired eye bones). Otherwise a small
Hair Rig with an anchor is created. Newly bound, previously unweighted hair
outside the chosen strands follows that head/anchor. Existing outside weights,
artist masks, Shape Keys, mesh coordinates, and other bone poses are preserved.
Shared welded roots stay attached to the head, while each strand bends
independently. Binding requires the attachment and replaced skin influences
at Rest; unrelated posed limbs do not need resetting.

The Armature modifier is placed before the existing surface modifiers. For an
existing Mirror with its default object-local plane, one managed reference
object keeps that plane attached to the head; both halves share the authored
side's controls and follow head and whole-rig movement correctly. An existing
custom Mirror reference is reported as unsupported. First binding parents the
hair object to its Armature while preserving its world placement. The reference
object is shared by that mesh's Mirror modifiers, not added per strand.

Generating the same selection again selects its existing controls, without
duplicating bones. New strands can be added later. Changes to an existing
chain's captured topology, Rest bones, or requested bone count are reported
instead of overwriting its animation; use Blender Undo to redo a new setup.
The generated deformation uses native Blender data and continues after saving,
reopening, or disabling Character Designer. No per-frame hair handler is used.

Version 0.37.0 makes **Rig > Forearm Twist (Prototype)** symmetric by default.
There is one calibration for both arms, with no Left/Right selector, Mirror
checkbox or Sync button. Select the body Mesh or a Hand Target and use
**Start 90° Test** / **Recalibrate 90°**. Choosing a Hand Target in Pose Mode
automatically identifies its body and test arm; Confirm or Cancel restores
the selection and Pose Mode. Enable and Remove act on both arms together.

Loop shares remain attached to their mesh rings through FK, Build IK,
Rebuild Rig and Remove Generated. The runtime automatically recaptures the
current forearm frames after these operations, preserving the exact saved
shares and each side's own influence region. This also runs after loading,
Undo and add-on refresh. Legacy one-arm files migrate to both arms; an old
pair without a recorded source uses its left profile. Both hands keep their
own control poses. Pairing verifies mirrored Basis geometry, loop connectivity
and Rest endpoints; changed topology or different captured loops require a
new calibration. No operation modifies the rig to force a correspondence.

The existing `CTRL_hand_IK.L` / `.R` **Twist (Y)** remains the hand orientation
input. Within the calibrated region, the correction replaces the original
weighted axial turn with the saved loop shares; it does not add a second turn.
The Target Rotation panel now reports whether Forearm Twist is active or has
fallen back to original skinning. Rotation tests preserve the starting wrist
bend and side swing, and can jump directly between positive and negative
90° / 120° without inheriting the previous preview's Euler singularity.

Press **Start 90° Test** to preview the calibrated turn. The
existing Hand Target previews a pure forearm-axis turn while the highlighted
closed loop and **Twist Share** slider let you calibrate its distribution.
Change **Loop** to adjust another ring; **Test Angle** previews either direction.
**Confirm** saves the ratios and restores the starting target rotation;
**Cancel** or Esc restores both the pose and previous calibration. The elbow
and wrist anchors stay at 0% and 100%. X's current topology provides seven
closed rings per side. The 100% endpoint continues through existing forearm
weights past the wrist so the correction does not stop in the middle of a blend.
If the arm has no generated IK yet, the test uses its existing unconstrained
hand bone directly. Its original rotation mode and pose are restored afterward;
building new control bones is not required. A third-party constrained hand is
refused instead of modifying that rig's constraints.

The feature adds one managed relative Shape Key, `CD Forearm Twist.L` or `.R`,
per calibrated arm (and Basis only when necessary). It adds no bones or
modifiers and does not repaint weights. Existing relative keys, including
animated facial and forearm keys, remain independent. Runtime reads evaluated
bone poses, removes the hand's existing axial twist from the affected skinning
contribution, distributes the requested circular rotation, and inverse-solves
the actual linear skinning to produce the pre-Armature correction. Pure twist
preserves the distance to the axis where the forearm/hand own all influence;
this is not a general volume solver for arbitrary wrist bends or other bones.

The prototype supports **-120° to +120°**, ordinary Vertex Groups skinning,
and exactly one **Armature first in the modifier stack**. Subdivision after
Armature is supported. Preserve Volume, envelopes, modifier masks, preceding
Mirror/deformation modifiers, and affected Bendy Bones are refused explicitly.
Calibrate before animating the Hand Target. Playback and rendering then follow
its animation with the add-on enabled; a saved key alone cannot reproduce the
dynamic result in another application or with the add-on disabled. While a
correction is enabled, Render > Lock Interface is managed to prevent concurrent
viewport/render writes; disabling/removing the last correction or unloading the
add-on restores the prior setting. Invalid topology, Rest changes, or unsupported
angles mute the correction with an explanation in the panel, and supported
poses resume automatically. Intact loops retain their shares through Rest
changes automatically. Remove and recapture after topology changes.

Validation includes disposable Blender 5.2 fixtures for both IK methods,
Auto Align, independent sides, relative-key animation, out-of-order frame
evaluation, render/static image equality, confirmation/cancellation, load and
undo recovery, unsupported inputs, and real-X geometry/visual comparisons.
The saved profile lives in the blend file; keep Character Designer enabled
when reopening it. The ordinary file save includes both arms' calibration.

Version 0.34.2 fixes the **Auto Align** display-frame handoff. Enabling Auto now
keeps the controller's complete relation to its wrist/ankle and applies the
same evaluated transform delta to both, rather than freezing the controller in
its previous world orientation while the Hand/Foot turns. This specifically
keeps nonzero fitted Foot-outline rotation and translation attached to the
shoe. Target Rotation channels remain unchanged, and the IK solver point does
not move. Mode-local visual defaults are retargeted with the visible frame so
Reset Visual, artist offsets, Auto/Manual round-trips, and Rebuild remain
consistent. Refresh the add-on and use **Rebuild Rig** once to upgrade an
already-generated rig.

Version 0.34.1 makes the Pole connector conditional on its control shape.
Choosing **Sphere Wire** hides the separate joint-to-Pole connector; choosing
**Arrow** or **Rig Default** restores it. Direct Pre-Roll filters its GPU guide,
while Stable hides only the constrained VIS display bone, leaving the helper,
constraints, IK result, and generated bone count unchanged. The choice also
survives **Rebuild Rig**.

Version 0.34.0 makes every generated Hand and Foot IK Target a complete aligned
rotation control without creating another bone. Generated Targets use Local
XYZ: Hand X = Flex/Extend, Y = Twist, Z = Side/Wave; Foot X = Toe Up/Down,
Y = Bank, Z = Turn. The active Hand/Foot Target gets a contextual
**Target Rotation** panel with those semantic channel names, keyframe
decorators, and **Reset Rotation**. Reset clears only the three local rotation
channels and preserves location, scale, and all display-only Custom Shape
adjustments.

Manual mode keeps its absolute end-rotation relationship. **Auto Align** now
keeps the solved Forearm/Shin frame live while applying the same Target XYZ
rotation as a local additive offset afterward; changing Auto/Manual remains
no-pop and uses no polling loop. No hard foot-angle clamp is authored, so all
three ankle channels remain available. One versioned owned constraint per limb
implements the Auto offset; it adds no visible or hidden bone. Existing rigs
migrate on **Rebuild Rig**, Remove cleans the new relation and marker, and
transactional failure restores the exact pre-Rebuild rig.

Version 0.33.0 unifies each Direct Pole's two drawing paths into one selection
language. The dynamic GPU shaft now reads the same Pole bone and Blender theme
state as its custom-shape arrowhead: both are black when unselected and both
highlight blue when selected. They remain technically separate render paths so
the shaft can change length and the head can re-aim continuously, but they act
as one control and create no extra rig data.

The **Control Visual** panel now includes a compact **Shape** menu for the
active generated animator control. **Rig Default**, **Arrow**, and
**Sphere Wire** are managed, reusable display widgets; the selected style and
existing Offset/Rotation/Scale survive Rebuild and are cleaned by Remove.
Changing the shape does not touch the control pose, Auto Align, constraints,
Rest bones, source/deform bones, weights, Shape Keys, or meshes.

Version 0.32.0 advances Direct Pre-Roll to schema 5. Each limb's visible,
selectable animator controls remain only its Target and Pole, but the generated
inventory now includes one hidden, non-selectable, non-Deform Pole display-aim
helper. In Pose Mode the GPU overlay draws the straight line from the real bend
joint to the Pole whenever the relevant viewport, Overlay, Bones, bone, and
Bone Collection visibility allows it; Pole selection is not required. The
existing `WGT_Randy_PoleArrow` custom shape remains the sole pyramid/cone head.
Its display-aim helper tracks the live joint-to-Pole axis, so moving the Pole
rotates that same head dynamically and keeps its base plane perpendicular to
the shaft. No duplicate arrowhead or persistent scene object is created.

**Auto Align** is one rig-wide toggle independent of the limb dropdown. A
single switch applies atomically to every generated Arm and Leg. While enabled,
each Hand and Foot Target's visible custom-shape frame follows its own evaluated
end. The shoe-sole outline therefore follows the solved Foot/Shin pitch and roll
continuously instead of remaining visually flat while the leg changes
direction. Switching back to Manual first matches all Targets to their live end
orientations and then restores each Target's own custom-shape frame without a
visible pop. This is a display/rotation-control contract only; it does not edit
Rest bones, weights, or meshes.

Version 0.31.1 kept each Direct Pre-Roll Pole as one visual arrow without a
display helper. The existing `WGT_Randy_PoleArrow` was the sole arrowhead, and
the selected-only GPU overlay contributed only the line from the real bend
joint to that displayed tip. No persistent scene object was created.

Version 0.31.0 turns **Auto Align Target** into a persistent Hand/Foot mode.
While it is enabled, the generated end-rotation constraint stays muted so the
end uses the limb's natural solved orientation instead of Target rotation.
Disabling the mode first matches the Target to that live orientation, then
restores ordinary manual Target rotation without a visible pop. The choice is
stored on the generated Target and survives Rebuild. Transitions follow the
constraint's actual coordinate spaces and roll back on failure; any transition
that would need to overwrite locked, keyed, NLA-driven, or driver-controlled
Target channels is refused instead of overwriting animation.

Version 0.31.1 showed Direct Pre-Roll elbow and knee guides only while their
Pole control was selected in Pose Mode. Version 0.32.0 supersedes that
selection rule with the always-present Pose Mode shaft described above. Both
versions respect Viewport, Overlay, Bones, bone, and Bone Collection visibility;
Stable rigs keep their existing constrained Pole guide.

The new **Control Visual** child panel exposes display-only Offset, Rotation,
and Scale for the active generated Master, Hand IK, Foot IK, Pole, or Heel Roll
control. **Reset** restores that control's generated display defaults, and a
successful Rebuild preserves deliberate visual adjustments relative to the new
fit. These controls change only Pose-bone Custom Shape presentation: control
transforms, IK behavior, Rest bones, constraints, weights, and meshes remain
untouched. Source finger and shoulder displays and internal helper bones are
not editable through this panel.

Version 0.30.0 finished the first joint-oriented presentation pass without
adding controls to the deform skeleton. Finger rings now sit at each detected
source finger bone's Head, which is the bend joint, and their wire plane remains
normal to the bone-local Y direction. Existing version-1 midpoint displays
migrate only when their saved generated state still matches the live bone; an
artist-edited display fails closed, and failed Rebuild restores the old registry
and display exactly.

Direct Foot controls are now independent `FOOT_L` and `FOOT_R` 64-point closed
outlines. Each side fits its own visible evaluated shoe sole, yaw, center,
width, and length with a small perimeter margin; raw +Y stays toe-forward and
raw +X maps to the medial big-toe edge. The outline stays horizontal, just below
the sole, and away from the body centerline in its authored frame. In 0.32.0 its
displayed frame follows the evaluated Foot while Auto Align is enabled, and
uses the Target frame in Manual. Old rigs that share the 46-point `FOOT` widget
remain removable; successful Rebuild upgrades them, while failed Rebuild
restores the old shared object, mesh, assignments, and display state.

Version 0.29.9 corrects the Rain Foot outline's signed left/right mapping. Its
raw +Y axis remains the toe direction, while raw +X now maps to the medial,
big-toe side on both feet; the previous mapping put that edge on the outside.
The visible-geometry fit still derives its yaw and footprint from the evaluated
shoe geometry. If no usable visible mesh is available, the fallback display is
now flattened into a horizontal Foot-forward frame instead of inheriting the
Foot bone's pitch and roll. This is a display-only correction: IK motion, Rest
bones, weights, and the character meshes are not changed.

Version 0.29.8 connects the two remaining Rain-inspired selection shapes to
the existing character bones. Each detected three-segment Thumb/Index/Middle/
Ring/Pinky chain now receives the 32-edge circular control with two small side
bumps, and each high-confidence Shoulder/Clavicle parent receives the 44-edge
arched control. The shapes are shared display meshes assigned directly to the
source Pose bones: no finger, shoulder, MCH, or helper bones are added, and
bone names, parentage, Rest matrices, constraints, animation paths, Deform
flags, Vertex Groups, and weights remain untouched.

Only fingers descended from an analyzed Hand and the immediate named parent of
an analyzed Upper Arm are eligible. Existing artist or third-party Custom
Shapes are skipped instead of overwritten. A versioned source-display registry
records the exact prior scale, translation, rotation, bone-size, and wire-width
state; **Remove Generated Rig** restores it before deleting shared widgets.
Build failure and Rebuild recovery cover the same state, while older generated
rigs without this registry remain removable and migrate on a successful
Rebuild.

Version 0.29.7 makes the generated wrist and foot controls follow the character
instead of relying on fixed bone-length guesses. A Direct Pre-Roll Hand Target
now uses the final Forearm Rest frame and transports animator rotation through
`Local Owner Orientation -> Local With Parent`. The modeled wrist offset stays
unchanged at Build, while the hand-shaped widget points along the Forearm on
both sides. Stable rigs keep their existing pose contract and receive the same
straight Forearm-aligned widget presentation without adding another bone.

In version 0.29.7, Foot Targets began fitting their flat shoe outline from
evaluated, currently visible geometry. Deform weights establish the trusted
left/right foot seed; nearby separate footwear is included, while hidden backup
meshes and unrelated distant objects are ignored. The outline receives a small
perimeter margin, is placed wholly below the lowest captured sole, and is
clamped away from the character centerline. At that release Direct Pre-Roll
still generated exactly two bones per limb: Target + Pole.

An untouched 0.29.6 Direct arm can be migrated with **Rebuild Rig**. If its old
world-space Hand Target has already been posed, Rebuild refuses before changing
anything; return that Target to Rest first so its animation is not silently
reinterpreted.

Version 0.29.6 turns **Direct Pre-Roll (Minimal)** into a real selectable Limb IK
Build Method. It re-planes the source Rest joint and pre-rolls the upper/lower
pair before placing direct IK on the deform chain. Each limb adds exactly two
control bones: its Target and Pole. It deliberately omits Master, MCH/ORI,
Pole-line/display helpers, and Heel Roll. **Remove Generated Rig** restores the
recorded original Rest chain exactly, while **Rebuild Rig** remains in Direct
schema and reuses the reversible Rest record.

Direct Pre-Roll is experimental and intended for a clean Rest/start pose with a
mostly fixed Pole range. It refuses posed, constrained, animated, effectively
straight, or unsafe shared-connected chains. It protects the calibrated start
frame, but does not promise twist-free motion across the complete animation
range. **Stable (MCH/ORI)** therefore remains the default production method.

Version 0.29.5 introduced the read-only **Direct Pre-Roll Lab** calculation now
used by the Direct builder. **Check Selected Limb** still previews its Rest-plane
offset, required joint shift, and coherent upper/lower rolls without editing
bones.

Version 0.29.4 decouples a limb's bend plane from its deform cross-section.
Each generated limb now has a hidden two-bone MCH chain for IK plus two
independent ORI helpers sharing the original upper bone's upstream anchor. The
MCH chain follows the explicit Pole direction; each ORI helper uses a World
Copy Location and `Damped Track +Y` to apply only the minimum swing needed to
follow its corresponding segment. The deform upper/lower bones copy those ORI
rotations, while Hand/Foot rotation remains driven by its existing target.

This prevents the former failure where correcting an elbow to bend behind the
character also turned the forearm cross-section by roughly 180 degrees. In
topology terms, the inset elbow pit remains on the compression side and the
rounded elbow tip remains on the stretch side. Arms still default to
Armature-local `+Y` behind and legs to `-Y` in front.

**Rebuild Rig** upgrades schema-1/2 rigs transactionally. It first removes the
old direct-IK graph, then reads the clean underlying source frames so the old
Pole-induced roll cannot be baked into ORI. Same-schema rebuilds preserve the
live MCH inputs and rebaseline the ORI helpers at the current pose; repeated
rebuilds remain stable, while a changed or flipped Pole direction is calibrated
again. Strict ownership, Remove, and rollback include every added MCH/ORI bone
and constraint.

Version 0.29.3 fixes repeated **Rebuild Rig** of the short Edit Mode Pole-line
helpers. Both helper endpoints are now authored in Armature-local Rest space:
the real elbow/knee joint points toward the newly-created Pole. A second or
later rebuild can no longer reverse only the helper through an already-solved
upper-bone pose. The helper remains short, unconnected, and Keep Offset; its
Pose Mode Stretch To guide still reaches the Pole.

Version 0.29.2 restores one unambiguous anatomical default for Character
Designer's Armature-local `-Y`-forward humanoid convention: Arm Poles use `+Y`
behind the character and Leg Poles use `-Y` in front. Version 0.29.1 had treated
the normalized modeled rest-joint residual as Default Direction. On an almost
straight chain that amplified tiny noise: X's elbows went forward and its knee
defaults became about 91% lateral. The residual is no longer substituted for
an artist-facing default. Explicit XYZ input and an existing generated Pole's
saved direction remain authoritative, so updating never silently migrates a
rig. To adopt the corrected defaults, Analyze, choose each generated limb,
press **Default Direction**, and run **Rebuild Rig** once.

The actual Pole, solved bend, and dynamic arrow stay aligned. `CTRL_*_pole`
remains a short, unconnected Keep Offset bone at the Pole target and its Edit
tail now follows the real bend direction. The non-selectable `VIS_*_pole_line`
helper also has a short Edit-rest body instead of spanning the whole gap; its
existing Stretch To constraint still draws the complete joint-to-Pole guide in
Pose Mode. Rebuild rollback records and restores the prior helper endpoints.

Version 0.29.0 expands **Copy Weight to Opposite** with same-side multi-bone
selection in Pose Mode and Weight Paint. Every selected Deform-bone pair is
planned from one immutable Vertex Group snapshot, then the complete batch is
preflighted before the first write. All pairs commit as one Undo step, or any
invalid pair leaves every Vertex Group unchanged. The original active-group
single-copy workflow remains available when there is no intentional multi-bone
selection. Source names must end in exactly `.L` or `.R`; the tool never strips
or guesses through suffixes such as `.001`.

The directed copy treats each source as explicit rather than relying on
Blender's selection-dependent Mirror command. Character Designer resolves each
paired Deform bone, derives the source half from that bone in Mesh-local space,
keeps the source half exact, replaces the paired group on the opposite half,
and clears wrong-side memberships from both group names. The operation requires
unique spatial X pairs for every weighted source vertex, unlocked groups,
single-user editable Mesh data, and unchanged per-vertex Deform totals. It
fails before writing when the result would need an implicit Normalize or when
the rig, names, transform, or pairing is ambiguous. Other Vertex Groups remain
point-for-point unchanged and context is restored.

## Weight Symmetry

In Pose Mode or Weight Paint, select two or more same-side Deform Pose bones,
then open **Character Designer > Weight > Weight Symmetry**. The button reports
the selected count and copies all pairs together. In Weight Paint, leaving no
intentional multi-bone Pose selection preserves the original workflow: make a
side-named group such as `forearm.L` active and use the exact one-way action
**Copy forearm.L -> forearm.R**. Each pair uses only its source group's weighted
source-side support, so unrelated asymmetric parts of the same character mesh
do not block the operation.

This is an exact reassignment/copy tool, not a global Normalize command. It
overwrites each opposite group's side, clears source/target wrong-side
memberships, preserves center-line memberships and every other group, and
cancels the entire batch before writing if any pair is invalid or a per-vertex
Deform total would change. A completed single- or multi-pair operation is one
Blender Undo step.

Version 0.27.0 refines **Randy Rig: Limb IK** around the compact single-limb
selector and a first complete animator-facing control set. One identity-rest
`CTRL_master` moves the character through exact source-root constraints without
rewriting the artist hierarchy; its head and horizontal widget rest at
Armature-local ground zero. Arms use concave hand targets and dynamic
elbow Pole arrows; legs use sole-level foot targets, dynamic knee Pole arrows,
and `CTRL_heel_roll.*` controls driving hidden solver targets. Pole guide lines
stretch and aim live while only the actual Pole remains selectable. **Build IK**
creates only the limb shown in the dropdown; **Build All** completes every
populated Arm/Leg chain through one atomic build transaction and Undo step.
Partly filled chains fail closed before anything is generated. The former Build
Arms/Build Legs operators remain registered for script/keymap compatibility but
are no longer drawn. Finger-ring-with-bump and shoulder-arch shapes are applied
directly to eligible source bones and create no bones.

Every dropdown limb has an explicit Armature-local **Pole Direction XYZ** and a
**Default Direction** button. Arms default to `+Y` behind the character and
legs default to `-Y` in front under the add-on's `-Y`-forward convention.
Magnitude is normalized, so **Pole Distance** remains the only distance control. Zero,
non-finite, or chain-parallel
directions fail before generated data is changed. Blender's evaluated IK solver
aligns the real elbow/knee residual to the visible Pole instead of compensating
back to a shallow modeled bend. A mathematically straight two-bone chain has no
stable bend plane, so Build fails closed with an instruction to add a small
elbow/knee bend first; measurable near-straight chains remain supported. New
Pole bones remember the applied direction;
0.26.1 rigs without that optional tag remain removable and rebuildable.

The generated graph is schema-versioned. Rebuild upgrades owned 0.25 legacy
limb controls in one transaction, while Remove continues to understand the old
inventory. Incremental Build and Rebuild neutralize and restore a moved Master
so no transform is baked twice. Master translation, rotation, and uniform scale
are supported; non-uniform scale is refused during Build/Rebuild because it
would introduce ambiguous IK shear. The heel control is a deliberately compact
first-pass heel/bank pivot, not Rain's complete reverse-pivot ball/toe system.
The Rain-inspired visual language is documented in
[ATTRIBUTION.md](ATTRIBUTION.md).

Generated bones, constraints, collections, widgets, and registries carry exact
versioned ownership. Repeated Build does not create suffix duplicates; foreign
same-name bones, pre-existing constraints, shared or linked Armature data, and
corrupt ownership fail closed. Remove/Rebuild validate the complete graph and
refuse artist animation, Drivers, foreign constraint references, foreign child
bones, or foreign collection members. Stable leaves source Rest matrices
untouched. Direct changes only the selected upper/lower Rest frames while its
owned rig exists, records both original and applied states, and restores the
original state on Remove. Deform-bone names, Deform flags, Meshes, modifiers,
Vertex Groups, and weights are never rewritten by Limb IK. This release still
omits IK/FK switching, Spine IK, generated finger-control systems, and
shoulder/corrective deformation helpers; its finger and shoulder additions are
selection shapes on the existing bones only.

Version 0.24.0 replaces proportional affected-weight scaling with **Full Auto
Blend (Normalized)**. When enabled, all editable Deform bones on the current rig
compete in one full Automatic Weights solve. Character Designer then applies
only the selected bones' old/new influence region: after locked weights reserve
their exact share, selected Bone Heat values get first claim on the remaining
budget, full-solver neighboring bones fill the remainder, and every affected
vertex totals one. Where locked weights leave the full budget available, a
target core therefore stays at weight 1 instead of turning an old `1 + 1`
overlap into `0.5 + 0.5`.
Pre-existing target leakage outside the full-rig solution is removed and repaired
from the full solver's local support. On a true half Mesh, generated-side pair
groups are kept empty so the Mirror modifier owns the opposite side. Non-Deform
groups, locked weights, and all vertices outside the repaired region remain
exact, except that a supported half Mesh has all invalid generated-side base
weights cleared. A non-empty locked generated-side group is refused and the
complete Vertex Group snapshot is restored. Any failed weighting transaction
likewise restores that snapshot. The session-only UI checkbox defaults on
but never dirties or persists into the `.blend`. The direct operator property
defaults to false, preserving omitted or explicit-false calls; scripts that
previously passed the 0.23 normalization flag as true now opt into the new Full
Auto Blend semantics.

Version 0.23.0 introduced the earlier **Normalize Affected Deform Weights**
option. Its equal proportional scaling is superseded in 0.24.0 because it could
dilute a full selected core and could not repair the spatial leakage of a
single-bone Bone Heat solve.

Version 0.22.0 introduced the compact two-row page grid used by RR Helper.
The current layout is **Hair / Weight / Rig**, then **Clothing / Miscellaneous**.
The active page is visibly pressed. Weight Tools and Weight Symmetry share Weight;
Spline IK is under Rig, and Skirt Setup is under Clothing. **Build Symmetry**
and **Reference Views** appear together under **Miscellaneous**. The page
choice is session-only (`SKIP_SAVE`), so changing pages does not dirty or
persist into `X.blend`.

Version 0.21.0 introduced **Weight Flow**, a two-bone weight-transition tool.
It was removed in version 0.41.3.

Version 0.20.3 makes selected-bone weighting fail closed before it can produce
misleading results on an unsupported half-Mesh stack. A true half Mesh must use
one active object-local X Mirror with **Vertex Groups** enabled, and that Mirror
must be before the Armature modifier. Multiple X Mirrors, a custom Mirror
Object, extra Mirror axes, or an Armature-before-Mirror stack are refused with
a normal warning. Shared Mesh data is also refused so an unselected linked
duplicate cannot receive the same underlying weight edits; make the Mesh data
Single User first. Version 0.20.2 makes selected-bone weighting complete the Vertex Group naming
contract for a true half Mesh with X Mirror and **Vertex Groups** enabled. When
a selected source-side `.L`/`.R` bone has a real Deform counterpart but that
counterpart group is missing, the tool creates only an empty paired group. The
base Mesh receives no opposite-side weights; Blender's Mirror modifier then
assigns the evaluated mirrored vertices to the paired bone correctly. Existing
unselected groups and every weight remain byte-for-byte protected, and failure
removes any newly created empty pair during rollback. Version 0.20.1 makes the
Weight Tools UI English-only. Version 0.20.0 adds a
separate **Weight Tools** section with **Auto Weight Selected Bones**. It wraps
Blender's selected-bone Bone Heat solver in
an all-or-nothing transaction: only visible selected Deform bones may receive
new weights, while every other Vertex Group is checked point by point and must
remain exactly unchanged. It works with an existing Armature modifier and does
not re-parent the Mesh or replace modifiers. Mesh X Mirror and paint masks are
temporarily isolated so they cannot redirect the write into an unselected
counterpart group or limit the calculation to a masked subset, then their exact
states are restored. Parenting, modifier identity and order, every bone's
Deform flag, active Vertex Group, selection, and Pose/Weight Paint mode are
preserved. Any failure rolls the complete Vertex Group state back, and the
finished operation supports Undo.

Version 0.19.0 makes **Recover Applied Curve** modifier-aware without adding
another mode control. A source stack made only of **Mirror** and **Subdivision
Surface** is copied to each recovered Curve in its exact original order,
including Mirror axes/object/Bisect settings and Catmull-Clark or Simple
Subdivision levels. This retains a non-destructive stack instead of baking it
twice, and works for both Half and Full source profiles. Preview reports the
stack that will be preserved. Any other modifier is refused before a Curve is
written. Version 0.18.0 expands **Recover Applied Curve** with explicit Aligned Bezier
handles, a GPU-only live preview, and **Add** or **Replace Mesh** output. Add
keeps the source Mesh. Replace removes only the source Object, and only after a
complete source passes preflight and every recovered strand succeeds. Version
0.17.2 matches RR Helper's unobtrusive **Refresh Add-on** workflow. The button
appears only when installed Python files changed (or refresh needs attention),
reloads the add-on without saving or reloading the current `.blend`, and
disappears after the refreshed code becomes current. Version 0.15.3 makes the
compact placement controls operate directly on the
active generated Centerline in either Object Mode or Curve Edit Mode. Centered
and Surface move the Curve immediately; Blend's 0–1 Mix updates continuously
and persists on that Curve. Curve Edit Mode uses an in-place point update, so
the active object, edited data, control-point selection, and mode are preserved.
No new button is added. Version 0.15.2 originally introduced a save-first guard
for add-on refresh; the newer 0.17.2 workflow supersedes that guard by reloading
only Python modules, matching RR Helper and leaving the current `.blend` open
and unsaved in memory. Version 0.15.1 turns Hair Centerline **Blend** into a continuous 0–1 Mix:
0 is the exact Centered endpoint, 1 is the exact Surface endpoint, and every
intermediate value is available. The compact mode order is **Centered**,
**Surface**, **Blend**; the Mix slider appears only for Blend and is stored on
each generated Curve. Every value reuses the same outward ribbon, normal, and
Tilt logic, so placement never reverses the hair profile.

## Randy Rig: Limb IK

1. Make the intended source Armature active in Object or Pose Mode, open the
   **Rig** page, and click **Analyze Rig**. Choose Left/Right Arm or Leg in the
   dropdown, then review or correct that limb's Upper, Lower, and End fields.
   Set **Pole Direction (Local)** with XYZ, or use **Default Direction**. For
   Character Designer's Armature-local `-Y`-forward convention, the fixed
   defaults are elbow-behind (`+Y`) and knee-forward (`-Y`). Choose **Stable
   (MCH/ORI)** or **Direct Pre-Roll (Minimal)** in **Build Method**.
2. Click **Build IK** to create only the limb selected in the dropdown, or
   **Build All** to complete every populated Arm and Leg together in one atomic
   Undo step. Direct schema 5 exposes only Target + Pole per limb and adds one
   hidden, non-selectable, non-Deform Pole display-aim helper; Stable adds its
   hidden roll-preserving graph and animator helpers. A partly filled mapping
   cancels Build All before any generated data is written.
3. In Stable mode, move the whole character with `CTRL_master`. Pose limbs with
   `CTRL_hand_IK.*`, `CTRL_foot_IK.*`, `CTRL_elbow_pole.*`, and
   `CTRL_knee_pole.*`; rotate `CTRL_heel_roll.*` for the first-pass heel/bank
   pivot. Direct mode exposes only the Target and Pole controls and has no Heel
   Roll or whole-rig Master. Its joint-to-Pole shaft remains visible in Pose
   Mode, and moving the Pole dynamically re-aims the single existing arrowhead.
   **Auto Align** is separate from this limb dropdown: one toggle applies to all
   generated Arms and Legs. In Auto, every Hand and Foot custom-shape frame
   follows its own evaluated end, so each shoe-sole display follows its live
   Foot/Shin frame. Manual restores every Target's own frame.
4. Change a direction and use **Rebuild Rig** to apply it to exactly the sides
   already generated, or to transactionally upgrade an owned 0.25 rig. The
   upper/lower pair deliberately re-planes to the chosen Pole while its End and
   unrelated source bones remain protected. Use **Remove Generated Rig** to
   restore the exact source rig; Direct also restores the exact recorded
   pre-Build Rest state. Stable removal remains available even if its Master was
   scaled non-uniformly, although Build/Rebuild require finite positive uniform
   scale.

Analyze is read-only. Build, Remove, and Rebuild are Undoable.
Generated controls never deform the Mesh, and exact ownership prevents the
cleanup tools from deleting artist-authored rig parts. If a generated control
is referenced by animation, a Driver, a foreign constraint, or a foreign child
bone, removal is refused.

When Direct is selected, the collapsed **Direct Pre-Roll Lab** remains a
read-only preview. Its absolute Roll U/L values describe the frame *after* the
reported Rest-joint re-plane; entering those Roll values alone does not repair
skin twist. A result of **Aligned** means only that the starting Rest plane is a
plausible Direct IK candidate, not that every Target/Pole pose will remain
twist-free.

Limb IK improves posing and joint aim; it does not by itself preserve shoulder
volume. Shoulder thinning at high arm elevation still calls for weight work and
later clavicle/scapular or corrective deformation helpers.

## Weight Tools

**Auto Weight Selected Bones** repairs the currently selected, visible Deform
bones. With the default Full Auto Blend it temporarily solves all eligible
current-rig Deform bones together; strict mode solves only the selection:

1. Select one already bound Mesh together with its Armature. In Pose Mode or
   Armature Edit Mode, select the exact bones to recalculate. The same button
   also works while the bound Mesh is already in Weight Paint Mode.
2. Open **Character Designer > Weight Tools** and click
   **Auto Weight Selected Bones**. Keep **Full Auto Blend (Normalized)** enabled
   for the healthy default: all current-rig Deform bones compete in Bone Heat,
   then only the selected bones' repaired local region is written back. The Mesh
   must have one unambiguous existing Armature modifier; the tool never creates,
   removes, or reorders modifiers and never changes parenting.
3. Existing target groups are replaced and a missing selected-bone group is
   created. A locked target group is refused until the artist unlocks it.

The complete Vertex Group state is snapshotted before the native solver runs.
In Full Auto Blend, unlocked Deform groups may be rewritten only inside the
selected groups' old/new influence union; non-Deform groups, locked groups, and
all membership outside that union stay exact, except that invalid generated-side
base weights on a supported half Mesh are cleared as described below. In strict
mode, every group not named after a selected Deform bone stays exact everywhere.
Mesh X Mirror is temporarily disabled, while face/vertex paint masks are temporarily disabled so
the calculation covers the complete base Mesh. All three flags are restored in
`finally`. Any protected-state change, native cancellation, unusable selected
result, or Python failure restores the complete original Vertex Group state
before returning.

The UI starts with **Full Auto Blend (Normalized)** enabled. It does not use a
single isolated bone as the heat source: all editable current-rig Deform bones
participate, so the selected result is the same spatial field it would receive
inside a complete Automatic Weights calculation. Only vertices influenced by
the selected groups before or after that solve are replaced. At each such
vertex, locked Deform weights first reserve their exact share. Selected solver
values then receive first claim on the available budget; the full solver's other
unlocked Deform values are scaled into the remainder. Consequently an unlocked
weight-1 core stays at 1, while a locked reservation can reduce the selected
value. Utility, mask, foreign, and non-Deform groups stay exact. “Normalized”
means that affected Deform totals equal one; this command does not run an
additional topology Smooth pass. The spatial transition comes from the complete
Bone Heat solver's local proportions. If the locked budget exceeds one, leaves
no room for the selected result, or the full solve has no support for a required
repair, the whole operation rolls back. One Undo removes the complete result
(including new groups), and one Redo restores it.

Turning the checkbox off retains the historical strict mode for scripts and
special cases: Blender recalculates only the selected groups and every other
group is verified byte-for-byte unchanged. That isolated solve is not a full-rig
weight column and can reach distant geometry when no neighboring bones compete,
so it is not recommended for ordinary character binding. Strict mode does not
normalize the combined Deform column; its final per-vertex total may exceed one.

For a true half Mesh that relies on a Mirror modifier's Vertex Group mirroring,
only source-side and center bones can own meaningful base-mesh weights. A bone
located solely on the generated side is refused with a normal warning; select
its source-side counterpart instead. Blender requires both `.L`/`.R` group
definitions before the Mirror modifier can flip evaluated membership, but only
the source-side group should own base vertices. Strict mode adds a missing empty
pair. Full Auto Blend clears every generated-side Deform group on the base Mesh,
including stale weights unrelated to the selected pair, then verifies that all
of those groups remain empty. The supported stack has one
object-local, X-only Mirror with **Vertex Groups** enabled before Armature;
Subdivision may be before or after that Mirror. The tool refuses ambiguous
Mirror stacks and shared Mesh data rather than claiming success when an opposite
bone could not independently deform its side.

## Spline IK Setup

**Spline IK Setup** builds the repetitive Curve, Hook-controller, and
constraint wiring for a flexible strap or other connected bone chain. It does
not create or edit deform bones, vertex groups, weights, or meshes.

1. In Armature Edit Mode or Pose Mode, select the exact continuous span the
   Curve should cover. Selecting the full chain produces a Curve from the
   captured root Head to the captured tip Tail; leaving rigid end bones
   unselected produces a flexible middle-only span.
2. Click **Capture Selected Chain**. The selection must be one unbranched,
   gap-free parent-to-child path with at least two bones.
3. Set **Curve Points** (default 5) and **Control Size Ratio** (default 6% of
   the chain's local length), then click **Build Spline IK Setup**. A stored
   capture can be built directly from Object Mode even when another object is
   active.
4. Click **Select & Reveal Controls** to select and frame every controller,
   enable Viewport Extras, and reveal the active middle controller in the
   Outliner. Move the generated spherical middle controls to bend the Curve. Move or
   rotate either cube end control to position the end and steer its Bezier
   tangent. Every controller exposes a Curve/strap-aware local frame: X follows
   strap width, Y follows the exact hooked Bezier tangent, and Z follows the
   evaluated strap-front normal. Rotate local Y to drive the matching Curve
   point's Tilt 1:1. The same Y-first rotation channel is distributed smoothly
   across the captured chain as incremental bone Roll, so a weighted non-round
   strap visibly twists instead of only changing Curve metadata. Local X/Z can
   be combined to steer the Curve without leaking into Tilt.

New setups include **Rotation Tilt** automatically. An older valid setup exposes
one contextual action: **Enable Rotation Tilt** when the drivers are absent, or
**Align Rotation Tilt Axes** when its saved v1/Swing drivers still use the old
frame. A fully enabled older setup shows the same contextual action once as
**Clean Hook Names**. Version 0.14.1 keeps the generated rig UUID exclusively
inside ownership metadata; visible modifiers use `CD Spline IK Hook 01`, `02`,
and so on. Existing UUID-bearing modifiers are renamed in place without
replacing their pointers, bindings, animation paths, or evaluated Curve. All
three paths work in place. They derive width/front from the evaluated
deform-bone frame, align the visible Empty axes, and use the Curve's actual
evaluated four-sided profile direction rather than the loose-edge Mesh `normal`
(which is only a tangent). The missing-driver path rebases Curve Tilt so a
zeroed controller represents the authored strap front. The v1 path freezes the
current evaluated Tilt and bone Roll into the new zero point, then converts the
same FCurves to isolated YXZ targets. Hook inverse compensation preserves the
Curve and evaluated bone pose while the controls change orientation. The small
profile probe exists only during evaluation and is removed immediately; no
persistent helper object or dependency cycle is added. Local axes are shown on
the controls. Artist bone constraints, unrelated animation, and the existing
X/Z Curve shape remain unchanged.

An invalid recapture never replaces the last valid chain. It reports a neutral
warning that the previous capture was kept. Routine mode/selection preconditions
also remain non-red; red status is reserved for a failed data transaction or
damaged generated setup, and every status message has a dismiss button.

The Curve and controls are created in Armature-local space and parented to the
Armature, so the complete setup follows its object transform. Every Bezier
point, including its two handles, is bound to its corresponding wire Empty by
one Hook modifier. Controls are independent after creation; no symmetry is
forced, so an initially symmetric chain may be adjusted symmetrically or
asymmetrically.

The Spline IK constraint is placed on the selected tip bone with its Chain
Length equal to the captured bone count. Y Scale and XZ Scale are both `None`,
Curve Radius, Even Divisions, and Chain Offset are disabled, and the constraint
is unmuted. This strictly prevents the Spline IK solver from introducing bone
scale. Consequently, if an artist later makes the Curve longer than the fixed
bone chain, the tip cannot both remain unscaled and reach the Curve endpoint.

Generated data carries an exact versioned internal UUID; it is not exposed in
artist-facing Hook modifier names. Rebuilding or **Remove Generated** only
removes a matching Character Designer setup. A foreign Spline
IK on the selected tip blocks Build by default; **Replace Existing Spline IK**
must be enabled explicitly, replaces only one unambiguous constraint, and never
deletes its target Curve.

If the generated Curve Object itself has artist Action, Driver, or NLA data,
**Remove Generated** stops before deleting anything and reports a normal
warning. This avoids pretending to recover Object animation that Blender's
manual Curve-object removal backup cannot reproduce losslessly.

## Generic Reference View Sets

The **Reference Views** panel creates spatial image references for any subject:
a prop, vehicle, building, room, creature, or character. It does not assume a
human height or anatomy. Codex Console (or another tool) writes a set beneath
`//References/CDesigner/<set>/reference-views.json`; **Scan Standard Folder**
finds those manifests and **Create / Update Views** synchronizes the selected
set.

The version-1 contract is:

```json
{
  "schema": "blackunity.cdesigner.reference-view-set",
  "version": 1,
  "setId": "2ed7799e-5b80-41da-a8cf-502aa32d09db",
  "name": "Any Subject",
  "updatedAt": "2026-08-04T08:00:00Z",
  "units": "METERS",
  "placement": {
    "origin": [0.0, 0.0, 0.0],
    "displaySizeMeters": 2.0,
    "distanceMeters": 10.0,
    "opacity": 0.35,
    "showInFront": true
  },
  "views": {
    "front": {
      "file": "front.png",
      "enabled": true,
      "flipHorizontal": false,
      "flipVertical": false,
      "distanceMeters": null,
      "displaySizeMeters": null,
      "opacity": null
    }
  }
}
```

`null` per-view values inherit `placement`. Image paths use drive-free POSIX
relative syntax and are rejected for backslashes, `.`/`..` components, or any
real-path escape from the manifest folder. Unknown fields are ignored
for forward-compatible producers, while schema, version, UUID, units, numeric
ranges, booleans, and enabled image files are validated before the Scene is
changed. Meter values are divided by `Scene.unit_settings.scale_length` to get
Blender Units.

Project-local loaded Images are stored with Blender `//` paths so moving the
saved `.blend` together with its `References` directory keeps links portable;
images outside the `.blend` directory retain absolute paths.
When Blender opens a Temp recovery file such as `quit.blend` or an autosave,
Character Designer detects only its exactly tagged managed Images whose `//`
path is now missing and reconnects them from the validated absolute source path
stored with the view. Existing valid paths, packed Images, and user Images are
left untouched. Version 0.7.3 defers this current-file check until add-on
registration has left Blender's restricted-data context, so both normal enable
and **Refresh Add-on** can complete safely.

An unused direction may be omitted or use `"file": null`. A null file is always
an unconfigured slot—even if an older producer accidentally leaves
`"enabled": true`—so a partial Front-only set remains usable. When a previously
created direction becomes empty or disabled, synchronization hides its managed
Empty but does not delete it; only **Clear Current Set** performs deletion.

Each Scene receives one tagged `C Designer References` collection. Every set
has a tagged root Empty and up to six tagged Image Empties. The image planes are
single-sided toward the subject so opposite views do not overlap; all remain
non-rendering. Synchronization leaves Edit Mode, the active object, and the
selection untouched. A failure rolls back new data and restores already managed
objects. **Clear Current Set** never claims same-named user data and retains a
managed object if another Scene or an unowned child depends on it.
Synchronization and visibility changes are refused before mutation when their
managed collection or objects are shared by multiple Scenes, preventing one
Scene's operation from silently changing another Scene.
Standard-folder discovery also refuses symlink, Junction, and other Windows
reparse-point directories, and rechecks every traversed directory and manifest
against the real `//References/CDesigner` boundary.

The WindowManager controls are intentionally session-only, but managed Scene
objects are persistent. On reopen, **Managed Scene Sets** reconstructs available
sets from exact current-version tags. A single set is adopted automatically;
multiple sets require an explicit UUID-backed dropdown choice before Show,
Hide, or Clear becomes available. This recovery does not require the original
manifest to still exist.

## Build Symmetry

Open **Miscellaneous > Build Symmetry**. This tool links the two topological sides of an asymmetric mesh
without forcing their absolute positions to become mirror images. It reflects
only the movement delta: an X-axis local delta `(dx, dy, dz)` becomes
`(-dx, dy, dz)` on the paired vertex, while both vertices keep their existing
different baselines.

1. Edit one Mesh and select either one continuous, non-branching center edge
   chain/loop, or two adjacent center-band boundary chains. For a center band,
   both boundaries must have matching open/closed structure and edge count;
   every boundary edge must have one band face and one outer face.
2. Choose **Local** or **World** and the reflection axis, then click **Build
   Symmetry**. The topology is detected automatically; there is no mode switch.
3. Enable **Auto Select Opposite** in Vertex Select mode. Selecting or
   deselecting a paired vertex immediately applies the same selection state to
   its partner while keeping the vertex you clicked active. To clear a linked
   pair, Shift-deselect its active/source vertex or use `Alt+A`.
4. Enable **Mirror Movement** and use normal Blender transforms. It remembers
   which side you selected first, so multi-vertex transforms still use the
   intended side as the driver.

Pairing is propagated face-by-face from the chosen centerline or center band.
With a band, its two boundary rows become the first vertex pairs, then pairing
grows outward. On an even-column closed tube, the two directions may meet at a
topologically proven opposite self-mapped face, so selecting only the front
band boundaries is sufficient. The tool supports matching quad, triangle, and
n-gon topology and never substitutes a nearest spatial vertex when topology is
ambiguous. Side labels are assigned to whole topological components, not sorted
independently for every pair.

Mirror Movement currently supports one edited Mesh at a time. While it is on,
Character Designer temporarily disables Blender's native Mesh Mirror axes and
restores their exact previous state when the feature turns off, the add-on is
refreshed, or the file is saved. Auto Merge and Proportional Editing remain
untouched; if either conflicts with the workflow, Mirror Movement simply turns
itself off. Leaving the paired mesh, changing topology, or leaving Vertex
Select mode likewise turns the relevant toggle off without a persistent red
error. Native modal translation with Auto Select Opposite has been verified as
one Undo/Redo step, and Esc restores both sides. The pair map is deliberately
session-only and must be rebuilt after an add-on reload or Blender restart.

## Hair Centerline

Character Designer turns selected hair cross-sections into an exact, shaped
center curve with one visible action and one compact placement selector:

1. In Mesh Edit Mode, select the complete connected strip that should define
   the centerline.
2. Choose **Centered**, **Surface**, or **Blend**. Centered preserves the
   original section centers and allows the Half profile to protrude; Surface
   keeps the evaluated visible front flush with the source mesh. Blend reveals
   a **Mix** slider: 0 is Centered, 1 is Surface, and the full range is usable.
3. Click **Generate / Update Centerline**. If the current scene already has a
   Character Designer Curve with the same source-layer record, that exact Curve
   is updated in place. A new Curve is created only when the source has no
   generated Curve in the current scene; a related but nonmatching or ambiguous
   target stops with a normal informational message and creates nothing.
4. With a generated Curve active in Object Mode, the same control reads
   **Refresh Centerline**, re-reads its recorded source layers, and preserves
   that Curve's saved placement and Mix.

Generation uses the selected placement mode; Object Mode refresh uses the
Curve's saved placement. **Blend** first solves the complete Centered and
Surface endpoints, then interpolates every Curve point by Mix and recomputes
Tilt from the resulting Curve's own tangent; raw Tilt values are never averaged.
The dominant-ribbon front direction is shared by every value, so changing Mix
cannot turn the Half profile into the head. Old fixed-Blend Curves without a
stored Mix read as exactly 0.5. The legacy preview, alignment, and explicit-target
operators remain registered for file and shortcut compatibility but are no
longer exposed as separate panel buttons.

Open rows, closed profile loops, and a collapsed `POINT` tip are supported. The
selection parser is independent of Blender's vertex/edge/face selection mode.
For example, four selected triangular profile loops plus one pointed tip are
read as `3 → 3 → 3 → 3 → 1` and create five center points; selected side faces
are never mistaken for one large root cap.

Each valid topological layer contributes one preview point at the arithmetic
mean of that layer's current vertices. The points stay in source-object local
space and the source transform is copied to the confirmed Curve, preserving
the original placement and local precision.

The confirmed Curve is immediately usable as a hair profile. It uses a
**Round** bevel with **Half** fill. The widest source cross-section sets the
default Depth, while every control point receives a proportional Radius. A
collapsed `POINT` endpoint keeps 1.5% of its nearest non-`POINT` loop's Radius:
visually pointed at normal scale, but still nonzero when inspected closely.

For a closed four-sided strand, width comes from the longest real boundary
edge rather than a diagonal vertex chord. Four-point rectangles and rounded
rectangles with more vertices both identify their two dominant longitudinal
face ribbons. The outward ribbon is chosen once for the full strip from
cumulative surface area and the direction opposite centerline curvature, with
the source-origin radial test retained only as a fallback. This prevents a
single noisy segment or reversed polygon winding from flipping the profile into
the head. Point Tilt aligns the Half-round bulge to that outward normal and is
unwrapped continuously along Blender's Minimum-twist frame.

Closed profiles without a dominant opposite face pair keep their complete
tangent-plane span, so a smooth regular 8- or 10-sided profile is not reduced to
one polygon edge. Open rows likewise use their complete end-to-end span. The
one-action workflow does not modify the source mesh or its selection. In-place
updates retain the Curve object, its materials, collections, and identity.

### Recover Applied Curve

When hair was previously converted or applied to a Mesh, select its complete
regular surface band in Mesh Edit Mode and click **Recover Applied Curve**. You
may select several disconnected strands at once. For a clean quad strand, a
single unbranched longitudinal edge path is also enough; its endpoints define
the recovered range and Character Designer expands it across the complete
cross-sections without changing the visible selection.

Each connected strand becomes one independent Curve Object. Choose **Bezier**
for a smaller, smoother editing cage: its **Control Points** slider is bounded
to 3–32 and defaults to 5. The endpoints stay exact, while interior position,
Radius, and continuously unwrapped Tilt are interpolated uniformly by source
arc length. Explicit **Aligned** Bezier handles keep the reduced cage smooth,
stable, and directly editable without relying on Blender's Auto-handle solver.
Choose **Exact** when geometric fidelity matters more than control count; it
keeps one Poly point for every recovered source cross-section. Changing the
mode or point count and repeating the action updates the same Curve Object.
Use the slider—not manual point deletion—to change the managed cage size.
Moving points or handles is normal Curve editing, but running Recover again
rebuilds the cage from the selected source Mesh and therefore replaces those
manual Curve edits. A manually added/deleted control point is treated as an
unreadable managed output and is refused rather than guessed over.

Point Radius keeps Alt+S meaningful, and point Tilt restores roll around the
local path. Common applied hair profiles are reconstructed without helper
objects:

- Round + Half with Extrude;
- Round + Full capsule profiles;
- rectangular Full profiles using Curve's embedded Profile data.

For Half profiles, the open endpoints identify the applied axis. Character
Designer also solves an equivalent Curve Offset when the section planes provide
enough evidence; otherwise the equivalent position stays in the recovered
control points with Offset zero. Mesh geometry cannot uniquely reveal the
historical split between Offset, control-point position, and some profile
parameters, so Exact mode promises equivalent editable geometry rather than the
original hidden parameter history. An applied Mesh cannot recover deleted
historical Bezier handles; Bezier mode creates a new explicit Aligned-handle
cage and is intentionally a smooth approximation when its point count is
reduced. A strand whose width/depth ratio changes along its length is refused
instead of being silently approximated by Radius alone.

**Recovery Preview** draws the current result directly in the viewport using
the GPU. Curve type, control-point count, and output changes update it
immediately, so the cage can be judged before recovery is confirmed. Preview
never deletes the source Mesh and never creates a temporary Object or datablock.
Turning preview off, confirming recovery, or refreshing the add-on clears all
preview drawing state. If the source has a compatible Mirror/Subdivision stack,
Preview also names the original-order stack that the Curve will retain.

The **Output** choice controls source ownership:

- **Add** is the default. It keeps the source Mesh Object and creates or updates
  the matching recovered Curve Object.
- **Replace Mesh** is destructive only after every strand in the complete source
  Mesh has passed preflight and the entire Curve batch has succeeded. It then
  removes the source Object, but does not delete its Mesh datablock; shared Mesh
  data therefore remains available to other Objects.

Replace Mesh refuses a partial source selection rather than deleting unselected
geometry. Mirror and Subdivision Surface are transferred in original stack
order, so ordinary combinations of either modifier no longer block Replace.
Other modifier types are refused. Sources with parent/child relationships,
constraints, animation or drivers, shape keys, links to multiple scenes, or
other ownership that cannot be transferred safely are still refused.
Replacement is a single Undo transaction, and any failure before source removal
leaves the source Object intact. A managed recovery output may update its copied
stack, but an extra or reordered artist modifier is never silently deleted.

In Add mode, recovery objects keep the exact source Mesh identity plus a
direction-independent source-layer signature. Renaming a source or reusing its
former name cannot send an update to the wrong Curve. The same component
updates its existing Curve Object; another component from the same Mesh creates
its own Curve and can later be refreshed independently. Replace outputs retain
their provenance metadata after the source Object is removed. The whole
selection is preflighted before any write, and creation/update is all-or-nothing
across the batch.

## Source Metadata

The confirmed Curve keeps lossless, versioned source metadata behind the
automatic profile. For every layer it records:

- the raw maximum vertex-to-vertex chord and the vertex pair that defines it;
- the maximum profile span used as the cross-section sizing reference;
- the longest real boundary edge and its vertex pair;
- the final shaping width, front normal, width axis, source face, and
  orientation source;
- the local center, the exact front-surface target and how it was found, the
  tangent, legacy width axis, and raw mesh normal.

The metadata is stored in `SOURCE_OBJECT_LOCAL` space together with the source
matrix at confirmation time. Metadata v4 added the Centered and Front Surface
solutions; v5 refreshes the front semantics with continuous dominant-ribbon and
curvature evidence. Valid v2, v3, and v4 Curve metadata remains readable and is
safely upgraded from its recorded source by the one-action workflow.
`profile_tip_radius` stores a ratio applied to the nearest non-`POINT` layer
Radius, not a global Radius. `shape_span_local` uses the longest boundary edge
only for a four-sided `CLOSED` layer; every other non-point layer keeps its full
tangent-plane profile span.

## Refresh Add-on

Character Designer watches its installed Python files. **Refresh Add-on** is
hidden while the loaded code matches disk and appears only when source changed,
a refresh is running, or an error needs attention. Refresh validates the Python
files and then reloads only the add-on modules; it neither saves nor reloads the
current `.blend`, so unsaved modeling remains in Blender memory without a save
prompt. Successful reload establishes the new source signature and the button
disappears. Session-only live preview and symmetry capture are stopped
safely as part of unloading the old runtime.

## Workspace Filter Guard

Character Designer preserves Blender's per-workspace add-on filtering. In each
workspace where filtering is enabled, it only ensures that Character Designer
and the owner required by the current render engine are present. For Cycles,
that owner is `cycles`, which keeps Material Slots, Preview, and Surface
available in Material Properties. Existing allow-list entries are never removed
and filtering is never disabled. The guard runs after registration and continues
to cover project loads, add-on refreshes, workspace changes, and render-engine
changes.
