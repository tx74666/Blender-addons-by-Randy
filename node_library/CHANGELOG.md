# Randy Node Library changelog

This history covers original node assets maintained in this repository. Library versions group a set of assets; individual node versions are recorded in [manifest.json](manifest.json). Local changes reach GitHub only after the owner commits and pushes them in GitHub Desktop.

## 0.3.1 — 2026-10-02

- Unify the visible full-ring and arc asset as **Ring Mask 0.2.1** in Textures.
  It has Inner Radius, Ring Width, Edge Softness, Start Angle and Sweep Angle;
  Sweep defaults to 360. Mask and Ring Data retain Arc Mask 0.2.0's native
  calculations and socket identifiers. The normal Texture color tag is brown.
- Keep Arc Mask 0.2.0 as a preserved binary/report fixture outside the visible
  asset directory. The original three-input Ring Mask 0.1.1 and its radial
  evidence remain historical fixtures; the new public name does not overwrite
  or relabel that proof.
- Use dedicated current Ring Mask build, verification, publication and guarded
  deployment tools. Current saved-asset evidence belongs to
  `validation/ring_mask_current.json`, separately from older render reports.
- Verify Ring Mask with **96 passed checks**: 46 real shader samples in each of
  Cycles and EEVEE, plus four structure, persistence and source/asset-preservation
  checks. The isolated explicit-migration fixture passes ten checks, including
  parameter/link preservation, customized-source refusal and injected rollback.
- Add explicit current-file migration audit and rollback helpers. Independently
  proven old radial/Arc nodes retain values, links and presentation; customized,
  animated and read-only owners are refused. Migration requires a backup and
  explicit invocation. There is no automatic handler or global node-group purge,
  and linked source library files remain unchanged. Live Builder6 migration and
  menu refresh are separate verification, not implied by local publication.

## 0.3.0 — 2026-10-02

- Add native **Extend Mask 0.1.0** in Textures. One menu selects Inner, Outer,
  Both or Outline; Width, Gap and Softness control new bands from real source
  boundaries. Radial modes inherit Arc angles. Outline uses exact annular-sector
  contour distances, including cut ends, and excludes the original source.
- Arc Mask **0.2.0** appends a native Ring Data Bundle output. Existing inputs,
  Mask socket identifiers and Mask graph remain unchanged. The extra data avoids
  duplicating the source's radius and angle controls.
- Inner Data and Outer Data independently describe the generated radial bands
  for further extension. Outline's radial data outputs are inactive; arbitrary
  scalar-mask dilation and repeated outline-distance approximation are excluded.
- Validate new computation using 64 rendered points in each of Cycles and EEVEE,
  native Bundle/Menu save/reopen, and fresh examples. Arc Mask passes 38 rendered
  points and compatibility checks against its previous saved Mask graph.
- Update the existing local Textures library without changing working scenes or
  unrelated assets. Current-file Arc copies are not silently upgraded.

## 0.2.1 — 2026-10-02

- Retire the standalone Ring Mask asset. Arc Mask covers full circles with
  Sweep Angle 360, as well as partial arcs; no separate mode toggle is needed.
  Preserve the original radial binary as an internal build/test dependency
  outside `assets/`. The visible inventory is five assets in four bundles.
- Arc Mask 0.1.1 hides its embedded radial dependency from the ordinary Group
  submenu. Its Texture color tag, sockets and calculations are unchanged.
- RR Helper 0.2.45 adds Ring Group header **+ / −** controls for any number of
  Mask inputs, retaining other parameters/connections and supporting Undo.
  The Add Ring shortcut now adds Arc Mask with a 360-degree sweep.
  Existing scene Ring Mask nodes can be explicitly migrated without moving
  nodes or losing their original radial controls and connections; customized
  or animated graphs are preserved. No automatic scene migration runs.

## 2026-10-02 — Arc UV mapping diagnosis

- Document why overlapping mirrored UV halves can repeat one Arc Mask while
  full Ring Masks still look correct. Builder6's floor comparison changed from
  two arc components to one in both Cycles and EEVEE after unfolding its left
  UV half, preserving radial distances. Arc Mask remains 0.1.0 and the library
  0.2.0; no asset graph, interface, catalog or binary was changed.

## 2026-10-02 — RR Helper 0.2.44 header visibility fix

