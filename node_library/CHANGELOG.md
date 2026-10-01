# Randy Node Library changelog

This history covers original node assets maintained in this repository. Library versions group a set of assets; individual node versions are recorded in [manifest.json](manifest.json). Local changes reach GitHub only after the owner commits and pushes them in GitHub Desktop.

## 0.1.2 - 2026-10-01

- Add **Mix Shaders 0.1.0** beside **Ring Mask** in the existing **Textures**
  catalog, retaining UUID `cc7f1f5b-84b2-4f62-93e3-d05ed982a2c2`. Its Shader Editor
  entry is **Shift+A > Textures > Mix Shaders**; no second root-level entry is
  required.
- Build the asset with RR Helper's canonical generator: Base Shader, Mask 1 /
  Shader 1, a hidden per-instance connection-state input, and one Shader output.
  The native Shader color tag gives Blender's normal shader-group appearance.
- RR Helper 0.2.40 supplies **Add Shader Slot** and automatic empty-Shader
  passthrough while editing. Saved native graphs remain renderable without the
  add-on. Expanding one instance preserves existing links and values and leaves
  other instances and the asset template unchanged.
- Add saved-asset verification for independent expansion, native save/reopen,
  and nine real shader samples with three existing Ring Mask nodes. Expected
  masks reuse Ring Mask's existing independent scalar oracle. Exact results
  and generator/dependency hashes belong in `validation/mix_shaders.json`.
  All 13 asset checks passed, including the nine rendered samples.
- Reuse Ring Mask's guarded deployment implementation, including catalog
  collision checks, backups outside the library, atomic replacement, external
  writer detection, rollback and read-only `--check`. Three ordinary-Python
  deployment tests verify that Ring Mask and unrelated files survive.
- Preserve Ring Mask's binary, computation, source, version and catalog, and
  both existing geometry assets.

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
