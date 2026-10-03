# Character Designer 0.73.2 — per-character bone color palette

Date: 2026-10-03, Asia/Shanghai. Owner: X / Character Designer. Validation,
packaging, deployment and artist-scene save results are pending final recording.

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

Final results and evidence paths:

| Check | Result / evidence |
| --- | --- |
| Five-group native/control membership and protected-data checks | 8 native palette cases pass, Blender 5.1.0; X/Validation/bone_palette_20261003/native_tests_8_final.log |
| Per-group edits, current-color display and operator Undo contract | Passed in the palette suite; RGB tolerances account for Blender byte quantization |
| Save/reopen, rename and strict restore | Passed in the palette suite, including dedicated-to-shared Dress recovery |
| Invalid ownership/recovery records and injected write rollback | Passed; third-write failure restores the full checkpoint; direct and nested artist references still block Dress migration |
| New controls/native Dress inheritance, shared migration and Dress removal | Passed; migration uses its existing solver tolerance while palette-only protected data remains exact |
| Existing UI and controller-color regressions | UI routing 6, Head/Neck 4, Body detail 5, shared Dress safety 7 pass; total native cases this release: 30 |
| Source package and verified Blender-version/project deployment checks | Pending final 0.73.2 rebuild and deployment checks |
| Artist-scene refresh, authorized palette changes and save verification | Pending |

This note makes no GUI latency, FPS, memory or theme-hue claim. Runtime versions,
test counts, real-model limits, deployment state and artist save state must be
recorded from completed evidence before final delivery. Local packaging or
deployment does not imply a commit, push or public release.
