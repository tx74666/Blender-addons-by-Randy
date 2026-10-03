# Randy Node Library

Reusable native Blender node groups, their build scripts, and their change history live in this repository. The 0.3.1 library exposes six original assets; third-party libraries such as Higgsas and Node Tools are not bundled.

**Library version: 0.3.1.** Multifunction **Ring Mask 0.2.1** is the visible node for full rings and partial arcs. It defaults to Sweep Angle 360 and supplies Extend Mask through Ring Data. The former Arc Mask 0.2.0 and original three-input radial Ring Mask remain historical fixtures outside the visible asset directory.

| Asset | Editor | Asset catalog | Version | Purpose |
| --- | --- | --- | --- | --- |
| Mix Shaders | Shader Editor | `Textures` | 0.2.0 | Native Mask / Shader slots over a Base Shader; no runtime connection-state helper. |
| Ring Mask | Shader Editor | `Textures` | 0.2.1 | Full rings or adjustable arcs, with reusable Ring Data. Default sweep 360 and the normal brown Texture color tag. |
| Extend Mask | Shader Editor | `Textures` | 0.1.0 | New inner/outer bands or complete contour outlines; radial branches can be extended again. |
| Ring Group | Shader Editor | `Textures` | 0.1.0 | Combine many Ring / Arc masks with Maximum and one shared Shader. |
| Randy Ring | Geometry Nodes | `Randy/Primitives` | 0.1.0 | A parametric torus with radius, tube radius, resolution, shading, and material controls. Legacy bilingual interface preserved. |
| Randy Circular Pattern | Geometry Nodes | `Randy/Patterns` | 0.1.0 | Instances input geometry around a circle, with count, radius, orientation, scale, and optional realization. Legacy bilingual interface preserved. |

The two geometry assets retain their existing bilingual Blender names and sockets. Their English names above are reading labels, not a migration of existing assets. Other personal assets already present in a local library are outside this initial, source-backed collection.

See [manifest.json](manifest.json) for exact asset identifiers, files, source references, and checksums, and [CHANGELOG.md](CHANGELOG.md) for changes. Source code is in [tools/randy_node_assets](../tools/randy_node_assets).

## Use in Blender

The ready-to-use files are in [assets](assets). They are asset-library files, not an add-on ZIP.

1. Clone this repository, or use GitHub's **Code > Download ZIP** and extract it after the owner has committed and pushed the files.
2. Keep the `assets` directory intact: both `.blend` files and `blender_assets.cats.txt` belong together.
3. In Blender, open **Edit > Preferences > File Paths > Asset Libraries**, add the repository's `node_library/assets` directory, and name the library **Randy Nodes**.
4. In an Asset Browser, choose that library and use **Library > Refresh** after an update.
5. In the Shader Editor, use **Shift+A > Textures > Ring Mask**, or search for **Ring Mask**. The current node defaults to a full ring; reduce Sweep Angle for an arc.
6. Add **Ring Mask**, **Extend Mask**, **Ring Group**, and **Mix Shaders** from that same **Textures** catalog. There is no separate Rings panel or root-level Mix Shaders entry.

The existing local Windows library is
`D:\Blender\Helper\Asset-Libraries\Costom\Nodes`. Keep using this library and
refresh its Asset Browser with **Library > Refresh**. Select Ring Group to show
its optional header **+ / −** controls; refresh RR Helper with **F3 > Refresh
Add-on** when its editing helpers have also been updated. Current-file migration
is a separate, explicit operation after a backup. Installing a library does not
establish that live Builder6 nodes or menus have been upgraded.

Geometry assets belong in the Geometry Node Editor. Blender filters node assets by editor type.

If these same assets are already installed in an existing **Nodes** library, keep using that library and refresh it. Do not register another library containing the same assets unless you deliberately want duplicate search results. This repository setup does not change an existing local library or any open scene.

For an existing library, use the deployment scripts below rather than replacing its entire catalog file. A catalog can also describe unrelated assets, which must be preserved. Existing node groups already appended into a scene remain local copies; updating a library does not automatically replace them.

