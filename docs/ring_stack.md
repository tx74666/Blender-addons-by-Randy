# Ring Stack

This is the legacy workflow from RR Helper 0.2.37. Starting with 0.2.39, its
standalone Rings toggle and Ring Stack panel are hidden. Use
[Mix Shaders](shader_mixer.md) from the Shader Editor's Shift+A menu and connect
ordinary Ring Mask nodes instead. Existing Ring Stack graphs and saved layer
data remain supported; the older workflow below describes those saved files.

RR Helper > Texture > Sections > Rings manages ordered material layers for a
round floor. Expand **Ring Stack** to edit the current material. A pinned Shader
Editor remains the target, including when viewing one of its nested groups.
Without a Shader Editor, the active object's material is used.

The existing **Ring Mask** must already be loaded in the current file. Its
implementation and asset are reused unchanged. Ring Stack is a per-material
manager, not another library asset or a replacement Ring Mask.

## Workflow

1. Keep the original shader connected to the active Material Output's Surface.
2. Click **Add Ring** and choose a current-file **Material** for that layer.
3. Set **Radius**, **Width** and **Softness**. Rename the layer in the list.
4. Duplicate or move layers with the controls beside Add Ring. The top layer
   covers the layers below it; the original Base Shader is always at the bottom.
5. Uncheck a layer to hide it. A layer with no material passes the Base through.
6. Delete individual layers as needed. Deleting the final layer restores the
   original Base connection.

The Shader Editor gets a single managed **Ring Stack** group with **Base Shader**
input and **Shader** output. The original Base graph stays in place. Layers mix
from the bottom of the list upward. Width zero hides a ring, and soft edges blend
with the layers underneath. Volume and Displacement connections are not managed.

## Ring and Arc

**Ring** uses the entire circle. **Arc** adds **Start Angle** and **Sweep Angle**:

- Angles are degrees in the active render UV map, not world-space rotation.
- Zero points right along +U; positive angles turn counterclockwise toward +V.
- Sweep 0 hides the arc. Sweep 360 gives the complete circle.
- An arc can cross zero degrees without splitting it into multiple layers.
- Softness controls the inner and outer radial edges; arc ends stay hard in this
  first version.

Radius means the **inner radius**, matching Ring Mask's existing Inner Radius.
The UV center is `(0.5, 0.5)`, the circular rim is radius 1, and Width extends
outward. These values are normalized UV distances, not meters. Arc uses the same
active render UV map and center as the existing Ring Mask.

For a floor with several concentric bands, use wider non-emissive material layers
for the paving and narrow emission-material layers above them for light strips.
Arc can leave a gap at an entrance. The manager does not generate floor geometry
or automatically reproduce the procedural material in Unity; the existing baking
and export workflow still applies.

## Data and safety

Layer names, stable IDs, order, material references and parameters belong to the
Material and persist in the `.blend`. Parameter changes rebuild the managed
implementation safely. Ring Mask and existing nested node groups/images stay
shared references. The existing Material-to-Shader extraction is reused for layer
materials; a previously generated Material Shader Group is reused rather than
silently refreshed. Use Material-to-Shader's **Refresh** when intentionally updating
that shared implementation after editing its source.

Self-reference and indirect node-group recursion are rejected. If a managed
Surface connection is manually rewired, the manager reports the conflict rather
than replacing the new connection. Failed graph preparation leaves the previous
working graph in place. The error appears in the panel so it cannot look like a
successful visual update.

The first version has no Shader Ramp, automatic material indexing or nested Ring
Groups. Each layer has a stable ID and shape setting so future layer shapes and
grouping can be added without interpreting node positions as layer order.
