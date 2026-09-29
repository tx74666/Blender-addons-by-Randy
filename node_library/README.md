# Randy Node Library

Reusable native Blender node groups, their build scripts, and their change history live in this repository. This directory contains three original assets; third-party libraries such as Higgsas and Node Tools are not bundled.

**Library version: 0.1.1.** Built and checked with Blender 5.2. Other Blender versions have not been verified.

| Asset | Editor | Asset catalog | Version | Purpose |
| --- | --- | --- | --- | --- |
| Ring Mask | Shader Editor | `Textures` | 0.1.1 | A normalized UV mask for adjustable concentric rings. English interface. |
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
5. In the Shader Editor, use **Shift+A > Textures > Ring Mask**, or search for **Ring Mask**. You can also drag the asset from the Asset Browser into the Shader Editor.

Geometry assets belong in the Geometry Node Editor. Blender filters node assets by editor type.

If these same assets are already installed in an existing **Nodes** library, keep using that library and refresh it. Do not register another library containing the same assets unless you deliberately want duplicate search results. This repository setup does not change an existing local library or any open scene.

For an existing library, use the deployment scripts below rather than replacing its entire catalog file. A catalog can also describe unrelated assets, which must be preserved. Existing node groups already appended into a scene remain local copies; updating a library does not automatically replace them.

## Ring Mask

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

## Verification and its limits

The [validation](validation) directory records the evidence and its scope:

- Ring Mask's original numerical implementation passed 59 actual shader samples plus 4 structure, instance, persistence, and source-preservation checks: 63 checks in total.
- The 0.1.1 English naming and `Textures` catalog update passed 5 metadata and graph-equivalence checks. Those checks establish that its computation matches the previously tested graph; they are not a new render run.
- The two geometry assets passed 9 evaluated-geometry and persistence checks.

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

To install a verified Ring Mask build into an existing asset library, substitute your paths for the placeholders below. The destination must already have a valid `blender_assets.cats.txt`. Keep backups and deployment reports outside the asset library so Blender does not discover duplicate assets.

```sh
python tools/randy_node_assets/deploy_ring_mask.py --asset node_library/_build/Randy_Ring_Mask.blend --verification node_library/_build/ring-mask-verification.json --library "<existing asset library>" --backups "<backup directory>" --report "<deployment report.json>"
python tools/randy_node_assets/deploy_ring_mask.py --asset node_library/_build/Randy_Ring_Mask.blend --verification node_library/_build/ring-mask-verification.json --library "<existing asset library>" --backups "<backup directory>" --check
```

For the geometry pair, use `deploy_assets.py` with the `Randy_Toolkit.blend` build and `geometry-verification.json` report. Both deployment tools check the verified source, preserve unrelated catalog entries, back up replaced files, and support a final read-only `--check`.

## Keep every change visible

For each new node or update:

1. Edit or add its generator in `tools/randy_node_assets`; keep native node construction readable for code review.
2. Update its version and validation for changed behavior, then produce the corresponding `.blend` asset.
3. Update the asset entry and checksums in `manifest.json`, the appropriate catalog entry, this usage guide, and `CHANGELOG.md`. Save shareable verification evidence and an updated preview when needed.
4. Run the relevant Blender checks and the lightweight library check. Preserve existing scenes, asset identifiers, and unrelated catalog entries.
5. Leave the changes in this local repository for the owner to inspect, **Commit**, and **Push** using GitHub Desktop. Do not commit or push automatically.

After that manual push, GitHub shows the asset inventory, readable source changes, version history, and validation evidence together. A local edit remains local until the owner pushes it. Automated review or monitoring is a separate opt-in setup; this library does not enable it by itself.
