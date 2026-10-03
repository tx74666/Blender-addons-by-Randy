# Character Designer 0.70.0: Refine Symmetry

Local feature update on 2026-10-02. No commit, push or published release.

The new Modeling/Weight panel analyzes reliable topology correspondence and
repairs only Basis coordinates. It adds Matched/Misaligned/Unmatched/max
error reporting, Select Misaligned, read-only Preview, Left to Right,
Right to Left and Average, strict selected scope, centerline tolerance,
Shape Key delta preservation, ordinary X Mirror validation and Undo.
The complete workflow and safety behavior are in `docs/refine_symmetry.md`.

Fourteen native Blender 5.2 test cases pass. They include independent
asymmetric expressions, relative keys, live Edit Mode data, mode round
trips, protected topology/UV/weights/attributes/rig links, stale Shape Key
plans, injected post-write failure rollback, ordinary mirrored selection
with Topology Mirror disabled and native Undo.

Thirteen pure topology-pair tests pass, including local damage containment,
seam-qualified repeated topology, conflict rejection and invariance under
vertex/edge/face renumbering. The existing six UI-page checks and four native
restricted-registration/save-load checks also pass with the new module.

## Saved Cosha integration

The private saved X character was appended into an isolated factory scene;
the user's current scene was not edited. Of its 3,658 vertices, 1,160 pairs
and 135 center vertices were qualified. 129 pairs were misaligned, with
maximum mirrored error 0.04761341796 in mesh-local units. Average moved
258 vertices and reduced target error to zero. The 1,203 unmatched vertices
were left exactly unchanged.

All 12 Shape Keys retained their original Basis-relative deformation deltas
within float32 rounding (maximum component error 1.4901161194e-8), including
the independent eye keys and forearm twist keys. Topology, indices, loops,
UV, attributes, vertex groups, weights, rig links and modifier targets were
unchanged. Native ordinary X mirrored selection was sampled on separate
temporary meshes with Topology Mirror disabled: 16 pairs in both directions,
0/32 correct before repair and 32/32 correct after repair.

Evidence: X project's `validation/refine_symmetry_cosha_20261002.json`.
The original `X.blend` was never saved by the probe and retained SHA-256
`433589b1b692c995bdcab44e296caef1935bcadee3903a0059adebc6e9925b96`.
Private character diagnostic fixtures remain under the X project's
validation directory and are not packaged.

## Local release

Built `dist/character_designer-0.70.0.zip` with 128 files. SHA-256:
`3691d2476167a925c9137dce3b94ab21cf96083604e1250519c5137e5d0872ec`.

Deployed canonical source to the Blender 5.2 user installation and
`D:\Blender\Projects\Character\X\addons\character_designer`.
The matching `tools/deploy_local.py --module character_designer
--project-addons D:\Blender\Projects\Character\X\addons --check`
reports zero differences in both 128-file copies.
Previous installed files are retained under
`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20261002-154911-623e6528`.
All isolated Blender test processes exited normally. Existing user work was
not closed or saved by this feature task. Changes remain local, with no
commit, push or publication.
