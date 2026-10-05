# Character Designer 0.76.1 — explicit Hair reconciliation

The Strand Motion panel now keeps **Reconcile Strands** available after valid
saved metadata is loaded. An intentional Hair Rest or segment change can make
the live structure differ while the saved records still parse; previously the
strict actions refused that state but the panel did not expose reconciliation.

The button calls the existing explicit reconciliation transaction. It preserves
stable identity, strand settings and the existing ownership checks. Sidebar
redraw still reads saved metadata and does not perform native mirror searches.
All physics, profile, export and pairing code is unchanged from 0.76.0.

Native Blender 5.1.0 verification passed all 16 existing Hair UI tests, including
metadata-only redraw, reconciliation, rollback, Undo/Redo and save/reopen.
Evidence: `D:\Blender\Projects\Character\X\Validation\hair_motion_20261004\hair_ui_0761_20261004_1811.log`.
The 144-file release is deployed to the 5.1 and 5.2 user add-on directories and
the X validation copy; both deployment checks report zero differences.

Unity full-32 native lifecycle and concurrent focused gravity-independence checks
passed in isolated fixtures; all 32 strands also completed finite fixed-root
PlayerLoop sampling with context/files/start-setting preservation and lease
release. A separate same-clock eight-team collision check also passed 768
samples, including 32 direct contact frames, a target response difference of
0.00666339136660099 m and zero difference on the other three strands. Its first
background cleanup timed out; foreground plus the existing guarded idle Cancel
retry restored the context and released the lease without clearing flags or
waiving checks. Adventure remained the sole dirty scene with 46 roots, and its
disk file was unchanged. See `docs/hair_motion.md` for the exact report/source
hashes, preserved gravity evidence and cleanup limit. The supporting Editor
rebuild passed with exit code 0, 220 warnings and zero errors in 197.48 seconds;
its log is `unity_editor_collision_support_build_20261005.log` in X's validation
directory. Root found no retained task dotnet/MSBuild/VBCS workers.
The authorized Blender 5.1.0 artist operation now passed: Character Designer
0.76.1 and official `bl_ext.user_default.wiggle_bones` 1.1.2 are loaded;
installation, preferences save and X.blend save returned `FINISHED`. The file
was saved at 2026-10-05T01:32:20.3313822+08:00, 32,258,575 bytes, SHA256
`2690a4a97fa30e0e94f7df6a14d3f75e7ec8f5b4e71175d91ac966da6875445d`.
The title was clean, with 32 configured strands: Front10 / Side8 / Back14.
The 24 mirror-ID records include two center self-identities.

All seven live protection stages retained the same raw asset/pose/display/
selection/legacy-settings proofs, with zero evaluated pose error. Current live
counts are 377 meshes / 40 Shape Keys; isolated historical 375 / 20 counts retain
their original input scope. Inactive legacy `wiggle_2` 2.2.4 and its settings/raw
backing were retained and backed up; no automatic legacy conversion occurred.
Native recovery also finished. The immutable live report is
`live_install_refresh_config_save_20261004T173113.695674_0000.json`, SHA256
`45687a1ef5f4bd3fdf8e5170322bd9537c708ffb67ae76e9f914108eee8c25b5`.
No artist preview, bake, reload or new disk-reopen audit was performed. Runtime
source is unchanged by this installation/save step. The integration task is
complete; no Wiggle/Magica equal-time or physical-equivalence claim is made.
