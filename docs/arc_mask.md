# Arc Mask

Arc Mask is a native Shader Node Group in **Shift+A > Textures**. It makes a
full or partial ring using the existing Ring Mask radial graph and a
small angular gate. It needs no RR Helper at edit or render time. Adding this
asset does not change an existing material or the original Ring Mask asset.

Since library **0.2.1**, Arc Mask is the only exposed circular mask asset.
Use **Sweep Angle 360** for a full ring, or any smaller positive sweep for an
arc. No extra mode switch is needed. Its title uses Blender's **Texture** color
tag. The old Ring Mask implementation remains an internal radial dependency,
outside the public asset directory; it is not another node to add or manage.
Arc Mask 0.1.1 also hides that embedded dependency from the ordinary Group menu.

Arc Mask **0.2.0** appends **Ring Data**, a native Bundle output that passes
the normalized UV position, visible radial boundaries and angular span to
[Extend Mask](extend_mask.md). Its original Mask calculation, controls and
socket identifiers are preserved. Connect Ring Data to Extend Mask's Source
to add inner, outer or both border bands without repeating the Arc controls.
Previously appended local groups need an explicit upgrade or a new asset
instance to expose this additional output.

| Input | Default | Meaning |
| --- | --- | --- |
| Inner Radius | 0.6 | Same normalized UV inner radius as Ring Mask. |
| Ring Width | 0.08 | Width extending outward from the inner edge. |
| Edge Softness | 0 | Same radial fade as Ring Mask; angular ends stay hard. |
| Start Angle | 0 | Degrees counterclockwise from UV right. 90 is up. |
| Sweep Angle | 180 | Degrees of arc. 0 hides it; 180 is a semicircle; 360 is a full ring. |

Start Angle rotates the arc, so a separate Rotation control is unnecessary.
The sweep crosses the 0/360 seam normally: start 300 with sweep 120 draws from
300 through 0 to 60 degrees. Linked negative sweep values become 0; linked
values above 360 become 360. Start angles outside the displayed range wrap.

The coordinate contract is exactly Ring Mask's: the active render UV map has
center `(0.5, 0.5)` and a circular rim of normalized Radius 1. Arc Mask does not
accept Geometry data or world-position coordinates. Such a socket would give
its angle and the existing Ring Mask radius different coordinate systems.

Use the **Mask** output with an ordinary Mix Shader, with a mask union for
several rings using one shader, or with other mask composition nodes. Multiple
instances can have different start, sweep, radius and width while sharing the
native Arc Mask group and its one native Ring Mask dependency. This combines
masks, not Material datablocks, and introduces no extra shader per arc.

## If one arc appears twice

Check the active render UV map for mirrored or overlapping islands. A full
Ring Mask can look correct on a folded UV map because reflection preserves
distance from the center. Arc Mask also reads direction, so that same fold
repeats its angular selection on both halves of the mesh.

For one continuous arc over the whole floor, map the entire circular surface
once into the centered UV square; do not stack the left and right halves.
Applying a Mirror modifier does not automatically remove existing UV overlap.
Unfolding the mirrored half preserves the radius when its U coordinate changes
from `u` to `1 - u` around the same center; that repair is appropriate only
after confirming the specific fold and other textures that use the UV map.

The Builder6 floor diagnosis on 2026-10-02 reproduced two arc components with
its folded UV map and one after unfolding, in both Cycles and EEVEE. The tested
Start / Sweep angles were 18.5 / 321.6 degrees. Arc Mask 0.1.0 and the original
Ring Mask asset were unchanged; this was a floor UV correction.

The asset contains a non-asset nested copy of the unchanged Ring Mask graph,
so its file adds only **Arc Mask** to the asset menu. The published Ring Mask
file stays unchanged. Blender may give that local dependency a suffix when a
different Ring Mask datablock is already appended into the current file; that
does not change the original group or duplicate the shader per arc instance.

## Build and verify

From the repository root, use a fresh output directory and run each process
serially. The verifier renders constant-UV shader samples, compares the nested
radial graph to the published Ring Mask, checks independent instances, then
saves and reopens the fixture. It does not inspect a live Add menu.

```sh
blender --background --factory-startup --disable-autoexec --threads 2 --python-exit-code 1 --python tools/randy_node_assets/build_arc_mask.py -- --output node_library/_build/Randy_Arc_Mask.blend
blender --background --factory-startup --disable-autoexec --threads 2 --python-exit-code 1 --python tools/randy_node_assets/verify_arc_mask.py -- --asset node_library/_build/Randy_Arc_Mask.blend --report node_library/_build/arc-mask-verification.json
```

The current verifier checks **38 actual shader samples**, the original radial
graph and old Mask socket compatibility, and native save/reopen. Evidence is in
[arc_mask.json](../node_library/validation/arc_mask.json). Its local asset
deployment is verified. The running Blender Add menu was not refreshed or
checked in this release; refresh the **Nodes** Asset Browser library in an
existing session. Builder6 materials were not edited.
