# Character Designer 0.76.2 — Unity Export foldouts

The Unity Export Objects and material foldouts repeated whole-scene helper
metadata checks on each redraw. The material body also listed every used
material, including those not selected for simplified export.

Objects now inspects helper roles only on bound candidates and saved references.
It still reads custom-shape pointers from every scene armature, once per bone,
so a foreign rig can never turn its custom shape into an exportable mesh. Each
draw reuses its fresh binding inspection; nothing is cached across draws or
used as stale authority by an export or material action.

The section arrows use an internal operator without `UNDO`, avoiding an undo
snapshot for expanding a list. The existing saved foldout properties remain
saved with the per-character profile.

Use Simplified Materials shows only chosen materials. Add Material opens a
searchable list of other materials on included meshes. Use Original removes
one choice, including a retained choice no longer used by the character. The
search list belongs to that popup and is cleaned on accept, cancel or refresh;
acceptance freshly validates the current character, bindings and material use.
Original materials, shaders, mesh data, poses and animation are not edited.

## Validation

Blender 5.1.0 and 5.2.0 LTS native UI tests pass, including registered RNA enum
callbacks, search acceptance after scene changes, stale choice removal,
asynchronous export lifecycle and per-character save/reopen persistence.
Blender 5.1.0 scope checks pass all 34 conditions, including directly assigned
foreign custom shapes, container/helper roles, unbound saved references,
binding/ancestry changes, hidden clothing, missing references and main-body
guards. Drawing the material body enumerates no unused material slots and
does not capture Hair motion or invoke its native backend proof.

The frozen 0.76.1 package and 0.76.2 were compared in one native 5.1 process,
using the same dependencies, 2,415 bones, 377 meshes, 5 bound meshes,
64 materials/320 slots and one chosen material. Each condition has one warmup
and five alternating paired samples; median Python drawing time:

| Foldouts | 0.76.1 | 0.76.2 |
| --- | ---: | ---: |
| Closed | 22.115 ms | 2.474 ms |
| Objects | 24.366 ms | 2.855 ms |
| Materials | 21.639 ms | 2.691 ms |
| Both | 23.733 ms | 2.651 ms |

These measure Python panel work with a recording layout, not mouse-to-screen
latency, viewport FPS or memory savings. The old 0.69.0 scope-suite timings
are retained only as historical regression evidence and are not this comparison.

Evidence: `D:/Blender/Projects/Character/X/Validation/unity_export_foldouts_20261005/`
contains `benchmark_51_pair1.json`, `scope_freshness_51_final.json`, `ui_51.log`
and `ui_52.log`. Earlier failed probes are retained; the native enum callback
was corrected to use OperatorProperties, and the test fixture's temporary
collection link was cleared before its scene-unlink check.

## Deployment and artist integration

0.76.2 is locally packaged as 144 files/1,076,862 bytes, SHA256
`f3b00d2e92fa9954d7816aff9ff7bb099b06395cafffa5e274e1d69b6006f9e7`.
The 5.1 and 5.2 user installations and X validation copy all pass deployment
`--check` with zero differences. Only README, version metadata and the two
Unity Export runtime modules differ from the frozen 0.76.1 package.

The current X window was already running 0.76.2 under Blender 5.1.0. Registration
and installed module paths were verified; this integration did not execute
Refresh. Native foldout operations and a visual check confirm that the material
body shows only the chosen Stocking Main entry, alongside Add Material.

On the current artist data, the same frozen 0.76.1/new-runtime Python drawing
comparison used one warmup and five alternating paired samples per condition:

| Foldouts | 0.76.1 | 0.76.2 |
| --- | ---: | ---: |
| Closed | 2.935 ms | 0.988 ms |
| Objects | 3.025 ms | 1.016 ms |
| Materials | 3.370 ms | 1.317 ms |
| Both | 3.289 ms | 1.132 ms |

These are RecordingLayout Python timings, with the same limits as the fixture
comparison above. The author mesh/UV/weights/Shape Keys/Rest/actions/material
relationships, selection, display, Texts and export settings remain exact.
Pose inputs remain exact and maximum evaluated matrix error is zero. The
temporary editor was restored to Geometry Nodes before saving.

An independent native recovery copy was saved first. Two initial artist saves
failed with "Cannot change old file (file saved with @)"; those reports are
retained and the transient replacement failure's cause is undetermined. GUI
Save then succeeded, followed by the final protected save with one FINISHED
attempt. X.blend was saved at 2026-10-05 16:46:46.215693 +08:00, 32,261,124 bytes,
SHA256 `da79369f32b81c8db425e3cb59dab6f8a926b6a059089de89cdf2e401e0d4bb3`.
The GUI title has no unsaved marker and the disk hash matches the native report;
there was no additional artist disk-reopen audit.

Artist evidence in the same Validation directory:
`live_refresh_save_20261005_161922_650499.json` contains the protected foldout
checks and paired measurement; `finish_live_save_20261005_164637_600603.json`
contains the successful final save and protection checks. Frozen ZIP versus
5.1/5.2/X installations was independently rechecked at 16:46:54 +08:00: all
144 files match in each copy. This check used the frozen release, since current
canonical sources also contain separate, unreleased Dress work.

All changes are local; no Commit, Push or PR. This task does not change Unity,
Builder6, Hair motion, FK/IK, physics or rig setup. Actual model/reasoning effort
for this phase are unrecorded. One final Console performance entry records this
completed integration under CD-UNITY-EXPORT-FOLDOUTS-20261005-01.

Next investigation: a confirmed slow click in the refreshed 0.76.2 runtime
should measure actual UI/undo/depsgraph work. Do not repeat the historical
whole-rig switch or Hair backend investigations from the material foldout.
