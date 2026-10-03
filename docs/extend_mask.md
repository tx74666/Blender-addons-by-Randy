# Extend Mask

Extend Mask generates a new border band from the boundary data of a ring or
arc. It does not expand an arbitrary black/white image mask. The original band
is excluded from the resulting **Mask**, so the new band can use another shader.

## Add and connect

1. In the Shader Editor, add **Shift+A > Textures > Ring Mask**.
2. Set its Inner Radius, Ring Width and angles. **Sweep Angle 360** makes a full ring.
3. Add **Shift+A > Textures > Extend Mask**.
4. Connect **Ring Mask > Ring Data** to **Extend Mask > Source**.
5. Connect the resulting **Mask** to a Mix Shaders slot and provide the shader
   for the new band. The original Ring Mask remains independently available.

The nodes are native Shader Node Groups. Saved computation and connections
require no add-on. There is no boundary list or **+ / −** boundary-input UI.

## Controls

| Control | Behavior |
| --- | --- |
| Mode: Inner | Create a band toward the center from the source's inner boundary. |
| Mode: Outer | Create a band away from the center from its outer boundary. |
| Mode: Both | Create both radial bands using the same Width and Gap. |
| Mode: Outline | Extend the complete contour, including the end caps of an arc. |
| Width | The thickness of the new band. |
| Gap | The separation from the source boundary; 0 touches it directly. |
| Softness | The transition at the new band's edges. |

Width, Gap and Softness use the same normalized UV distances as Ring Mask,
not world meters. A correctly centered circular UV map is required.
**Inner**, **Outer** and **Both** retain the source's Start Angle and Sweep
Angle; they do not extend the arc's angular ends.

For example, a source ring from radius 0.26 to 0.27 with Width 0.005 and Gap 0
produces an inner band from 0.255 to 0.26 and an outer band from 0.27 to 0.275.

## More bands and materials

For another radial layer, connect **Inner Data** or **Outer Data** to another
Extend Mask's Source. Those outputs describe the new bands independently;
use one Extend node for each branch when continuing both sides. For two
independent source rings, extend each source separately.

When several bands share a shader, combine their scalar Mask outputs in a
**Ring Group** and supply that shader once. To retain different materials,
use separate **Mask / Shader** slots in **Mix Shaders**. Include the original
Ring Mask in the union only when it should use the same shader.

**Outline** supports its final Mask in this first version. Its Inner Data and
Outer Data are invalid for further radial chaining because the result also
contains end-cap contours. Use radial modes for successive concentric bands.

## Existing files

Historical three-input **Ring Mask 0.1.1** and **Arc Mask 0.1.x** groups do not
have Ring Data. Updating
the asset library does not automatically replace local groups already stored
in a .blend file. Add the current asset from **Textures** for this workflow;
choosing an older local or linked Ring/Arc Mask from **Add > Group** can still select the old
interface. Existing Mask connections should remain intact during any explicit
upgrade. The current multifunction **Ring Mask 0.2.1** replaces the visible Arc
asset and defaults to a full 360-degree ring. Arc Mask 0.2.0 already has compatible
Ring Data and remains usable in existing materials. Explicit migration after a
backup preserves proven old graphs; customized or animated owners are not
silently replaced, and linked source files are never changed.

See the current [Ring Mask](ring_mask.md), [Ring Groups](ring_groups.md) and
[Mix Shaders](shader_mixer.md) for their normal mask/material workflows.

## Verification

The unchanged Extend Mask 0.1.0 saved asset passed 131 checks with Arc Mask 0.2.0:
64 independently calculated shader
samples in Cycles, the same 64 in EEVEE, native Bundle/Menu save and reopen,
and asset-preservation checks. These include inherited arc angles, complete
end contours, cropped bands, softness, missing inputs, large gaps and radial
branch chaining. The examples below are actual Cycles renders, from left to
right: full-ring radial bands, arc radial bands, complete arc outline and
continued radial branches.

![Extend Mask examples](../node_library/previews/extend_mask.png)

The assets were deployed into the existing local Textures library. A running
Asset Browser still needs Library > Refresh; this verification did not operate
the live Builder6 scene or inspect its menus. Compatibility of the current
multifunction source is recorded separately in
[ring_mask_current.json](../node_library/validation/ring_mask_current.json).
Local installation does not mean
the owner has committed or pushed the files to GitHub.
