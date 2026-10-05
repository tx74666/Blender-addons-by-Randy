# Character Designer 0.73.3 — per-character bone color palette

Date: 2026-10-03, Asia/Shanghai. Owner: X / Character Designer. Native validation,
packaging, deployment, live refresh and artist-scene save are complete. The final
saved X was independently read back with its pre-change model and pose baseline.

## Behavior and use

**Rig / Weight > Bone Display > Color Palette** adds five color groups for the
selected character. Each group has separate **Normal**, **Selected** and
**Active** RGB colors.

| Group | Targets |
| --- | --- |
| Body | Native central body bones and owned body controls |
| Arms / Hands | Both arms, shoulders, wrists, hands and fingers, with corresponding owned controls |
| Legs / Feet | Both legs, feet and toes, with corresponding owned controls |
| Hair | Trusted native Hair membership and owned hair bones |
| Dress | Owned Dress controls and native deform bones on shared or attached dedicated rigs |

Open the section to inspect the current pose/bone palette colors. Theme presets
are resolved through their Normal, Selected and Active slots. **Mixed** marks a
group whose bones differ; **Default / Scheme** or **Scheme** identifies proposed
colors where no explicit effective palette is available. A preset identifier
alone does not establish a particular displayed hue.

Click a group name to edit its three colors and apply them to that group. The
dialog starts from the current group colors, or its saved scheme when the group
has no targets. **Apply Palette** applies all five saved groups. These operators
support Blender Undo. **Restore** recovers the recorded preceding colors and
bone-color display setting; saved group RGB values remain available for a later
Apply.

## Persistence and protection

The scheme and recovery records belong to the main character object and persist
in the `.blend`. Object references preserve attached-rig recovery across renames.
Repeated palette edits retain their recovery point. Newly generated owned
controls and native Dress bones inherit an enabled scheme. Shared Dress migration
transfers the recovery reference before deleting its temporary dedicated rig;
deleted generated-rig references are pruned when a palette operation commits.

Target selection uses managed Original membership, saved limb mappings and exact
Hair/Dress ownership records. Wrist, hand and finger bones use the Arms group;
foot and toe bones use the Legs group. MCH/helpers, Dress manual/physics
mechanisms and foreign characters are excluded. Unsupported shared or linked
armature data and invalid recovery ownership are rejected before mutation.
Failed color writes restore their checkpoint.

Palette operations change pose display colors and their recovery properties.
They preserve native Bone colors, mesh topology, Shape Keys, UVs, vertex groups,
weights, Rest bones, pose channels, constraints, permanent rig relationships,
selection and Original-session records. Intentional native-widget removal can
retain the chosen palette; exact rebuild and rollback snapshots restore their
captured color state.

The section is closed by default. Panel reads do not apply colors, and no new
draw or frame handler reasserts the scheme over later artist edits.

## Validation

The initial live dialog-cancel check exposed an unsafe panel color binding:
swatches referenced temporary operator properties after the dialog released
them, causing a native Blender access violation while drawing the panel.
Version 0.73.2 copies RGBA into static swatches instead. Editable RGB properties
are used only inside the running dialog. The regression test rejects any panel
property bound to a temporary operator and checks all 15 copied color widgets.
Crash evidence remains in X/Validation/bone_palette_20261003/X_palette_dialog_crash.txt.
Version 0.73.3 also uses COLOR_GAMMA in the dialog, matching native bone display
RGB semantics without changing stored colors or the character's saved scheme.

Final results and evidence paths:

| Check | Result / evidence |
| --- | --- |
| Five-group native/control membership and protected-data checks | 8 native palette cases pass, Blender 5.1.0, including COLOR_GAMMA; X/Validation/bone_palette_20261003/native_tests_8_gamma.log |
| Per-group edits, current-color display and operator Undo contract | Passed in the palette suite; RGB tolerances account for Blender byte quantization |
| Save/reopen, rename and strict restore | Passed in the palette suite, including dedicated-to-shared Dress recovery |
| Invalid ownership/recovery records and injected write rollback | Passed; third-write failure restores the full checkpoint; direct and nested artist references still block Dress migration |
| New controls/native Dress inheritance, shared migration and Dress removal | Passed; migration uses its existing solver tolerance while palette-only protected data remains exact |
| Existing UI and controller-color regressions | UI routing 6, Head/Neck 4, Body detail 5, shared Dress safety 7 pass; total native cases this release: 30 |
| Source package and verified Blender-version/project deployment checks | 0.73.3 package: 133 files, 998,056 bytes. Blender 5.1, Blender 5.2 and X deployment checks each report zero differences; 5.2 was not a native GUI/regression target this release |
| Live artist UI | Refresh and Apply completed in Blender 5.1.0; Dress Escape/Cancel and Arms Cancel return to the panel without a crash, all 15 static swatches redraw, native Undo/Redo restores palette state |
| Artist-scene save and protection | Native Saved X.blend confirmation; 2026-10-03 21:25:24.519 +08:00, 32,317,033 bytes. Recovered saved readback passes against actual_x_before_palette.json; 598 raw meshes, 4 armatures and their protected channels/relationships retained; all 71 evaluated meshes have zero position error |
| Character scheme | Body 20, Arms 42, Legs 16, Hair 128, Dress 51: 257 colored bones; 33 native Dress bones use soft pink, wrists/hands share Arms. 2,163 foreign/helper bone colors remain unchanged |
| Eye/Hair texture recovery | Opened the artist X rather than a Temp auto-save, then used native Make Paths Absolute. Eye and both Hair images resolve to their original files; all 6 previously existing external images and material-image bindings are preserved. No packing or texture replacement |

During live recovery, the first saved readback caught cleared pose rotations.
Native Undo History contained Clear Pose Rotation after selection operations;
its origin was not established. The Make Paths Absolute history state preceding
that operation restored the authored pose and selection. Palette was reapplied
and the artist saved again. The failed readback and pose diagnosis remain intact;
the succeeding evidence is saved_artist_palette_recovered_readback.json. No pose
protection checks were relaxed. The final saved file SHA256 is
6927aa8ca0ff1b4a4c1a18663d3e7af617f4f7a52d855023907f3f3ca3b532df.

The final texture audit records unchanged input SHA/mtime and dirty=true in its
isolated runtime, including after a read-only reopen. That runtime flag is not a
disk-content comparison: the [Blender 5.1 getter](https://github.com/blender/blender/blob/v5.1.0/source/blender/makesrna/intern/rna_main.cc#L56)
returns the WindowManager's file_saved flag. Initial texture comparisons that
required dirty=false are retained as failed evidence. Save verification instead
uses the native save confirmation and the strict protected readback of the same
disk SHA. The explicit saved-verification comparison passes with that readback
and an unchanged current disk fingerprint, retaining all dirty flags:
texture_paths_verified_saved_disk_comparison.json. Raw texture evidence is
texture_paths_after_save_reopen_readonly.json;
pre-existing missing unpacked references Ref.png, Shoe.png and ShoeFront.png
remain outside this Eye/Hair repair. Relative paths in a Temp recovery file were
the observed failure mechanism; moving the external assets can still invalidate
absolute paths.

Dress Normal/Selected/Active stored RGB bytes are (173,110,140), (224,156,189),
and (255,207,230). This note makes no GUI latency, FPS or memory improvement
claim. Background verification did not modify or save the artist file. The
pre-change artist backup remains before_live_change/X_before_palette.blend.
Model/reasoning effort for this release are unrecorded. Changes, packaging and
deployment are local; there is no commit, push, PR or public release.
