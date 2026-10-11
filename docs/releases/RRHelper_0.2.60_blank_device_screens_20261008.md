# RR Helper 0.2.60 — Plain gray imported device screens

Unity device boards imported into Blender now display an opaque, non-emitting
gray screen. Unity text, buttons, and captured screen pictures are omitted;
screen dimensions, geometry, UVs, facing, placement, and device identities stay
in the existing layout reference workflow. Ordinary hardware textures retain
their original loading, packing, path validation, and image memory limits.

`screenPreview.path` is bounded metadata only. Blender no longer reads its PNG,
requires the file, or counts it against its image budget. Screen size and affine
transform validation remain in force. The Unity export schema stays at version
1, so existing snapshots can be reimported even without their screen preview PNGs.

**Blank Imported Screens** converts existing, owned SCREEN overlays without
reimport. It uses object material overrides, preserves shared source meshes,
materials and images, and supports Undo. It skips read-only or altered ownership
references. Repeated blanking reuses the gray material; failure after partial
allocation restores old slots and releases only newly owned unused resources.
There are no automatic scene handlers or saves for this action.

## Validation and delivery

- Pure protocol/coordinate suite: 35 passed, 1 skipped for Windows symlink
  privilege, 0 failed (36 total).
- Blender 5.2.0 LTS isolated native suite: **14/14 passed**, no skips. Covers
  old textured screens, missing preview PNGs, retained hardware textures,
  dimensions/facing, persistent roots, artist children/shared resources,
  explicit migration, repeated clicks, allocation failure rollback,
  save/reload, and exclusion from model export.
- Only the single authorized background Blender process was started: PID
  66548, exit 0, confirmed gone. Sampled peak working set was 196.6 MiB.
- `rr_helper-0.2.60.zip`: 29 files, SHA-256
  `f74bcd2415ed4cd11984e643a0e11a4f7ca721184f6a98932a5d2a0748d60b2b`.
- Official deployment/check passed with zero differing files in the Blender
  5.2 AppData installation, actual `D:\Blender5.2\5.2\scripts\addons_core`
  installation, and RandomRealm2's portable Tools add-on copy.

Evidence is retained in
`D:\Blender\Projects\Build\WIP\Validation\device_blank_screens_20261008`.
The native report is
`layout-contract-ad76c8ae16154cf2a98a33b776d04d92/report.json`.
No Unity source or assets were changed, no Unity tests or builds were run, and
no artist scene was saved. Changes are local; no commit, push, or remote release.

See [Unity device layout usage](../unity_device_layout.md).

## Live Builder6 result

The running Blender 5.2 instance (PID 31796) was refreshed to RR Helper
**0.2.60** from the actual `D:\Blender5.2\5.2\scripts\addons_core` installation.
The native refresh guard passed: the complete before/after protection snapshots
were equal, deployed source differences were empty, and the new
`RR_BUILDER_OT_blank_unity_device_screens` operator was registered.

The native **Blank Imported Screens** operator returned `FINISHED` and converted
all **8 existing managed SCREEN overlays** in Builder6. They now share the gray
screen material with Base Color `(0.35, 0.35, 0.35, 1)`, Emission Strength `0`,
and no image texture nodes. Non-SCREEN material slots, all previous materials
and images, object/mesh identities, geometry, parents, positions, active object,
and selection were preserved. The protected before/after scene snapshots were
equal. The original 3D View was restored; the Tasks board was visually confirmed
to be blank gray without text or buttons. GUI and heavy-operation access were
returned after verification.

`Builder6.blend` remained unsaved and dirty throughout. Its on-disk SHA-256 stayed
`02f677645fb3420cc9e0ed31fa00b5c24a4a9bd7e8f20f1b45f262565727025e`.
**The 8 screens are already gray in the open scene; the user must save the scene
normally to retain this scene change on disk.** Subsequent layout imports use
gray screens by default without requiring this migration action.

The native live receipts are `live-refresh.json` and `live-blank-screens.json`
in the evidence directory above.
