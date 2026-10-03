# Mix Shaders: EEVEE Gold-edge diagnostic, October 2

The user's same-view screenshots show a darker inner boundary when the saved
Mix Shaders group drives Surface instead of the two original native Mix Shader
nodes. This diagnostic identifies a renderer-sensitive topology difference,
but does not claim an exact reproduction or a repaired working scene.

## Screenshot measurement

In the original 2559 x 1439 images, the inner four-pixel band loses an average
3.968/255 luminance (about 2.51%). The change extends roughly 8-11 pixels inward.
Selected central-ring and base-floor samples differ by at most one integer
channel value. The effect is concentrated at the boundary, not a uniform
darkening of the material. Cursor-affected pixels were excluded.

## Isolated comparisons

Blender 5.2.0 LTS appended only saved Hub_Base into factory-startup background
processes. Disposable material copies retained the real Gold parameters
(Metallic 1, Roughness 0.3), shared images/groups and Ring Mask outputs. The
fixture used a normalized UV plane, a fixed forest.exr world, 128 x 128 EXR,
64 EEVEE samples and one render thread. No user scene was opened or saved.

| Variant | Surface graph |
| --- | --- |
| A | Original native chain, two Gold node instances |
| B | Native chain, one Gold output shared by both shader inputs |
| C | Diagnostic group with B's ordering and direct factors |
| D | C with Multiply-by-one Clamp factors |
| E | Diagnostic D group with two Gold node instances |
| F | Actual saved managed group, two Gold node instances |
| G | Actual saved managed group, one shared Gold output |

F/G directly reference the original saved group's node_tree. Its schema-2
signature, internal links and node creation order were verified without
rebuilding it. Slot 1 has higher priority than slot 2; the original native
chain and diagnostic C/D/E have the opposite priority. The tested ring masks
do not overlap. E/F therefore test the combined saved definition and ordering,
not an isolated priority change.

The first ray-tracing-off matrix A-E had no measured pixel differences. An
explicit ray-tracing-on A-E matrix produced A=E and B=C=D, with maximum absolute
linear RGBA difference 0.02294921875 between the two sets.

The user then saved Builder6, changing its file hash. Old images were therefore
not reused for the final matrix: all seven A-G images were newly rendered from
the latest snapshot. With ray tracing on, A=E and B=C=D=F=G exactly; the same
maximum difference, 0.02294921875, separates those sets. In particular:

- A/F differ: the actual saved group does not match the original native chain.
- F/G match: using two Gold instances does not repair that actual group.
- B/C and C/D match: neither encapsulation nor extra Clamp independently
  changes the result in the controlled diagnostic chain.

The fixture's aggregate edge luminance increases by 0.0050831 while its ring
interior decreases by 0.0016971, measured as candidate minus native A. This
does not reproduce the screenshot's darker inner boundary and unchanged
center. The actual live viewport's ray-tracing, HDRI and view settings were
not successfully inspected, and the real floor geometry was not reproduced.

## Interpretation and current workflow

EEVEE reflection evaluation is sensitive to shader-graph structure in these
tests. Blender's [weight-tree implementation](https://raw.githubusercontent.com/blender/blender/v5.2.0/source/blender/nodes/shader/node_shader_tree.cc)
combines weights for a shared shader output. Its
[reflection implementation](https://raw.githubusercontent.com/blender/blender/v5.2.0/source/blender/draw/engines/eevee/shaders/eevee_nodetree_lib.bsl.hh)
alternates multiple reflection closures between bins and documents a
ray-tracing denoiser concern. These are plausible mechanisms; this diagnostic
does not separately isolate compilation order, bin selection or denoising.
The precise cause of the screenshot's dark-edge distribution remains open.

Retain the original native Mix Shader chain when matching that preview is
required. Do not change Gold brightness/roughness to hide the discrepancy, and
do not describe separate Gold instances as a proven fix for the saved group.
No runtime algorithm, asset, package, or working Builder6 graph was changed.

## Evidence and preservation

Evidence directory:
`D:\Blender\Projects\Build\Recovery\ShaderMixerControls_20261002`.

- `user-screenshot-pixel-audit.json`: original-image hashes and boundary bands.
- `eevee-default-01/eevee-mixer-preview-report.json`: five new default renders.
- `eevee-raytrace-01/eevee-mixer-preview-report.json`: five new ray-traced renders.
- `eevee-actual-group-01/eevee-mixer-preview-report.json`: seven new current-file
  ray-traced renders; no reference renders reused.
- `verify_eevee_mixer_preview.py`: reproduction script with source/graph/HDRI
  protection, fresh-output checks, finite pixels and nonconstant-surface checks.

All three background processes exited normally. Every render returned
FINISHED. Source file, source dependency graphs and HDRI checks remained
unchanged within each job. The user-owned Blender stayed open. No Unity edits,
scene saves, exports, commits, pushes or releases were performed. Cross-chat
heavy work was coordinated with Pool and the window returned after completion.