## Original radial implementation (historical three-input Ring Mask)

The original three-input standalone Ring Mask is retired. Its unchanged 0.1.1
binary is retained in `dependencies/`, outside `assets/`, as a reproducible
build/test fixture. Use the current five-input Ring Mask 0.2.1 for new materials;
the following radial contract still describes its embedded computation. The
matching public name does not make an old scene or linked group current.

![Ring Mask examples: a thin ring, a soft ring, and two independent rings](previews/ring_mask.png)

The node outputs only a scalar **Mask** in the range 0 to 1. Use it as a factor when mixing colors, shaders, emission, or other material properties. Those material choices remain outside the group.

The active render UV map must place the circular surface inside UV 0 to 1, with its center at `(0.5, 0.5)` and its circular rim touching the four sides of that square:

```text
CenteredUV = (UV.xy - (0.5, 0.5)) * 2
Radius = length(CenteredUV)
OuterRadius = InnerRadius + RingWidth
```

The center has Radius 0 and the circular rim has Radius 1. These are normalized UV distances, not meters. The group reads UV internally; it does not use object or world position, and it ignores Z.

| Input | Default | Behavior |
| --- | --- | --- |
| Inner Radius | 0.6 | The inner boundary, clamped to 0 to 1. |
| Ring Width | 0.08 | Width extending outward from the inner boundary. Negative values become 0; width 0 produces a completely black mask. |
| Edge Softness | 0 | An inward smooth transition at each ring boundary. Negative values become 0; softness is capped at half the ring width. |

With zero softness and positive width, `InnerRadius <= Radius <= OuterRadius` produces white. With positive softness, the mask fades from black at each boundary toward white inside the ring. Setting Inner Radius to 0 produces a disk without an artificial fade or hole at its center.

All samples beyond Radius 1 are black. If Inner Radius + Ring Width exceeds 1, the ring is cropped at the circular rim; its calculated outer boundary is not moved back to 1. The rim crop is hard, independently of Edge Softness. The parameter fields show a 0 to 1 range, while linked width values above 1 are supported by the internal calculation.

Add multiple group instances to make independently adjustable concentric rings. The node modifies no mesh and does not replace an existing material. Exporting a model does not automatically recreate this procedural shader in another application; bake the result or implement equivalent shader logic there.

## Mix Shaders

Use **Shift+A > Textures > Mix Shaders** beside Ring Mask. It is a normal Shader
Node Group with the **Shader** color tag, one **Shader** output, and these visible
inputs: **Base Shader**, **Mask 1 / Shader 1**, **Mask 2 / Shader 2**. Connect an ordinary Ring Mask to
Mask 1 and any BSDF or imported material shader to Shader 1. The node does not
automatically connect to Material Output.

With **RR Helper 0.2.41 or later** enabled, select this node and use **right-click
> Add Shader Slot**, or **F3 > Add Shader Slot**, to add another Mask / Shader
pair. Existing links and values stay attached; expanding one instance leaves
other instances unchanged. Connect multiple Ring Mask nodes independently. Earlier
slots cover later slots, with Base Shader at the bottom.

**RR Helper 0.2.44** also draws **+ / −** on the selected mixer header. Each plus
adds two input sockets, one Mask and one Shader. Minus removes the final pair
and its input cables while preserving the upstream nodes; Undo restores the
edit. One pair and Base Shader always remain. These are optional editor controls,
not new saved node types; the 0.2.0 asset graph and binary are unchanged.
The buttons remain visible with **Show Options** disabled; hiding the normal
node settings does not disable input-slot editing.

The 0.2.0 asset has no hidden `_Connected` inputs or Python-driven connection
state. Editing and rendering its shader connections works natively without
RR Helper. Keep unused Mask inputs at **0**. As with Blender's ordinary Mix
Shader, a white Mask with an unconnected Shader socket mixes in black. The
optional helper only saves editing steps when expanding a node; ordinary Mix
Shader nodes can extend the material chain without it. Previously appended
0.1.0 mixers keep their legacy connection-state behavior and are not silently
replaced. See [the full workflow](../docs/shader_mixer.md).

