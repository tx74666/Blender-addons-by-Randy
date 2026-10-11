# RR Helper 0.2.59 — Ring PBR bake and Standard material import

Local release record for 2026-10-08. Native acceptance, required integration
checks, packaging, Blender 5.2 deployment/checks and live runtime activation are
complete. A separate native audit confirmed the running Builder6 session loaded
**0.2.59** from its actual `addons_core` installation. Scene preservation and the
small, explicitly recorded UI/helper changes are detailed below. No remote
release is included.

## Result

Ring patterns authored in Blender can be baked and exported through the
existing Standard Model workflow. Unity's generated Ring material exposes
**Ring Color** and **Emission Strength** first. Ring Color changes the visible
line color and emission color together; the floor background has an independent
Base Color control. Zero strength removes Ring emission while keeping its
colored pattern visible.

The bake resolves supported opaque Principled, Emission and native Ring/Mix
graphs, including supported nested groups. It writes the usual PBR maps plus
separate Ring background and coverage maps. Ordinary HDR emission is normalized
for PNG storage and restores its exported gain on import. Unsupported graphs
are rejected before allocation rather than exporting one arbitrary branch.

**Editable Ring** chooses the layer controlled from Unity. The saved Hub_Base
material defaults to its emitting yellow Ring; its unlit blue-gray Ring stays
in the baked background. Selection persists at zero brightness. Source graphs,
shared node groups and unselected material users remain unchanged.

The everyday workflow is documented in [Standard Ring bake and import](../standard_ring_bake.md):
choose the material, Size and Editable Ring in **RR Helper > Texture > PBR
Framework**, run **Bake PBR for Unity**, inspect the baked material, export
**Model**, then run the Unity **Standard** import. Pattern or UV changes need a
new bake. Unity color/brightness edits alone do not.

## Reimport and ownership

An unchanged source preserves Unity colors, scalar settings and generated
material GUIDs, including new mesh slots using that material. A changed source
updates its owned generated material. Explicit import fingerprints PNG bytes,
so replacing a texture at the same path, size and timestamp is detected.

Repeated bake names and source material renames retain the original source
identity separately from display aliases. Manually assigned external materials
are preserved. Missing owned textures can be recovered without discarding Unity
material tuning. Newly baked PNG changes require an explicit Standard import;
they do not enter the automatic whole-prefab rebuild queue.

## Completed native evidence

| Check | Recorded result |
| --- | --- |
| Filesystem Standard transaction suite | **32 passed** |
| Blender 5.2 Ring PBR suite | **23/23 passed** |
| Blender 5.2 baked-material suite | **7/7 passed** |
| Blender 5.2 bake-tools suite | **11/11 passed** |
| Actual saved Hub_Base fixture | Two materials, 12 persisted PNG maps and native Standard FBX export; RingMask min 0/max 1, 84 of 4096 covered pixels; source graph and saved file unchanged |
| Unity compilation | Production importer/shader and verification compiled in Unity 6000.5.9f1 |
| Fixed native Corrections suite | **5/5 passed**, 0 failed/skipped; 2026-10-07 21:45:34.842–21:47:52.320 UTC; scene guards preserved |
| Full native isolated suite | **23/23 passed**, 0 failed/skipped; 2026-10-07 21:50:18.9223697–21:57:17.7118544 UTC; every scene guard preserved, exception empty |

The saved material was tested on controlled two-material UV geometry, not the
user's unsaved building scene or complete floor geometry. Factory Blender
processes used CPU baking with one thread and exited. The saved Builder6 file
SHA stayed unchanged during the fixture run. Unity's guards protected scene
identity, dirty state, root identities, selection and saved scene bytes; no
artist scene save or restore was part of these checks.

The full native result is recorded at
`D:/Unity Projects/RandomRealm2/Logs/StandardRingMaterials/isolated-contract-results.json`.
The fixed five-case report and detailed earlier evidence are under
`D:/Unity Projects/RandomRealm2/.codex/task-staging/ring-pbr-export-20261008/`.

## Earlier failures, warnings and limits

The initial **18/23** and first correction **4/5** reports are preserved. Native
material path/GUID/localFileID and raw color checks replace insufficient managed
wrapper/literal equality checks. The private visual mask was widened to provide
a true zero-coverage floor sample; production filtering, camera, sample positions,
default pipeline, assertions and tolerances were retained.

The first run's enormous negative-blue sample did not recur. Its cause remains
unknown; the later passes do not establish a production cause or claim a fix
for that original value. The accepted five-case GPU run captured 12 frames with
zero invalid pixels. Default floor RGB samples matched exactly and Ring radiance
passed all three channels at the original 0.075 tolerance. Diagnostic controls
were separate from default-pipeline acceptance.

Readable generated aliases can produce Unity's material-name versus
`Material.mat` filename warning. Artist-visible names remain readable while
internal identities stay stable; names were not replaced with a generic label
solely to silence that warning.

Existing active bake UVs are required; no automatic unwrap is performed. Narrow
lines require enough texture resolution. Selected editable layers need one
uniform color and initial emission strength. Tiny face/emission color differences
within one linear 8-bit step are unified to the face color with a recorded bake
warning; larger differences need matching source colors.

Emission sets material radiance. Nearby illumination and bloom remain part of
the Unity lighting/rendering setup. This change does not adjust global lights,
shadows or post-processing, and does not transfer arbitrary Blender node graphs.
The first deep fixture export exceeded the Windows temporary-manifest path
limit; its failed receipt remains, and the isolated fixture succeeded at a shorter
path. No general long-path export repair is claimed.

