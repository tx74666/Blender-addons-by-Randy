# RR Helper 0.2.43: Mix Shaders header controls

The expandable Mix Shaders node required a context-menu action to add inputs
and had no removal action. Selecting the native group now shows **+ / −** at
the right of its header. Plus adds one **Mask / Shader** pair; minus removes
the final pair and its input cables while preserving all upstream nodes.
At least one pair and Base Shader remain. Both changes support Undo.

The controls follow node zoom, DPI and Frame-relative positioning. Clicks
outside their exact rectangles pass through to Blender. The controls do not
add a separate panel, custom saved node type, timer or per-frame scene scan.
Saved materials remain native shader graphs that render without RR Helper.

Edits use a staged group copy and stable socket identifiers. Retained values
and incoming/outgoing cables stay attached; shared assets and other instances
are preserved. Customized or animated graphs are checked before rebuilding.
A failed edit restores the original group, parameters and cables.

## Validation status

Blender 5.2 runtime and renderer checks passed: 47 core mixer regressions,
11 UI helper regressions and 276 add-on lifecycle checks. Four controlled
metallic-boundary A/B cases produced a maximum absolute pixel error of 0.0.
The saved Builder6 Hub_Base material comparison also passed with error 0.0,
retaining its actual Gold parameters and shared group instances. Its enabled
scene-dependent floor modifier required a normalized UV plane fixture; this
does not reproduce the user's original geometry or Material Preview lighting.
The source file and appended source material/group graphs were unchanged.

The disposable GUI fixture passed the actual header-button sequence
2 -> 3 -> 2 -> Undo 3 -> Redo 2 inside a Frame. Existing socket identifiers,
all six original cables, retained defaults and upstream parameters survived.
The fixture process exited normally without saving or touching user scenes.

All eight exporter pipeline stages passed, including the updated 276-check
registration stage. Sync-RRHelperAddon -Check passed. The 20-file release ZIP
and three installation copies match the canonical source at 0.2.43.

The already-open Builder6 process still runs 0.2.42. The final live reload
attempt stopped in the read-only audit before any reload was scheduled:
Modifier RNA exposes `items()` but does not support IDProperties. The audit
helper raised TypeError, the Shader Editor was restored, and no scene save or
export took place. This is a verification-helper failure, not a mixer runtime
failure. Use Blender's F3 > Refresh Add-on action to activate the installed
0.2.43 controls in that existing process.
The audit helper was subsequently corrected offline to mark that exact
unsupported-IDProperties case; eight mock/AST checks passed. The live attempt
was not repeated, so this does not establish a successful live reload.

AST checks passed for changed runtime and test files. The node library
consistency check and installed Mix Shaders asset read-only deployment check
passed. Asset-construction functions and their constants match the validated
0.2.42 source by AST equality. The library stays at 0.2.0, with the same saved
Mix Shaders and Ring Mask graphs, interfaces, catalogs and binary checksums.
The metadata update records current helper source provenance separately from
the original saved-asset render evidence.

Checks cover add-on refresh/disable/failure rollback, native save/reload without
the helper, four metallic-boundary A/B cases, and a conservative audit/comparison
of saved Builder6's actual Hub_Base. The disposable GUI fixture was separate
from working Builder6 and X scenes. Actual-render failures return a nonzero
exit code and cannot be reported as a preflight skip or reuse old EXR files.

The live Builder6 audit found a v2 Mix Shaders group with a matching graph
signature, but its output was disconnected. Surface was driven by two native
Mix Shader nodes. Both paths reference the same two Ring Mask outputs and the
same Base Shader; the Gold instances share a group with no inputs, Metallic 1
and Roughness 0.3. This audit does not establish a mathematical dark-edge bug.
Renderer comparisons must retain those actual parameters and instance reuse.

The separate live export identity repair passed validation after giving only
Hub_Floor_Belcony an independent identity. Original floor/elevator identities,
all geometry, transforms, parenting, selection and queue were preserved. That
operation neither saved Builder6 nor exported assets. Save the current Builder6
session with Ctrl+S to persist that identity repair. Its recovery evidence is
in `ExportIdentityConflict_20261001`, beside the mixer evidence directory.

Evidence belongs in
`D:\Blender\Projects\Build\Recovery\ShaderMixerControls_20261001`.
Changes are local. No Git commit, push or external release is performed.

Package: `dist/rr_helper-0.2.43.zip` (20 files).
SHA256: `71ba06dde8721b4698f49dc9c383d764bebac3f1ba7481998cc195c35bb70888`.

## October 2 EEVEE follow-up

The metallic-boundary and saved-material renderer checks above used Cycles.
They do not guarantee identical EEVEE Material Preview reflections. A later
[EEVEE diagnostic](Shader_Mixer_EEVEE_Gold_Edges_20261002.md) measured differences
with ray tracing enabled, including the unchanged actual saved managed group.
It did not reproduce the precise screenshot dark-edge distribution. No runtime,
asset, deployment package, or working material was changed for this follow-up.
