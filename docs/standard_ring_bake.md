# Standard Ring bake and import

Ring geometry/pattern is authored in Blender. Unity keeps the line color and
emission strength editable independently from the floor's base color.

## Blender

1. Select the floor mesh and open **RR Helper > Texture > PBR Framework**.
   The mesh needs an existing active bake UV layer. A missing layer produces
   an actionable error; the tool does not automatically unwrap or alter it.
2. Select **Hub_Base** in Materials. Start at **1024** for a floor viewed at a
   moderate distance, or **2048** for close views of narrow lines. **Recommend
   Size** uses the selected world area, Pixels / Meter and estimated UV coverage.
3. Use **Bake PBR for Unity**. Ring Group must use its own Mask and Shader as
   one layer of the final Mix Shader. The graph can contain nested native groups
   and multiple Ring layers. **Editable Ring** selects which layer receives
   Unity's controls; selected layers need one uniform color and initial emission
   strength. The saved Hub_Base defaults to its emitting yellow layer, retaining
   the blue-gray unlit layer as background. The selection remains available
   after the selected layer's brightness becomes zero. Unsupported transparency and extra BRDF layers
   are rejected before creating output.
4. Preview the new source_baked material, then use the existing Queue **Model**
   export. Bake again after changing the procedural pattern or its UVs. Repeated Bake
   resolves the retained original Material ID, including after a rename. Model
   export alone does not reevaluate the procedural pattern.

**Advanced Bake Settings** exposes Samples (default 32), Margin (16 pixels), and
the output directory. The optional manual target workflow keeps its original four-map
default; Emission can be enabled separately. Auto Bake always selects the complete
PBR/Ring contract. Narrow lines need enough pixel coverage; higher samples
cannot restore a pattern narrower than a pixel. A material shared across meshes
requires the same chosen bake UV layer name and intentionally nonoverlapping UVs.

The bake creates independent material and group copies for each pass, restores
selection/UV settings and source material slots, and finally applies an object
material override only to the selected meshes. Other users of shared source
materials and meshes keep their original material.

## Unity

Import/update the package using the **Standard** workflow. Open the generated
Ring material. Its first controls are **Ring Color** and **Emission Strength**.
Color changes the visible lines and their emitted light together. Zero strength
removes Ring emission while retaining the visible colored pattern. Base Color
belongs to the floor background.

Emission controls the material's radiance. Nearby illumination and bloom remain
part of the project's Unity lighting/rendering setup; this import does not alter
global lighting, shadows, or post-processing settings.

## Texture contract

| Export channel | Meaning | Encoding |
| --- | --- | --- |
| BaseColor | Full original preview/fallback | sRGB |
| Roughness | Final weighted roughness | linear |
| Metallic | Final weighted metallic | linear |
| Normal | Tangent-space final normal | NormalMap |
| Emission | Radiance outside the editable Ring layers, divided by the exported gain | linear |
| RingBase | Weighted background contribution, including unselected Ring layers | sRGB |
| RingMask | Final Ring coverage, including soft edges/layer priority | linear |

Unity evaluates `RingBase * BaseColor + RingMask * RingColor`, then adds the
separate Ring emission. This avoids applying the Ring soft edge twice. Ordinary
emission uses a white tint and its exported normalization gain; explicit zero
is retained. Tiny face/emission color differences up to one linear 8-bit step
are unified to the face color and recorded as bake warnings; larger differences
require choosing a matching source color. Metallic/Roughness are packed as R=Metallic and A=1−Roughness;
the mapped material uses a Smoothness multiplier of 1.

## Reimport ownership

Source PNG/FBX files stay in the package. Owned Unity materials/textures are
generated outside the replaceable source folder. Source content fingerprints
include the texture bytes, so an explicit Standard import detects an image
replaced at the same path even when its timestamp/size match.

Each baked output retains its original source material identity separately
from the FBX display name. Repeated Bake suffixes and new texture filenames
do not change the Unity material GUID or force an unchanged source to reset.

An unchanged source preserves Unity material edits, including when a new mesh
slot begins using that material. A changed Blender source updates its owned
generated material. An external material assigned to a slot is preserved. Missing owned generated textures can be recovered by an explicit import, while
retaining Unity material colors and scalar settings. PNG
changes do not enter the existing automatic whole-prefab rebuild queue; use the
explicit Standard import after a new bake.

## Verification status

The 32 filesystem transaction cases and 41 targeted native Blender cases passed.
The controlled saved Hub_Base fixture exported two materials and 12 maps with a
nonempty Ring mask. It does not represent the dirty live scene or complete
building geometry. Unity compilation passed, followed by the fixed five-case
correction suite (5/5) and complete isolated native regression (23/23), with zero
failures/skips and preserved scene guards. The full run completed at
2026-10-07 21:57:17.7118544 UTC.

The earlier 18/23 and 4/5 reports remain preserved. The first enormous
negative-blue GPU sample did not recur, and its cause remains unknown. The
small floor delta in the 4/5 run came from filtered mask coverage in the private
visual fixture; only that fixture's black plateau was enlarged. Original
default-pipeline assertions and tolerances remain required and passed.

Unity can report that a readable generated material name differs from the
fixed `Material.mat` filename. The alias remains readable for artists and the
internal material identity remains stable; this naming warning does not mean
the material failed to import.

RR Helper 0.2.59 passed all eight required ExporterContracts groups, including
276 registration lifecycle checks. The three affected offline projects built
serially with embedded SDK 8.0.318 and zero errors. The Tests build retains seven
existing CS0649 warnings; the other two builds have zero warnings.

The 29-file 0.2.59 ZIP has been built and installed to Blender 5.2. The local
source-match check reports zero differences, and both default and portable
Sync checks passed. ZIP SHA256:
`05439EDF4B6170E19FF7DB001416E2E73DCE585A5ECCA723AF565A539274EDB0`.

Live refresh was separately confirmed: the running Builder6 session loaded
RR Helper **0.2.59** from
`D:/Blender5.2/5.2/scripts/addons_core/random_realm_builder_exporter/__init__.py`.
All 29 Python files match canonical source bytes and the Editable Ring operator
is registered. The final audit preserved existing user scene values, selection,
dirty state and the saved file. It records a newly added collapsed Advanced UI
default and cleanup of the old plugin's temporary preview helper separately;
it does not claim every snapshot field is identical. No scene Save, Bake or
Export ran during refresh. Changes remain local; no Git commit, push, PR or
remote release is included.
