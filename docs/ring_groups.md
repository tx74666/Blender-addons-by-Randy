# Ring Groups

Use native nodes in the Shader Editor:

1. **Shift+A > Textures > Ring Group** creates one material layer.
2. Connect the chosen BSDF or imported material's **Shader** to its **Shader** input.
3. Add **Ring Mask** nodes and connect their **Mask** outputs to **Mask 1**, **Mask 2**, and further mask inputs. The current node defaults to **Sweep Angle 360** for a full ring; use a smaller span for an arc.
4. Connect the Ring Group's **Mask / Shader** outputs to one **Mix Shaders** material slot. Connect the original base shader to **Base Shader**. An ordinary Mix Shader also works: Mask to factor, base to the first Shader input and the group's Shader to the second Shader input.
5. Connect the mixer output to Material Output's Surface.

All masks in one Ring Group use the same shader. Make a second Ring Group for a
different material. **Mix Shaders 0.2.0** starts with two Mask / Shader pairs;
slot 1 covers slot 2 and Base Shader stays at the bottom. Its optional Add
Shader Slot shortcut adds more materials, while ordinary native Mix Shader
nodes or chained Mix Shaders nodes work without any add-on. This avoids one
full shader chain per ring.

Each Ring Mask keeps its own radius, width and softness. The multifunction node
also has Start Angle and Sweep Angle: 180 is a semicircle, 360 is a full ring,
and 0 hides it. See [Ring Mask](ring_mask.md) for its normalized UV coordinates.

The group combines masks with **Maximum**, clamped to 0–1. It does not add
overlapping masks and does not apply the shared shader repeatedly. Two
half-strength masks still give half-strength coverage where they overlap.

## Adding more rings

With RR Helper enabled, select the Ring Group and use its node context menu:

- **Add Ring** creates the current Ring Mask with Sweep Angle 360 and connects it to an unused mask input. If needed, it creates another input.
- **Add Arc** uses the same multifunction Ring Mask with a partial Sweep Angle.
- **Add Mask Slot** adds an input for a mask you have already made.
- **Remove Mask Slot** removes the last mask input, retaining at least one.

With **RR Helper 0.2.45**, the selected Ring Group also shows **+ / −** on the
right side of its title bar, including with Show Options disabled. Plus adds
one Mask input; minus removes the final one and its cable, leaving the upstream
mask node in place. Other input values, identifiers and connections are kept;
Undo restores the removed input. Edits affect this instance independently.
For five masks, press plus three times on a new two-input Ring Group, then
connect your five Ring Mask outputs. There is no fixed two-mask limit.

These are optional editing shortcuts. They change only the selected node
instance, preserve its existing shader and links, and do not create another
panel or a fixed ring-count setting. The Ring Group remains active after each
add, so right-click **Add Ring** or **Add Arc** can be repeated. The new mask
is selected and positioned beside its group for parameter editing.

When existing radial controls are finite, unlinked constants, the helper
spaces only the new ring into an available normalized-radius interval with
a small gap. Existing Ring / Arc parameters are preserved. If there is no
room for the new width, or a mask has linked, animated or nonconstant radial
controls, it keeps the new mask's parameters and reports a warning to adjust
its Radius or Width manually. Such an add can overlap an existing ring until
adjusted; it does not move or resize the existing rings to force space.

RR Helper registers no Ring Group update,
save or render handler. Saved groups render natively with the add-on disabled.
Expansion retains linked sources, overrides, fake-user groups and local asset
templates even if their final current-file node reference was replaced.

Without RR Helper, duplicate Ring Mask nodes as usual. To exceed the two
initial group sockets, connect the first Ring Group's **Mask** output to a
second Ring Group's **Mask 1**, then put another Ring / Arc Mask into that
second group's **Mask 2**. Continue that mask chain for additional rings.
The shared Shader only needs to connect once, to the **final Ring Group**;
earlier groups can operate as mask unions with their Shader inputs unused.
Connect the final group's Mask and Shader outputs to the material mixer.
Alternatively, combine masks with native Math nodes set to Maximum, or add a
float input inside your own group and extend its Maximum chain. There is no
fixed ring-count limit in this native workflow. The optional helper does not rebuild an
internally edited group; keep that custom group and add a fresh managed one if
you want its automatic socket shortcut again.

The Shader input/output is a native Blender Shader socket. A Material datablock
or Geometry socket is not interchangeable with it. Import the source material's
shader first when needed, and use the resulting BSDF or Shader output.

## Validation

The source verifier checks the exact saved asset against the canonical graph,
expands one of two shared instances to 24 mask inputs with old links intact,
and renders three independently controlled Ring/Arc masks feeding one shader.
Soft overlaps, zero masks, clipping, semicircles and wrapping are compared with
independent scalar expectations. Save/reopen and rendering run without RR
Helper registration or handlers; live Add-menu refresh is separate evidence.

```sh
blender --background --factory-startup --disable-autoexec --threads 2 --python-exit-code 1 --python tools/randy_node_assets/build_ring_group.py -- --output node_library/_build/Randy_Ring_Group.blend
blender --background --factory-startup --disable-autoexec --threads 2 --python-exit-code 1 --python tools/randy_node_assets/verify_ring_group.py -- --asset node_library/_build/Randy_Ring_Group.blend --arc-mask node_library/validation/fixtures/Randy_Arc_Mask_0_2_0.blend --report node_library/_build/ring-group-verification.json
```

The saved asset passed **24 compatibility checks**, including **17 actual shader samples**,
and is published with evidence in
[ring_group.json](../node_library/validation/ring_group.json). Runtime editing
passed 34 checks plus 7 rendered samples. Local deployment is verified; this
release did not edit Builder6 materials or check its live Add menu. In an
existing session, use **F3 > Refresh Add-on** and the **Nodes** Asset Browser's
**Library > Refresh** before using the new assets and shortcuts.

The historical Arc Mask shader fixture remains outside the visible asset
directory for those Ring Group tests. New materials should use multifunction
Ring Mask 0.2.1 from Textures. An older linked Ring/Arc Mask can remain in the
current file even after an asset-library refresh; migration is explicit, after
a backup, and does not purge unrelated groups or alter linked source files.