## Required checks and local delivery

| Delivery field | Status at this checkpoint |
| --- | --- |
| RR Helper version | **0.2.59** |
| Portable canonical Sync | Passed |
| Required eight-job ExporterContracts wrapper | **All eight groups passed**, exit 0; 2026-10-07 22:01:05.7302789–22:01:44.0836186 UTC; registration lifecycle 276 checks passed |
| Affected offline builds | **All three passed**, serial embedded SDK 8.0.318, zero errors |
| Versioned ZIP | `dist/rr_helper-0.2.59.zip`, 29 files |
| ZIP SHA256 | `05439EDF4B6170E19FF7DB001416E2E73DCE585A5ECCA723AF565A539274EDB0` |
| Blender 5.2 local installation and source-match check | Both user add-ons and the actually loaded addons_core copy: 29 files, four updated; each `--check` reports zero differences |
| Local/portable Sync check | Both default and portable `Sync-RRHelperAddon.ps1 -Check` passed |
| Live RR Helper refresh and loaded runtime version | **Passed**, loaded 0.2.59 in Builder6 PID 31796; final native read-only audit 2026-10-07 22:34:51.018856 UTC |

The affected offline projects are
`Unity.RandomRealm.Gameplay.Builds.Editor.csproj`,
`Unity.RandomRealm.Tests.csproj`, and `Unity.RandomRealm.Editor.csproj`.
The third already includes the verification menu, so project regeneration is
unnecessary. `Assembly-CSharp-Editor.csproj` is unrelated to this Ring change.

The affected offline build outcomes are:

| Project | Errors | Warnings | Recorded log |
| --- | --- | --- | --- |
| Unity.RandomRealm.Gameplay.Builds.Editor.csproj | 0 | 0 | `build-builds-editor.log` |
| Unity.RandomRealm.Tests.csproj | 0 | 7 existing CS0649 warnings | `build-tests.log` |
| Unity.RandomRealm.Editor.csproj | 0 | 0 | `build-editor.log` |

Those logs and their `*-receipt.json` files are in the persistent task staging
directory recorded above. The required wrapper's log is
`native-logs/exporter-contracts-0.2.59.log`, with its matching
`exporter-contracts-0.2.59-receipt.json`. That receipt records one-thread Blender
5.2 execution, exit 0, and no background Blender process before or after the
wrapper. The offline receipts record successful sequential execution with the
Unity 6000.5.9f1 embedded SDK.

The released `__init__.py` SHA256 is
`6E9729C549F8FAA6A3E4DB3C6738AD2D34D774FEB64EF886612049E1725014E0`.
The six Unity production files and two test/menu source hashes are retained in
the staged source/evidence. Initial user add-ons deployment backup:
`C:/Users/Randy/AppData/Local/CodexBackups/addon-deploy/20261008-060715-a68e5ea5`.
Actually loaded addons_core deployment backup:
`C:/Users/Randy/AppData/Local/CodexBackups/addon-deploy/20261008-062423-8c5d0295`.

## Live runtime refresh and scene protection

The first refresh still found runtime **0.2.58** because Builder6 loaded the
`D:/Blender5.2/5.2/scripts/addons_core/` copy instead of the already updated user
add-ons copy. The actual core installation was then deployed with the same
0.2.59 source and checked with zero differences. The first receipt,
`live-refresh-first-core-0.2.58.json`, remains preserved.

The final receipt is `live-refresh-0.2.59.json` in the persistent task staging
directory. It records **before 0.2.58, after 0.2.59**, actual module path
`D:/Blender5.2/5.2/scripts/addons_core/random_realm_builder_exporter/__init__.py`,
29 checked canonical Python files with no byte differences, and registered RNA
`RR_BUILDER_OT_set_pbr_ring_layer`. Its final native read-only audit passed at
2026-10-08 06:34:51.018856 +08:00, equivalent to
2026-10-07 22:34:51.018856 UTC.

`existing_scene_values_preserved` is true. The remaining **404 objects** match
their original mesh, material, transform, RR identity, queue settings and
reference layout. Active/selected `Hub_Glass_Circular_A` and dirty state true
remain unchanged. The saved file SHA is still
`02f677645fb3420cc9e0ed31fa00b5c24a4a9bd7e8f20f1b45f262565727025e`.
No Save, Export or Bake ran.

Two limited changes are recorded explicitly rather than claiming full snapshot
equality:

- The old plugin's unregister cleanup removed its temporary
  `RR_IconPreviewCamera` and two preview-restoration markers, changing the
  object count from 405 to 404. This was the plugin's helper, not a user model;
  the other objects, selection, dirty state and saved bytes were preserved.
- The new collapsed Advanced-section property `pbr_bake_advanced=False` was
  added to two scenes. An initial comparison rejected these newly initialized
  defaults. Its raw receipt, `live-refresh-second-added-ui-default.json`, is
  retained. The final read-only audit allows only this exact new False field
  and checks all existing values strictly; it does not relax existing settings.

The original VIEW_3D and bottom Shader Editor were restored. The historical
pending Python input string was restored but not executed. These UI restorations
did not run the user's pending command or save the scene.

All earlier failed receipts remain available. No Git commit, push, PR or remote
release is included; the user handles GitHub Desktop.
