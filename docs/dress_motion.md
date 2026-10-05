# Dress automatic motion

The default **Setup Dress** choice is **Physics + Manual**. It reuses the
character armature and Dress mesh, retains the fitted wire controls, and adds
a connected native Cloth surface with a fixed waist and closed pelvis/leg
collision proxies. Original skin weights and Shape Keys are preserved after
the existing setup has been built. Internal manual, physics and final DEF
chains share that setup; changing motion modes does not rebuild them.

**Automatic** enables the actual-surface Cloth displacement layer. **Manual**
disables that layer and retains the wire controls, their animation, and local
Dress pose corrections. Automatic mode also retains those corrections and the
manual baseline for optional refinement. The Physics generation choice enables the
automatic workflow; Manual creates no Cloth surface; Physics + Manual allows
both motion modes. Internal fit controls remain owned data in every choice.

## Daily workflow

1. Select the Dress mesh and use **Setup Dress**. Existing setup geometry is
   reused; it is not silently replaced after topology or ownership changes.
2. Use **Automatic**, then **Reset** and play from the simulation start frame.
   Work on the character animation; leg motion affects the skirt through
   collision rather than binding the whole surface to a thigh.
3. Use **Tuning** for bend, damping, shape recovery, clearance and quality.
   Advanced holds material, waist goal, gravity and self-collision settings.
   Only changed fields are applied. Batch tuning explicitly selects all
   registered physics Dresses on the same Main Rig.
4. Use **Bake** for stable seeking/playback. Reset a sealed cache before
   changing physical material settings, then replay or bake again.
5. Open **Manual Refinement & Export** for the original rings, wire handles
   and collider fitting. Automatic and Manual are complete endpoints for the
   actual-surface backend; an intermediate influence is rejected. A baked Cloth
   cache still drives the physics layer, so manual wire shaping
   and local Dress pose corrections remain available without baking again.
   Changes to character animation or colliders require resetting/rebaking.

The tuning dialog reads current native settings without changing them on
opening or cancellation. Saved configuration belongs to the Dress source,
with a version and setup identity, and survives reopening the `.blend`.
Settings from a different owner or changed chain structure are rejected.

## Native parameter meanings

- Stretch resistance changes Cloth tension and compression together. Shear
  and bend are separate native springs on the connected circumferential and
  vertical surface.
- Damping changes native tension/compression/shear damping together. Bend
  damping and air damping are independent. Material fields that were not
  changed retain the artist's native values.
- Shape recovery adds weak, depth-decaying waist-relative goals to the Cloth
  pin group. It does not modify thigh following, Rest bones or skin weights.
  Zero recovery/waist depth retains the earlier fixed-waist pin pattern.
- Fixed waist depth expands the fully fixed rings; waist transition controls
  the first soft ring. The moving hem is never fully fixed by recovery.
- Body/self clearance is a fraction of fitted Dress height. Native RNA bounds
  are checked at the actual model scale; an unrepresentable value is rejected
  before writing rather than silently clamped and saved incorrectly.
- Gravity multiplies scene gravity. Motion inertia comes from the native
  moving Cloth surface and its mass/damping; there is no separate artificial
  inertia slider or claim that these are Magica parameter equivalents.

The pending actual-surface backend simulates the source's exact raw vertex
indices. Its final output adds the simulated displacement to the current
manually posed Dress, subtracting an independently evaluated neutral surface
to avoid applying the same motion twice. Existing asymmetric Shape Key and
manual deformation deltas remain in the final output. A separate tracker still
drives the existing PHYS/DEF chains; it does not substitute its coarse surface
for the final vertex result. Collision acceptance must inspect the final Dress.

The earlier bone-driven Cloth cage remains readable for explicit migration.
The new panel's physics setup requests the actual-surface backend; it refuses
to discard a sealed legacy cache or rebuild edited geometry implicitly. This
pending backend has not yet been installed in the artist scene.

## Export boundary

Simulation Bake saves Blender's native Cloth cache. A bone-only animation copy
cannot preserve the actual-surface vertex displacement and is refused for this
backend. Static Unity export validates the owned setup and excludes its internal
simulation helpers; it does not export a baked Cloth animation. Unity's separate
Magica simulation must be validated against the actual imported Dress. Blender
and Magica settings or results are not treated as interchangeable.

## Safety and validation status

Setup, tuning, cache actions and baking prove proxy topology, local ownership,
sample/pin/binding weights, exact PHYS/DEF constraints and collision graph.
Closed, independent pelvis/leg collider vertex fitting remains allowed through
the fitting action. The Body attachment shares the artist Body mesh and is
excluded from selection for fitting. Edited or foreign targets,
modifiers, animation or incomplete ownership are rejected before mutation.
Posed attachment bones are converted to Rest coordinates when creating
colliders, avoiding applying an existing pose twice.

Reset releases a sealed cache, explicitly invalidates unsealed data and
returns to the native start frame. It retains original Actions and cache step.
Material tuning invalidates only an unsealed cache; mode changes keep sealed
cache data. A failed tuning transaction restores parameters, goal weights,
influence and saved records; unsealed frame data may require replay afterward.

Earlier Blender 5.1 fixtures verify the legacy structure/ownership, posed
attachments, cache/reset replay, parameter isolation, batch rollback, native
limits and save/reopen. The pending actual-surface backend has a separate
real-model installation gate and requires fresh native rollback, static export
and motion evidence after the current source changes. Representative Cosha motion and final skin collision
checks, interactive artist integration, and Unity Magica effect validation are
still pending. These fixture passes do not establish the full goal's natural
motion or cross-engine equivalence. No production scene has been modified by
the new Dress runtime yet.