The same **Add Shader Slot** context action works on an ordinary **Mix
Shader** node: it converts that node into the expandable native mixer while
preserving the Factor, both shader cables, output cables and presentation.
Its first shader becomes Base Shader, Factor becomes Mask 1 and the second
shader becomes Shader 1; the new second pair starts unused at Mask 0.
Expansion preserves even an unused local asset template instead of removing
it after the final current-file node reference is replaced.

## Ring Mask

Multifunction Ring Mask 0.2.1 exposes the unchanged **Inner Radius**, **Ring Width**, and
**Edge Softness**, plus **Start Angle** and **Sweep Angle** in degrees. It reads
the same normalized UV map. Sweep 180 makes a semicircle, 360 a full ring, and
0 an empty mask; arcs can cross the 0/360 seam. The existing Ring Mask radial
graph is one shared native dependency, with only the angular gate added.
Angular ends are hard; Edge Softness controls the radial edges.

Map the circular surface once, without overlapping mirrored UV halves. Such a
fold can leave full rings looking correct while repeating an arc; see the
[UV troubleshooting notes](../docs/arc_mask.md#if-one-arc-appears-twice).

Ring Mask retains Arc Mask 0.2.0's **Ring Data**, native Mask calculation and
socket identifiers. The public name is now Ring Mask and Sweep Angle defaults
to 360. Connect Ring Data to Extend Mask's Source to reuse the boundaries and
angles. Its native Texture color tag uses Blender's normal brown header.

Add it through **Shift+A > Textures > Ring Mask** and connect its scalar **Mask**
to a material mixer or Ring Group. It needs no add-on. See
[Ring Mask](../docs/ring_mask.md) for controls, old search entries and migration.
The historical [Arc Mask guide](../docs/arc_mask.md) remains intact.

An old **Group > Linked > Ring Mask** entry can refer to a previously loaded
linked node group. Library refresh does not replace such current-file groups.
Explicit migration preserves validated old parameters and links; customized,
animated or read-only owners are refused. Only superseded groups with no actual
users can be removed afterward. No global purge or source-library edit runs.

## Extend Mask

Connect **Ring Mask > Ring Data** to **Extend Mask > Source**. One native menu
offers **Inner**, **Outer**, **Both**, and **Outline**; Width, Gap and Softness
control only the new bands. Radial modes retain the original arc angles, while
Outline surrounds the whole contour, including an arc's two cut ends. Both is
the default, and full rings use Ring Mask's default Sweep Angle 360.

The original source is excluded from the new Mask. Continue radial bands by
connecting Inner Data or Outer Data to another Extend's Source; each side can
have its own next node. Combine the resulting masks in Ring Group for one shared
shader, or use separate Mix Shaders slots for different shaders. Outline outputs
only a usable final Mask in this first version; its radial data outputs are inactive.

No boundary list, extra mode panel or add-on is required. Distances use Ring Mask's
normalized UV space. See [Extend Mask](../docs/extend_mask.md) for the workflow.

![Extend Mask: full-ring bands, arc radial bands, contour outline and chained bands](previews/extend_mask.png)

## Ring Group

Ring Group accepts one **Shader** and initially two scalar **Mask** inputs.
Connect any independently configured Ring Mask nodes, including full rings at
Sweep 360. Their masks
are combined with **Maximum**, clamped to 0–1; the Shader passes through once.
Overlapping half-strength masks stay half strength instead of adding together
or applying the same shader repeatedly.

Connect its **Mask** and **Shader** outputs to one Mix Shaders material slot.
Use another Ring Group for another material. To add more masks without an
add-on, connect a Ring Group's Mask output to another Ring Group's Mask input,
put the additional Ring / Arc masks into the remaining inputs, and connect the
Shader only to the final Ring Group. Native Math nodes set to Maximum work too.

RR Helper 0.2.41 offers optional node-context **Add Ring**, **Add Arc**, and
**Add Mask Slot** shortcuts. It keeps the Ring Group active for repeated adds,
creates another input when necessary, and spaces a new ring when constant
radial controls leave room. Existing parameters stay unchanged. The native
Ring Group requires no update, save or render handler. See
[Ring Groups](../docs/ring_groups.md) for the workflow and warnings.

RR Helper **0.2.45** adds **+ / −** on the selected Ring Group header to add or
remove one Mask input. Five masks need three plus clicks from a new group;
existing values and retained connections survive, and Undo restores removal.
The current **Add Ring** helper creates multifunction Ring Mask at Sweep 360;
**Add Arc** uses the same node with a partial span. Earlier helpers may still
create a historical Arc Mask until the add-on is refreshed.

## Verification and its limits

The [validation](validation) directory records the evidence and its scope:

- Ring Mask's original numerical implementation passed 59 actual shader samples plus 4 structure, instance, persistence, and source-preservation checks: 63 checks in total.
- The 0.1.1 English naming and `Textures` catalog update passed 5 metadata and graph-equivalence checks. Those checks establish that its computation matches the previously tested graph; they are not a new render run.
- The two geometry assets passed 9 evaluated-geometry and persistence checks.
- Mix Shaders 0.1.0 previously passed 13 checks, including nine rendered
  samples. That evidence describes its legacy connection-state implementation.
- Mix Shaders 0.2.0 passed **14 saved-asset checks**, including **9 real shader
  samples**, independent expansion to 25 Mask / Shader pairs and connection
  editing after native save/reopen with RR Helper unregistered. See
  [mix_shaders.json](validation/mix_shaders.json).
- Arc Mask 0.1.0 passed **43 checks**, including **38 real shader samples**, exact
  comparison with the original Ring Mask radial graph, independent shared
  dependencies and native save/reopen. See
  [the preserved baseline](validation/arc_mask_0_1_0_baseline.json).
  Arc Mask 0.1.1 passed **4 metadata and recursive graph-equivalence checks**,
  including native save/reopen, bound to that baseline. The computation is
  unchanged; those checks are not another render run. See
  [the archived 0.1.1 report](validation/arc_mask_0_1_1_baseline.json).
  Arc Mask **0.2.0** passed **44 checks**, including 38 fresh rendered samples,
  native save/reopen and exact compatibility of the old Mask graph and socket
  identifiers. Its preserved binary baseline is outside the public asset directory.
  Its original report is preserved as
  [arc_mask_0_2_0_baseline.json](validation/arc_mask_0_2_0_baseline.json), with
  `validation/fixtures/Randy_Arc_Mask_0_2_0.blend` outside the visible library.
- Current multifunction Ring Mask **0.2.1** passed **96 checks**: 46 real shader
  samples in Cycles, the same 46 in EEVEE, and four structure, persistence and
  source/asset-preservation checks. See
  [ring_mask_current.json](validation/ring_mask_current.json). These cover the
  unchanged native graph and socket identifiers against Arc 0.2.0, full-ring
  defaults, arcs, Ring Data/Extend connections and native save/reopen. Old radial
  evidence remains historical. The isolated migration fixture passed ten checks,
  including exact link preservation, customized-source refusal and rollback;
  it did not operate live Builder6 or verify its menus.
- Extend Mask passed **131 checks**, including **64 real shader samples in
  Cycles and 64 in EEVEE**, native Bundle/Menu save/reopen, missing-source and
  invalid-data guards, radial branch chaining and exact arc end-contour cases.
  See [extend_mask.json](validation/extend_mask.json).
- Ring Group passed **24 fresh compatibility checks**, including **17 real shader samples**, three
  overlapping soft masks sharing one shader, independent expansion to 24 mask
  inputs and native save/reopen/rendering with RR Helper unregistered. See
  [ring_group.json](validation/ring_group.json).
- The 0.3.0 lightweight library check passed for **6 assets / 5 bundles**. Extend
  publication and deployment passed 9 and 4 pure regression tests; Ring Group's
  original factory archive proof passed 6. Publication
  rollback passed 6 tests, Ring / Arc deployment 2 tests, and mixer deployment
  3 tests. Updated native assets passed their final read-only
  deployment checks; the original radial binary is preserved as an internal
  fixture outside the visible asset directory.

Runtime tests, release package checksum and the limits of live verification
are recorded in [the 0.2.41 release notes](../docs/releases/RRHelper_0.2.41_native_ring_groups_20261001.md).

The preview above comes from the earlier actual shader render. It illustrates the unchanged computation, not a new render of the 0.1.1 asset. Automated asset checks do not establish that a particular running Blender window has refreshed its menus.

Run the lightweight repository consistency check with Python 3.12 from the repository root:

```sh
python tools/randy_node_assets/verify_library.py
```

This validates the tracked library metadata and files without launching Blender or rendering. Source checksums use UTF-8 content with LF-normalized line endings so Windows and Linux checkouts agree; `.blend` checksums use the original binary bytes. The GitHub workflow uses the same lightweight check after the owner commits and pushes; its existence does not mean that a cloud run has already passed.

## Rebuild and deploy

Build from the readable scripts when changing a node. Run these commands from the repository root, replacing `blender` with your Blender executable if it is not on PATH. Choose a fresh output directory: build scripts refuse to overwrite an existing output.

```sh
blender --background --factory-startup --disable-autoexec --threads 2 --python-exit-code 1 --python tools/randy_node_assets/build_ring_mask.py -- --output node_library/_build/Randy_Ring_Mask.blend
blender --background --factory-startup --disable-autoexec --threads 2 --python-exit-code 1 --python tools/randy_node_assets/verify_ring_mask.py -- --asset node_library/_build/Randy_Ring_Mask.blend --report node_library/_build/ring-mask-verification.json

blender --background --factory-startup --disable-autoexec --threads 2 --python-exit-code 1 --python tools/randy_node_assets/build_assets.py -- --output node_library/_build/Randy_Toolkit.blend
blender --background --factory-startup --disable-autoexec --threads 2 --python-exit-code 1 --python tools/randy_node_assets/verify_assets.py -- --asset node_library/_build/Randy_Toolkit.blend --report node_library/_build/geometry-verification.json
```

The full Ring Mask verifier renders shader samples and example images. Run full validation when the computation changes; a documentation-only change does not need another render. Keep heavy Blender jobs serial when memory is limited. These commands use independent background sessions and do not open a working scene.

The Ring Mask commands above rebuild the internal historical radial fixture
only. Do not install that fixture in a visible asset library; use the current
`build_ring_mask_current.py` commands below for full rings and arcs. Keep all
backups and deployment reports outside the
asset library so Blender does not discover duplicate assets.

For the geometry pair, use `deploy_assets.py` with the `Randy_Toolkit.blend` build and `geometry-verification.json` report. Both deployment tools check the verified source, preserve unrelated catalog entries, back up replaced files, and support a final read-only `--check`.

Build, verify and publish Mix Shaders in serial factory sessions. Its generator
calls RR Helper's canonical `rr_shader_mixer` implementation. The manifest and
verification additionally bind that generator and the shared deployment/oracle
dependencies by source hash.

```sh
blender --background --factory-startup --disable-autoexec --threads 2 --python-exit-code 1 --python tools/randy_node_assets/build_mix_shaders.py -- --output node_library/_build/Randy_Mix_Shaders.blend
blender --background --factory-startup --disable-autoexec --threads 2 --python-exit-code 1 --python tools/randy_node_assets/verify_mix_shaders.py -- --asset node_library/_build/Randy_Mix_Shaders.blend --report node_library/_build/mix-shaders-verification.json
python tools/randy_node_assets/finalize_native_ring_nodes.py --kind mix_shaders --asset node_library/_build/Randy_Mix_Shaders.blend --verification node_library/_build/mix-shaders-verification.json --backups "<publication backup directory>"
python tools/randy_node_assets/verify_library.py
python tools/randy_node_assets/deploy_mix_shaders.py --asset node_library/assets/Randy_Mix_Shaders.blend --verification node_library/validation/mix_shaders.json --library "<existing asset library>" --backups "<backup directory>" --report "<deployment report.json>"
python tools/randy_node_assets/deploy_mix_shaders.py --asset node_library/assets/Randy_Mix_Shaders.blend --verification node_library/validation/mix_shaders.json --library "<existing asset library>" --backups "<backup directory>" --check
```

Build the current Ring Mask from the preserved Arc 0.2.0 fixture, validate its
full-ring default and Ring Data workflow, then publish and deploy that same
verified binary. The older Arc builder remains a historical source; it is not
the current visible asset's build or publication command.

```sh
blender --background --factory-startup --disable-autoexec --threads 2 --python-exit-code 1 --python tools/randy_node_assets/build_ring_mask_current.py -- --output node_library/_build/Randy_Ring_Mask.blend --source node_library/validation/fixtures/Randy_Arc_Mask_0_2_0.blend
blender --background --factory-startup --disable-autoexec --threads 2 --python-exit-code 1 --python tools/randy_node_assets/verify_ring_mask_current.py -- --asset node_library/_build/Randy_Ring_Mask.blend --source node_library/validation/fixtures/Randy_Arc_Mask_0_2_0.blend --extend-asset node_library/assets/Randy_Extend_Mask.blend --report node_library/_build/ring-mask-current-verification.json --eevee
python tools/randy_node_assets/finalize_ring_mask_current.py --asset node_library/_build/Randy_Ring_Mask.blend --verification node_library/_build/ring-mask-current-verification.json --backups "<publication backup directory>"
python tools/randy_node_assets/verify_library.py

python tools/randy_node_assets/deploy_ring_mask_current.py --asset node_library/assets/Randy_Ring_Mask.blend --verification node_library/validation/ring_mask_current.json --library "<existing asset library>" --backups "<backup directory>" --report "<ring-mask deployment report.json>"
python tools/randy_node_assets/deploy_ring_mask_current.py --asset node_library/assets/Randy_Ring_Mask.blend --verification node_library/validation/ring_mask_current.json --library "<existing asset library>" --backups "<backup directory>" --check
python tools/randy_node_assets/deploy_ring_nodes.py --kind ring_group --asset node_library/assets/Randy_Ring_Group.blend --verification node_library/validation/ring_group.json --library "<existing asset library>" --backups "<backup directory>" --report "<ring-group deployment report.json>"
python tools/randy_node_assets/deploy_ring_nodes.py --kind ring_group --asset node_library/assets/Randy_Ring_Group.blend --verification node_library/validation/ring_group.json --library "<existing asset library>" --backups "<backup directory>" --check
```

The current Ring Mask finalizer accepts only passed evidence bound to the exact
unchanged build, preserved Arc baseline, and source dependencies. It backs up
replaced publication files outside the asset directory before updating the
manifest, saved asset and evidence. The deployment wrappers reuse Ring Mask's
guarded catalog merge, backup and rollback implementation in isolated modules;
each changes only its selected asset and a missing Textures catalog entry.
The internal radial graph, geometry assets and unrelated library contents are
preserved. Retiring the former visible Arc file is a separate backed-up action;
deployment does not remove current-file node groups or migrate scene nodes.

## Keep every change visible

For each new node or update:

1. Edit or add its generator in `tools/randy_node_assets`; keep native node construction readable for code review.
2. Update its version and validation for changed behavior, then produce the corresponding `.blend` asset.
3. Update the asset entry and checksums in `manifest.json`, the appropriate catalog entry, this usage guide, and `CHANGELOG.md`. Save shareable verification evidence and an updated preview when needed.
4. Run the relevant Blender checks and the lightweight library check. Preserve existing scenes, asset identifiers, and unrelated catalog entries.
5. Leave the changes in this local repository for the owner to inspect, **Commit**, and **Push** using GitHub Desktop. Do not commit or push automatically.

After that manual push, GitHub shows the asset inventory, readable source changes, version history, and validation evidence together. A local edit remains local until the owner pushes it. Automated review or monitoring is a separate opt-in setup; this library does not enable it by itself.
