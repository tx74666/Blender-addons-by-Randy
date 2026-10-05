# RR Helper 0.2.51: Surface Text across a joined Mirror seam

Selecting a wall strip that reached the Mirror center previously offered only
Original or Mirror placement. **Add Surface Text** now treats a selected region
and its mirrored copy as one region when Mirror Merge welds a complete boundary
edge. It creates one editable Font centered over the full region and a sampling
mesh covering both halves. The target mesh and its Mirror modifier are retained.

Select the source faces extending to the center seam, then use Add Surface Text.
The connected case creates the text directly; a disconnected pair retains the
existing viewport click to choose a side. Neither the edit cage nor Clipping
establishes a seam: Merge and the actual geometry determine connectivity.
Existing texts are not moved or rebound automatically. Bind Existing Surface Text
keeps its explicit Original/Mirrored side choice and preserves the authored pose.

## Geometry and export

The weld uses Blender's strict source/reflected vertex distance in target-local
coordinates, compared with the modifier's Merge Distance. Matched vertices move
to their midpoint in the private sampling description. A shared edge is required;
point-only contact, separated regions and disabled Merge do not become joined
regions. Source faces crossing the plane or collapsing on it are not guessed.
The existing single-axis, single-Mirror and non-Bisect restrictions remain.

The complete area, centroid and normal are recalculated after welding. Winding,
nonuniform and negative object scale, and the actual Mirror Object transform are
handled explicitly. All source and mirrored adjacent faces, including the seam,
retain the existing normal/fold limits for a gently curved text region.

Original source face indices remain unique. A persistent joined flag distinguishes
the combined sampling region from either individual side. Evaluated sampling
retains both reflected halves and checks actual shared-edge connectivity after
evaluation, so later changes to Merge or Shape Keys cannot silently export a
disconnected pair. Transient sampling aliases retain their existing identity,
cleanup and coordinate contracts; no Unity format change is required.

Creation checks evaluated face mapping and connectivity without extending export
modifier restrictions to the viewport workflow. A trailing Smooth by Angle node
modifier can preserve the selected region during creation. Export keeps its
existing stricter modifier support checks; unsupported stacks still require the
existing apply/rebind workflow before exporting editable Surface Text.

## Validation and installation

All 50 checks passed in an isolated Blender 5.2.0 LTS factory process: 20 new
Mirror joining cases and 30 existing editable text, UV/export and display-name
regressions. The new fixtures cover threshold boundaries, point-only contact,
unselected caps, curved and sharp seams, independent object/plane transforms,
negative scale and normals, preserved explicit Bind placement, broken Merge,
Shape Key failures, and trailing shading nodes. A real FBX export/import retains
the complete selected +/-4 m region and excludes the unselected +/-6 m strips.

The first run retained 48 passes and one error from an inconsistent sharp-fold
expectation. Automatic joining now falls back to the existing side picker when
aggregate normals or adjacent folds are incompatible. The final assertion requires
that fallback. The initial and final logs are retained separately in
`D:/Blender/Projects/Build/WIP/Validation/surface_text_mirror_join_20261005/`.

The 25-file `rr_helper-0.2.51.zip` package was built locally. Repository deployment
tools updated both Blender 5.2 user add-ons and the verified Builder6 executable's
`D:/Blender5.2/5.2/scripts/addons_core/` installation. Both final `--check` results
report zero differences, and previous installed files have deployment backups.

The test process exited normally. The live Builder6 scene was not loaded, changed
or saved by this task, and the separate Blender 5.1 session was not updated.
Runtime refresh and a visual check on the user's current edited geometry remain
pending: use **Refresh Add-on** when convenient to load the deployed version.
No new Unity editor/runtime import verification is claimed. Changes are local;
no commit, push, PR or remote release was performed.
