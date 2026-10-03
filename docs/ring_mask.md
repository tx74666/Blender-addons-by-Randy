# Ring Mask

**Ring Mask 0.2.1** is the current multifunction node for both full rings and
partial arcs. Add it through **Shift+A > Textures > Ring Mask** in the Shader
Editor. Its native **Texture** color tag uses Blender's normal brown header.
There is no separate Ring/Arc mode panel or extra standalone Arc asset.

## Controls and outputs

| Input | Default | Behavior |
| --- | --- | --- |
| Inner Radius | 0.6 | The inner boundary in normalized UV space, clamped to 0–1. |
| Ring Width | 0.08 | The width extending outward from the inner boundary. Zero hides the mask. |
| Edge Softness | 0 | An inward transition at the radial edges, capped at half the width. |
| Start Angle | 0 | The start direction in degrees, counterclockwise from UV right. |
| Sweep Angle | 360 | The angular span: 360 is a full ring, 180 a semicircle, and 0 empty. |

To make one arc, keep the same node and reduce **Sweep Angle**. Change
**Start Angle** to rotate it. The span can cross the 0/360 seam; the angular
cuts stay hard while Edge Softness affects the radial edges.

| Output | Use |
| --- | --- |
| Mask | Scalar coverage from 0 to 1, for a material mixer or a Ring Group. |
| Ring Data | Native boundary and angle data for **Extend Mask > Source**. |

The mask generates no shader by itself. Connect a BSDF or imported material
shader separately to a Mix Shaders slot. When several rings share one shader,
combine their Mask outputs in Ring Group and provide that shader once.

## Coordinates

The active render UV map should put the circular surface inside UV 0–1, with
its center at `(0.5, 0.5)`. The circular rim has radius 1 after normalization:

```text
Position = 2 * (UV.xy - (0.5, 0.5))
Radius = length(Position)
```

Radii and widths use normalized UV distances, not world meters. The node clips
outside radius 1 and does not move vertices or change object transforms.
Overlapping mirrored UV halves can repeat a single arc while leaving full
rings apparently correct; see the [historical UV troubleshooting notes](arc_mask.md#if-one-arc-appears-twice).

## Extend the edges

Connect **Ring Data → Extend Mask > Source** to avoid repeating the original
radius, width and angles. Inner, Outer and Both make new radial bands while
keeping the arc's angle range; Outline also surrounds the arc's two cut ends.
Connect Inner Data or Outer Data to another Extend Mask for further radial
bands. See [Extend Mask](extend_mask.md) for the chaining limits.

## Existing files and old search entries

The historical three-input **Ring Mask 0.1.1** and five-input **Arc Mask** groups
may still be stored or linked in an existing .blend file. Installing the
current library does not replace those groups automatically. **Group > Linked
> Ring Mask** can therefore still refer to an old linked datablock; its name
does not establish that it is the new multifunction node.

Refresh the existing Nodes Asset Browser with **Library > Refresh**, then use
the current **Textures > Ring Mask** asset. A current node has all five controls
and **Ring Data**. Reusing a historical Group-menu entry can select its old
interface again.

Migration of existing nodes is explicit, after a backup. The migration helper
first compares the complete calculation with independently loaded historical
references. It preserves parameters, external connections and node presentation;
old three-control rings receive Start Angle 0 and Sweep Angle 360, while Arc
nodes retain their existing angles. Customized, animated and read-only owners
are refused. No render, update or save handler performs migration.

Only verified superseded groups with no actual users can be removed from the
current file afterward. There is no global purge, and linked source library
files remain unchanged. Live Builder6 migration and menu refresh require
separate verification; a factory test does not establish their completion.

## Source and verification

The current builder is `tools/randy_node_assets/build_ring_mask_current.py`;
the historical `build_ring_mask.py` builds only the original radial fixture.
The current node retains Arc Mask 0.2.0's native Mask and Ring Data calculations
and socket identifiers, changes the public name, and uses a full-ring default.

The current validation report is
[ring_mask_current.json](../node_library/validation/ring_mask_current.json).
The saved asset passed **96 checks**: 46 independently calculated shader samples
in Cycles, the same 46 in EEVEE, and four structure, persistence and source/asset
preservation checks. These verify the preserved Arc 0.2.0 calculation and socket
identifiers, full-ring defaults, controlled arcs, Ring Data/Extend connections
and native save/reopen. Historical evidence is not relabeled as a new render run.

The isolated current-file migration fixture also passed ten checks covering
old ring/arc controls, nested editable owners, exact external connections,
presentation, customized-source refusal and rollback after an injected failure.
This factory fixture did not operate the live Builder6 file or verify its menus.

The preserved Arc binary is
`node_library/validation/fixtures/Randy_Arc_Mask_0_2_0.blend`, outside the visible
asset directory, with its original evidence in
[arc_mask_0_2_0_baseline.json](../node_library/validation/arc_mask_0_2_0_baseline.json).
The original radial binary and its 0.1.1 evidence remain separate historical
fixtures. Saved Ring Mask nodes evaluate without RR Helper; adding the asset
through Blender's menu does not require a plugin panel.
