# Mix Shaders

**Mix Shaders** is a personal node-library asset beside **Ring Mask** in the
existing **Textures** catalog. In the Shader Editor use **Shift+A > Textures >
Mix Shaders**, or search for **Mix Shaders**. Place the node without changing
the existing material links. It is an ordinary Shader Node Group, with Blender's
Shader color tag; it can be inspected with Tab. There is no separate root-level
Mix Shaders entry in the Add menu.

The node starts with these sockets:

```text
Base Shader
Mask 1       Shader 1
                     -> Shader
```

Connect the original shader to **Base Shader**, a **Ring Mask** output to
**Mask 1**, and an imported material shader or BSDF to **Shader 1**. Connect the
output yourself, wherever the material needs it. Ring Mask's existing Radius,
Width and Softness controls stay on its own node.

With **RR Helper 0.2.40 or later** enabled, select the Mix Shaders node and use
**right-click > Add Shader Slot**, or search
**F3 > Add Shader Slot**, to add another Mask / Shader pair. Existing values and
links remain attached. Add as many ordinary Ring Mask nodes as needed and connect
them to the corresponding Mask sockets. Mask shapes are independent of this
mixer, so an arc mask can use the same inputs in a later workflow.

**Earlier slots cover later slots.** Shader 1 is the highest priority; Shader 2
is beneath it; Base Shader is always at the bottom. A Mask of 0 passes through
the lower shaders, 1 uses its own shader, and intermediate values blend them.
Linked masks are clamped to 0–1. A slot with no Shader link passes through the
lower shaders even if its Mask is white. A connected black shader still counts
as a shader and can cover the Base normally.

Native asset insertion can reuse the template group. Add Shader Slot expands
only the selected instance into an independent group, leaving the asset template
and other instances unchanged. Material
nodes, shared images, Ring Mask, and other existing node groups are not copied
or replaced. Expanding uses stable interface identifiers so external links and
old Mask values survive. Undo and Redo restore the graph normally. Edited or
animated mixer internals are preserved and expansion reports a concise error;
add a fresh mixer if a custom group should remain customized.

Empty Shader detection is saved as hidden per-instance socket values. RR Helper
updates those values when that owner shader tree changes, and before saving or
rendering. It does not poll all materials continuously. Saved graphs remain
ordinary native shaders and render without the add-on. Keep RR Helper enabled
while changing Shader connections so empty-input detection stays current.

The former Ring Stack panel is no longer the primary workflow. Existing Ring
Stack graphs and saved material layer data are retained for compatibility. Mix
Shaders does not automatically convert, delete or connect them.

Install [the published node library](../node_library/README.md) in the existing
library already used for Ring Mask, then refresh that Asset Library. Keep the
asset and `blender_assets.cats.txt` together. The Mix Shaders build uses RR
Helper's canonical generator; no second shader implementation is maintained.
Ring Mask's asset and its existing Textures catalog UUID are unchanged.
