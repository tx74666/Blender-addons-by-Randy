# Character Designer 0.69.1: Performance

Validation started 2026-10-01; local packaging and deployment completed
2026-10-02. No Git commit, push or published release.

## Changes

- Unity Export reuses one fresh scene inspection within a panel draw rather
  than discovering character armatures, helpers and bound meshes repeatedly.
  Expanded Objects uses that same inspection for invalid-reference cleanup.
  Operators and export requests still inspect the current scene independently;
  nothing is cached between redraws. Material slots are enumerated only when
  Use Simplified Materials is expanded.
- Target Rotation panel and Reset button availability use selection/ownership
  checks. Expanded drawing and actual operations retain full rig validation.
- Pole guide drawing retains only its latest numeric geometry/color GPU
  batches. Live pose, selection, visibility and theme checks still run.
  Mirror preview retains its shader beside its existing batches. Failure,
  preview replacement and unregister discard the relevant transient resources.
- Animation imports prepare constant conversion and bind-matrix inverses once
  per operation, preserving multiplication order and validation of each motion
  sample. The independent reference evaluator remains unchanged.
- The first Worklist Add shares its already validated packet with source
  import, avoiding a second live decoded copy. Fresh late packet/model/Link
  checks, rollback and explicit Source baseline verification remain intact.
- Animation FBX hashing reads at most 1 MiB per block instead of allocating a
  second whole-file buffer. The published hash and output contract are unchanged.
- Animation uses Character Setup's valid Main Rig when no Worklist is connected.
  Its duplicate Character Rig selector and Using label are hidden in that case.
  Connected Worklists retain their separately imported editing rig and strict
  ownership checks. Unset Main Rig retains the legacy workflow; invalid configured
  rigs require repair in Rig > Character Setup rather than falling back silently.
  The Connect button tooltip replaces the permanent unconnected-workspace hint.
- Unity companion Transfer/Window mirrors are synchronized to the accepted full
  RandomRealm2 versions, including its existing collection/provenance metadata.
  Interactive Quick View may fall back to ordinary skinning when forearm
  correction fails, with a concrete CorrectionWarning. The two-argument Preview
  constructor, Sample, Capture and exports remain strict. The companion window
  starts with Unity's current selection rather than hardcoded Cosha/Walk defaults.

These are targeted reductions in repeated work. They do not establish a fixed
FPS or memory-use reduction for every character. Sampling rates, Source hashes,
export locks, rig mutation checks and topology freshness checks are retained.

## Preserve Volume warning

The skirt generator intentionally enables Preserve Volume on its Armature
modifier. Blender uses quaternion deformation to retain volume during bending;
this exporter publishes ordinary FBX skin weights, so it reports a possible
deformation difference rather than an export failure. See the
[Blender Armature modifier manual](https://docs.blender.org/manual/en/5.2/modeling/modifiers/deform/armature.html).

The panel omits this exact general compatibility notice and normal old-file
preservation notices. The complete export report remains unchanged. No modifier,
weights or Avatar settings are changed to dismiss it. For a comparison, select Dress,
open Modifiers > Armature and temporarily disable Preserve Volume at the same
pose, then restore the setting. Inspect bending, shrinking and intersections
in Unity before deciding whether any weight or rig adjustment is needed.

The Warnings heading, disclosure icon and box appear only when actionable
diagnostics exist, even when the saved disclosure was previously open. Unweighted
vertices, missing texture outputs, disabled skinning, omitted forearm corrections
and unknown diagnostics remain visible. Notice-only completion uses normal
success feedback. Open Export Report and Open Folder buttons are removed from
the panel; report generation and the single Folder picker remain intact.

## Validation

- 98 pure metadata, Worklist, packet/publication, panel and GPU-resource
  lifecycle checks pass. An independent source review found no blockers.
  All eight native checkpoints pass, including three targeted limb tests for
  dynamic Pole display, Auto Align rollback and animated/driven/locked-state
  refusal. Two stale fixture assumptions were corrected: schema-5 Target
  Rotation owns an extra Arm PARENT_DELTA helper (7 bones / 8 constraints for
  one arm and leg), and the default-wire assertion must explicitly select
  DEFAULT palettes before restoring existing custom colors. Production creation,
  validation and color functions are unchanged from immutable 0.69.0.
- Real bpy panel fixture: 300 unrelated objects, 180 main bones and 8 bound
  meshes. Helper/armature inspection counts change from 3 to 1 when collapsed
  and 4 to 1 when expanded. Collapsed material enumeration changes from 1 to 0.
  Six recording-layout samples have medians 22.52 to 7.55 ms collapsed and
  29.81 to 7.48 ms expanded. This is Python panel work, not rendered viewport FPS.
- Matrix-only fixture: 8 bones, 240 samples, reference 30.66 ms and prepared
  15.25 ms. Reference error is 0; evaluated native output error is 4.77e-7.
  Motion finite validation and restore pass. No whole-import speed claim.
- Worklist native 13-case suite, animation import/restore, three actual FBX
  roundtrips and cold panel/RNA/save-reopen checks pass. Empty/notice-only
  warnings stay hidden and complete reports remain unchanged.
- Saved X.blend: four synthetic exact-character control samples pass for 184
  mapped bones and 52 controls, with maximum matrix error 3.50e-6. Geometry,
  weights, Shape Keys, Action/slot/NLA state and input-file hash remain intact.
  This is not a new Unity roundtrip or rendered skin-quality claim.
- GPU construction tests instrument the resource calls; they do not measure
  shader compilation or rendered viewport frame times.

## Release

`dist/character_designer-0.69.1.zip` contains 124 files. SHA-256:
`769691a331fcbb079c94fb03a5acd86f39055f277431c9c46177d10457c6f0b9`.
Re-running the module-only release builder verifies every archived byte against
canonical source. The previous 0.69.0 archive remains unchanged at SHA-256
`3956ad897dd079011a9287f4d128f1a80c169c21c3bda1f558b746ba44ba3273`.

The Blender 5.2 user installation and X validation copy each received 13 changed
files. Both subsequent module-only `deploy_local.py --check` comparisons report
124 files and zero differences. Previous files and the deployment manifest are
retained at
`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20261002-003031-4dc6e26e`.
All task-owned background Blender processes exited; the user's two running
Blender sessions were not reloaded or saved. Click **Refresh Add-on** to load the
installed update in a running session, or reopen normally after saving work.

Native logs and fixture reports are retained in
`D:\Blender\Projects\Character\X\task_artifacts\performance_20261001`.
The full companion Transfer/Window mirrors retain the accepted SHA-256 values
`234d1d8822c4670619ac998623da3897f99420a08fb2579ba1ff60113900fd2b` and
`8a964524488ef712f2e11b5e92eedf491ec1db6f582e8c07e8a8deb0a7501ceb`.
The Character chat independently completed Unity compilation, SDK builds and
strict sampling checks; this deployment did not write Unity project files or
change GUIDs.
