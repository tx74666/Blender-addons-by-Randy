# Randy Node Library changelog

This history covers original node assets maintained in this repository. Library versions group a set of assets; individual node versions are recorded in [manifest.json](manifest.json). Local changes reach GitHub only after the owner commits and pushes them in GitHub Desktop.

## 0.1.1 - 2026-09-30

- Organize three source-backed assets under `node_library`, with readable metadata, build scripts, validation evidence, and ready-to-use `.blend` files.
- Rename the shader asset to **Ring Mask**, use English names, descriptions, and parameter labels, and place it directly in the **Textures** catalog. Its menu path is **Shift+A > Textures > Ring Mask** after the library refreshes.
- Preserve the normalized UV computation, three controls, mask-only output, inward edge softness, zero-width behavior, and crop outside Radius 1.
- Record 5 checks for the English metadata update and unchanged computational graph, with the previous 63-check numerical and structural evidence retained. No new shader render is claimed for this metadata-only update.
- Include Randy Ring and Randy Circular Pattern at their existing 0.1.0 versions. Their geometry behavior, bilingual interfaces, and existing `Randy/Primitives` and `Randy/Patterns` catalogs remain unchanged.
- Establish a per-change manifest, changelog, validation, and manual GitHub Desktop commit/push workflow. Add a lightweight repository check that does not launch Blender.

## 0.1.0 - 2026-09-30

- Create a normalized UV Ring Mask shader group with Inner Radius, Ring Width, Edge Softness, and a single Mask output. The original asset used a bilingual name and the `Randy/Patterns` catalog.
- Validate 59 shader samples and 4 structural, instance, persistence, and source-preservation checks, including independent instances in one material.
- Create Randy Ring, a native Geometry Nodes torus, and Randy Circular Pattern, a native circular instancing group.
- Validate the geometry assets with 9 evaluated-geometry and persistence checks.
- Add separate build, verification, and guarded deployment scripts so updating assets does not require editing an open working scene.
