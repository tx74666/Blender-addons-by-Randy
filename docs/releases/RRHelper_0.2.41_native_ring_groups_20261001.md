# RR Helper 0.2.41: native Ring Groups

The old workflow made each added ring behave like another shader layer and
relied on hidden connection-state inputs in Mix Shaders. This update separates
shape masks from material layers: **many Ring / Arc masks share one Shader
inside Ring Group**, then one material layer is mixed over the original Base.

The node-library update is **0.2.0**: Ring Mask 0.1.1 remains unchanged, Arc
Mask and Ring Group are 0.1.0, and Mix Shaders is 0.2.0. All four shader assets
belong in **Shift+A > Textures**. There is no new Rings panel or root-level
Mix Shaders entry.

## Resulting workflow

Add Ring Group, connect the chosen BSDF or imported material Shader once, and
feed its Mask inputs from independently controlled Ring Mask or Arc Mask
nodes. Connect the group's Mask / Shader outputs to a Mix Shaders slot; the
original material enters Base Shader. Slot 1 covers slot 2, and Base stays at
the bottom. Make another Ring Group when another material is needed.

Ring Group unions masks with clamped **Maximum**. Three half-strength masks
overlapping at one point still produce half-strength coverage, avoiding added
mask values or repeated mixing of the same shader. Arc Mask adds degree-based
Start Angle and Sweep Angle around the same normalized UV center. Sweep 180
is a semicircle, 360 a full ring and 0 an empty mask; seam wrapping is supported.

RR Helper offers optional **Add Ring / Add Arc / Add Mask Slot** on the
active Ring Group and **Add Shader Slot** on the active Mix Shaders node.
These actions expand only the selected instance and preserve old links,
defaults, templates and other instances. Add Ring / Add Arc keeps the group
active for repeated right-click additions. The helper spaces only the newly
created ring when known constant controls leave room. Full radial space or
linked / animated controls leaves its values available for manual editing and
reports a warning; it does not move existing rings.

The same **Add Shader Slot** action also converts an ordinary Mix Shader to
the expandable mixer. Factor, both shader inputs and output connections map
to the new native group without changing the initial result; its new second
pair starts at Mask 0. Node presentation is retained and conversion failure
restores the original cables. Expansion preserves unused local asset templates,
linked sources, overrides and fake-user groups.

The workflow is also native without the add-on: chain Ring Group Mask outputs
into additional Ring Group Mask inputs and connect the shared Shader only to
the final group, or use Math Maximum. Add ordinary Mix Shader nodes or another
Mix Shaders node for more material layers. No fixed ring-count setting is needed.

Mix Shaders 0.2.0 contains no `_Connected` sockets or runtime connection-state
gates. Keep unused masks at 0. An unconnected Shader with a nonzero mask follows
Blender's normal black-input behavior. Existing v1 Mix Shaders nodes retain
their legacy gate support, and old Ring Stack data remains readable. Updating
the library does not replace already appended groups or rewrite user materials.

## Validation status

**Passed isolated validation, packaging and local deployment.** Heavy Blender
checks ran in serial factory background sessions without using an unsaved
working scene.

| Saved asset | Checks passed | Real shader samples | Evidence |
| --- | --- | --- | --- |
| Mix Shaders 0.2.0 | 14 | 9 | [mix_shaders.json](../../node_library/validation/mix_shaders.json) |
| Arc Mask 0.1.0 | 43 | 38 | [arc_mask.json](../../node_library/validation/arc_mask.json) |
| Ring Group 0.1.0 | 22 | 17 | [ring_group.json](../../node_library/validation/ring_group.json) |

Mix Shaders validation includes independent expansion to 25 shader pairs,
native save/reopen and further connection edits with RR Helper unregistered.
Arc Mask covers unchanged radial graph comparison, independent shared
dependencies, zero/full/negative sweeps, seam wrapping and radial softness.
Ring Group uses three real Ring / Arc masks with one Shader, soft Maximum
overlap, clamping, seam/zero/full arcs and expansion to 24 mask inputs. The saved
groups render natively with RR Helper unregistered.

Runtime checks passed **37 mixer tests plus 2 conversion render cases and 9
shader samples**, **34 Ring Group tests plus 7 shader samples**, and **170
registration lifecycle checks**. Ordinary-Python checks passed **6 publication
rollback tests**, **2 Ring / Arc asset deployment tests**, and **3 mixer
deployment tests**. Library consistency passed for **6 assets / 5 bundles**.
Detailed run logs and recovery evidence are retained under
`D:\Blender\Projects\Build\Recovery\NativeRingGroups_20261001\validation1`
and `validation2`.

Publication failures now roll back only files whose bytes and staged identities
still belong to the update. Concurrent edits, including same-byte replacement
files, are preserved; backups remain available for manual recovery.

## Local package and installation

[rr_helper-0.2.41.zip](../../dist/rr_helper-0.2.41.zip) contains **19 files**.
Its SHA-256 is:

```text
7620CD0D51FDDB6AD7389D7C5A393806FFCC2BE1552728574FBDC5851FC366FA
```

All three RR Helper deployment copies match the canonical package. Mix
Shaders, Arc Mask and Ring Group are installed in
`D:\Blender\Helper\Asset-Libraries\Costom\Nodes`; all three asset deployment
`--check` commands passed. Original Ring Mask's binary is unchanged:

```text
8740134297372a5cad2f16f330db5e20d2324922ab5ad4d04920aee7ffdd9cc5
```

## Live verification limit

**The running Blender Add menu remains unverified for this update.** No
desktop inputs or Builder6 material edits were performed during this release.
The background evidence records `ui_search_tested: false`; it does not establish
that a running window has refreshed its asset menu or loaded new shortcut code.

In an existing Blender session, use **F3 > Refresh Add-on**, then the **Nodes**
Asset Browser's **Library > Refresh**. Library updates leave already appended
groups in the working file untouched, so add the new asset when choosing the
new behavior.

These are local repository and installation changes. No Git commit, push or
remote release publication was performed; the owner handles GitHub Desktop
Commit and Push.