- Keep the selected Mix Shaders node's **+ / −** controls visible with
  **Show Options** disabled. Only collapsed, unselected or read-only nodes
  suppress the controls; hiding ordinary node settings no longer does.
- The library remains **0.2.0**. Saved asset graphs, interfaces, catalogs and
  binary checksums are unchanged; this is an editing-helper visibility fix.

## 2026-10-01 — RR Helper 0.2.43 editing controls

- Add optional **+ / −** controls on the selected native Mix Shaders node.
  Plus adds one Mask / Shader pair; minus removes the last pair and its cables,
  keeping upstream nodes. Both support Undo, preserve retained socket identifiers
  and links, and edit only that instance. At least one pair and Base Shader remain.
- Add **Remove Shader Slot** to the node context menu. Customized or animated
  graphs are validated before removal; failed staged edits restore the old graph.
- The library remains **0.2.0**: its saved graphs, interfaces, catalogs and binary
  asset checksums do not change. Update canonical helper provenance using AST
  equality of every asset-construction function against the validated 0.2.42 ZIP.
  Runtime and live editor verification are recorded in the 0.2.43 release notes.

## 0.2.0 - 2026-10-01

- Add **Ring Group 0.1.0** and **Arc Mask 0.1.0** beside Ring Mask in the
  existing **Textures** catalog. Ring Group combines scalar masks with clamped
  Maximum while passing one shared Shader through; each material can cover
  several independently controlled rings without repeated shader mixing.
- Arc Mask uses the unchanged original Ring Mask radial group and adds Start
  Angle / Sweep Angle in degrees. Default sweep 180 makes a semicircle;
  0 is empty, 360 a full ring, and the start wraps through the 0/360 seam.
  Edge Softness remains radial; angular cut ends are hard.
- Update **Mix Shaders to 0.2.0** with two initial Mask / Shader slots and no
  hidden `_Connected` state. Both connection editing and rendering use native
  Blender nodes with RR Helper disabled. Leave unused masks at 0; a white
  mask with an unlinked Shader follows native Mix Shader black-input behavior.
  Slot 1 covers slot 2, with Base Shader always at the bottom.
- RR Helper **0.2.41** provides optional node-context Add Ring, Add Arc,
  Add Mask Slot, and Add Shader Slot shortcuts. The active Ring Group stays
  active for repeated adds. New rings use available radial space when constant
  controls permit it; a full range or linked / animated controls produces a
  warning while preserving existing parameters.
- Add Shader Slot also converts an ordinary Mix Shader into the expandable
  native mixer, preserving Factor / shader / output cables and the initial
  result. Expansion retains unused local asset templates, linked groups,
  overrides and fake-user groups when their last node instance is replaced.
- Native Ring Groups can also be chained through their Mask sockets, with
  the shared Shader connected only to the final group. Native Math Maximum
  and ordinary Mix Shader nodes support larger mask / material combinations
  without the optional helpers or a fixed ring-count setting.
- Preserve previously appended Mix Shaders v1 and old Ring Stack data;
  their existing compatibility behavior is retained instead of changing
  saved materials silently. Original Ring Mask and geometry assets stay unchanged.
- Saved-asset validation passed: **Mix Shaders 14 checks / 9 rendered samples**,
  **Arc Mask 43 / 38**, and **Ring Group 22 / 17**, including native save/reopen,
  unchanged-source checks and independent expansion to **25 shader pairs /
  24 mask inputs**. Reports are in `validation/mix_shaders.json`,
  `validation/arc_mask.json`, and `validation/ring_group.json`.
- Runtime validation passed **37 mixer checks + 2 conversion render cases +
  9 mixer samples**, **34 Ring Group checks + 7 samples**, and **170 lifecycle
  checks**. Lightweight checks passed 6 publication rollback tests, 2 new asset
  deployment tests, 3 mixer deployment tests, and library consistency for
  **6 assets / 5 bundles**.
- RR Helper 0.2.41 packaged 19 files and all three local deployment copies
  match. Mix Shaders, Arc Mask and Ring Group installed in the existing Nodes
  library each passed final `--check`. Original Ring Mask's binary stayed
  unchanged. The running Blender menu was not refreshed or checked, and
  Builder6 materials were not edited in this release. Use F3 Refresh Add-on
  and Nodes Asset Browser Library Refresh in an existing session.
- Changes remain local; no Git commit, push or remote release publication was performed.

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
