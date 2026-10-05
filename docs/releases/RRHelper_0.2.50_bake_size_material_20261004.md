# RR Helper 0.2.50: bake resolution and separate baked materials

Complex Blender procedural materials need texture maps before the ordinary
Unity material handoff. The existing manual targets are retained. Texture > PBR
now adds **Recommend Size**, **Create Baked Material** and **Auto Bake PBR (4 maps)**.

## Size recommendation

The default is an adjustable 128 pixels per meter for balanced PC use and an
explicit estimated UV coverage of 70%. The tool sums evaluated world triangle
area per checked material, including modifiers, object transforms and scene
unit scale. Different object instances and object-level material overrides are
counted separately. Each material has its own map set; the shared Size setting
uses the largest recommendation. Calculation happens on request, not every redraw.

`required pixels = sqrt(area in m² / estimated UV coverage) × pixels per meter`.
The next supported 512, 1024, 2048 or 4096 tier is selected. Exceeding 4K is
reported as a capped result with the achieved density and a split/lower-density
suggestion. This estimates pixel budget; it does not measure UV overlap, island
distortion, camera distance or the smallest authored detail. Missing UV maps are
reported. The result changes Size only. Run Create Targets afterward to create
new targets; old shared images are retained. Prepare Bake rejects mismatched sizes.

## Manual material handoff

1. Choose the material and resolution; create targets and bake each required pass
   using Blender's native Bake. Inspect the maps.
2. Save Images after the last Bake. Base Color, Roughness and Metallic are required
   for the new opaque PBR material; Normal is optional. Metallic requires manually
   routing the final metallic mask through Emit. Color-only Diffuse may need a
   manually prepared Emit source for metallic/mixed materials.
3. Create Baked Material validates persisted PNGs, role color spaces and sizes,
   then creates a clean `<source>_baked` Principled material. Readable Unicode names
   and short collision sequences are retained. Source nodes and shared images are
   preserved. The material has a fake user and `preview_required` quality metadata.
4. Assignment is opt-in and affects selected asset meshes using OBJECT material
   slots, preserving unselected users of shared meshes. Preview before exporting.

PNG structure and file persistence do not prove the last bake was saved or that
the colors/UVs are visually correct. Use Save Images after every final bake.
This output is opaque PBR; glass, alpha, transmission and emission use a separate
material workflow. The preflight dialog checks the selection/maps again on apply.

## Automatic bake scope and source protection

Auto uses checked materials on selected assets and always creates four maps.
Only a direct active Principled surface is supported. Mixed/custom shader outputs,
linked/read-only sources, unsupported opacity/emission/transmission and inconsistent
shared UV names are refused before image allocation. This intentionally blocks
incorrect first-branch baking of Ring/Mix materials such as the illustrated Hub_Base.

Each pass uses a fresh unconnected temporary image target and the exact active
shader input, restoring old nodes, image bindings, active node, selections and
output links. Setup failure restores already prepared sources. Finished maps
create separate baked materials and opt-in-by-Auto object slots; late failure
restores assignments and removes only new materials. Every run uses a fresh
readable numbered output folder, retaining previous maps. Bake output files are
retained for inspection after failure. The progress bar measures workflow steps
and names the current pass; it is not render/sample accuracy or a quality score.

## Validation and local release

- 11 pure recommendation checks pass.
- Seven factory Blender material checks pass: source/shared image protection,
  Unicode names, persisted/packed PNGs and rollback.
- Ten factory Blender tool checks pass: evaluated area, units, Mirror, material
  filters/overrides, shared instances, old target preservation, assignment scope,
  mixed rejection and automatic restoration. The automatic test uses a tiny
  stand-in bake writer for transaction assertions.
- A separate real CPU Cycles smoke bakes all four 2×2 passes at one sample, checks
  linear/sRGB base color, roughness, metallic and flat tangent normal values, and
  preserves source links and an unselected shared user. The process exited.
- 16 existing exporter feedback checks pass. No production scene is baked by
  these tests and no Unity import/runtime result is claimed.

The 25-file 0.2.50 package was built and deployed with repository tools to both
Blender 5.2 user add-ons and the verified active addons_core copy; both deployment
checks report zero differences. Native refresh and the current floor estimate
remain unverified: after unrelated input was detected, activation of Builder6
timed out twice, including the documented window-binding recovery. No current
model was baked, assigned or saved. The existing Refresh Add-on button is the
entry for loading the deployed version. The prepared read-only scene/floor audit
and test evidence are in
`D:/Blender/Projects/Build/WIP/Validation/bake_recommendation_20261004/`.

Changes remain local; no commit, push, PR or remote release is included.
