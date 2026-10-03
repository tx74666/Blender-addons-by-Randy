# Mix Shaders

**Mix Shaders 0.2.0** is a native Shader Node Group in **Shift+A > Textures**,
beside Ring Mask, Arc Mask, and Ring Group. It has Blender's **Shader** color
tag. Add it without changing existing material links, then connect its output
where needed. There is no separate root-level Add-menu entry or Rings panel.

The node starts with these sockets:

```text
Base Shader
Mask 1       Shader 1
Mask 2       Shader 2
                     -> Shader
```

Connect the original material shader to **Base Shader**. For each material
layer, connect a **Ring Group**'s Mask and Shader outputs to the corresponding
**Mask / Shader** pair. A single Ring Mask or Arc Mask can also drive the Mask
directly. Connect the mixer output to Material Output's **Surface** yourself.

**Shader 1 covers Shader 2; Base Shader stays at the bottom.** Mask 0 passes
through the lower shaders, Mask 1 uses its layer, and values between 0 and 1
blend them. Linked mask values are clamped to 0–1. Keep unused masks at **0**.
As with Blender's ordinary Mix Shader, an unconnected Shader input contributes
black if its mask is raised. A connected black shader also covers normally.

This version has no hidden `_Connected` inputs, Python-driven connection-state
gates, or runtime helper requirement. Shader connections can be edited and
rendered with RR Helper disabled. Each group is a saved native shader graph.

## More rings sharing one shader

Keep the shapes separate from material layers. Give each Ring Mask or Arc Mask
its own radius, width, softness, and, for an arc, start and sweep angles.
Connect their masks to one **Ring Group**, and connect their shared material
shader only once. Ring Group unions the masks with **Maximum**. Overlapping
half-strength masks stay half strength; the shader is not mixed repeatedly.

For an entirely native workflow, connect an earlier Ring Group's **Mask**
output to a later Ring Group's **Mask** input, then connect another Ring / Arc
mask to the remaining input. Repeat that mask chain as needed. The shared
Shader only needs to be connected to the **final Ring Group**. Native Math
nodes set to Maximum offer the same union. See [Ring Groups](ring_groups.md).

## More material layers

RR Helper **0.2.41 or later** offers the optional **right-click > Add Shader
Slot** or **F3 > Add Shader Slot** shortcut on the active Mix Shaders node.
Each action adds a Mask / Shader pair, preserving old values and connections.
Expansion affects only that instance and leaves shared templates and other
instances intact. There is no fixed layer-count setting.

With **RR Helper 0.2.44**, select the expanded Mix Shaders node to show small
**+ / −** controls at the right of its header. **+** adds one new **Mask / Shader**
pair (two input sockets). **−** removes the final pair and that pair's incoming
cables, keeping its upstream material and mask nodes. At least one pair and
Base Shader remain. **Ctrl+Z** restores the edit. **Remove Shader Slot** is also
available from the node context menu or F3.

The header buttons remain available when **Show Options** is disabled. That
setting only hides Blender's ordinary node settings; it does not collapse the
node or disable slot editing. Version 0.2.43 incorrectly hid these controls
together with the node settings.

The header controls follow the selected node, including nodes inside Frames,
and disappear at very small zoom levels or on collapsed/read-only nodes. They
are optional editor controls supplied by RR Helper; the saved node remains a
normal ShaderNodeGroup and renders without the add-on. Other clicks pass through
to Blender's normal node interaction.

On an ordinary **Mix Shader**, the same **Add Shader Slot** action converts it
to the expandable mixer. The first shader becomes Base Shader, Factor becomes
Mask 1, and the second shader becomes Shader 1. Its incoming and outgoing
cables, Factor value and node presentation are preserved. A second Mask /
Shader pair is added with Mask 0, so conversion initially preserves the
material result. Animated shader trees are preserved and report why automatic
conversion cannot safely proceed.

Without RR Helper, add another ordinary Mix Shader, or another Mix Shaders
node. Connect the previous result to the next mixer's **Base Shader** and add
the new material above it. Inside each Mix Shaders node, earlier slots cover
later slots. Original base material nodes and unrelated links remain yours to
control.

Optional expansion uses stable interface identifiers. Undo and Redo restore
the graph normally. Cosmetic node arrangement does not change its calculation.
If the group calculation or animation was customized, the helper preserves it
and reports why it cannot rebuild; use a fresh managed node when appropriate.
Linked groups, library overrides, fake-user groups and even unused local
asset templates are retained when the last node reference is expanded. Only
an unused editable local non-asset helper is eligible for removal.

## Existing files

Previously appended **Mix Shaders 0.1.0** groups are retained. Those legacy v1
groups use hidden `_Connected` values to bypass empty Shader inputs; RR Helper
still updates that state for the existing legacy workflow. They are not
silently replaced with the native v2 behavior. Add a fresh Mix Shaders 0.2.0
asset when choosing the native workflow, and reconnect deliberately.

The old Ring Stack's saved graphs and material data also remain readable for
compatibility. Adding a new node does not convert, delete or connect them.

Install [the node library](../node_library/README.md) in the same asset library
already used for Ring Mask and refresh it. Updating the library does not
replace groups already appended into a working file. Ring Mask's original
computation, binary, and Textures catalog UUID are unchanged.

RR Helper 0.2.43 and the library assets are deployed locally. The unchanged
saved mixer asset retains its original 14 checks, including 9 rendered samples;
current helper provenance is recorded separately. The helper passed 47 core,
11 UI and 276 lifecycle checks. Actual GUI plus/minus, Undo and Redo preserved
the original six fixture cables and parameters inside a Frame. Four metal A/B
fixtures and the saved Hub_Base material on a test UV plane matched native Mix
Shader results exactly in Cycles; the original scene's lighting/geometry was not
reproduced. These checks do not establish EEVEE Material Preview equivalence.
This update did not edit Builder6 material graphs. Use **F3 > Refresh Add-on**
and the **Nodes** Asset Browser's **Library > Refresh** in an existing session.

An [EEVEE follow-up](releases/Shader_Mixer_EEVEE_Gold_Edges_20261002.md) found
reflection differences with ray tracing enabled between the saved managed
mixer and the original native chain. Reconnecting two separate Gold node
instances did not remove the difference in the actual saved group. Extra
factor Clamp and group encapsulation alone produced no measured difference in
the controlled diagnostic chain. The screenshot's precise dark-edge pattern
has not been reproduced with its original preview settings and geometry.
Keep the original native chain when preserving that preview result is required;
do not compensate by changing Gold brightness or roughness. No working
Builder6 material or node asset was changed during this diagnostic.
