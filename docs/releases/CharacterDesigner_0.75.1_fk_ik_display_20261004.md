# Character Designer 0.75.1 — native FK visibility and unified IK pole display

2026-10-04, Asia/Shanghai. Owner: X / Character Designer. Canonical source: `D:/MyRepository/Blender-addons-by-Randy/addons/character_designer`. Actual model and reasoning effort: unrecorded. Record ID: **CD-FKIK-DISPLAY-20261004-01**.

Switching to FK could leave owned ring shapes on the native limb bones or leave their PoseBone visibility disabled. IK pole cones and their guide lines used different visibility conditions. This release makes FK show the existing original upper, lower and end bones, and makes the IK pole and guide follow the same mode and visibility rules.

Use **Rig > Body > Bone Setup > FK** to select and rotate the original limb bones in Pose Mode. Choose **IK** to show the hand/foot targets and pole guides. Both directions preserve the pose through the existing matching transaction. **Bone Display > Original / Controls** remains the broader character workspace switch.

Owned FK rings are temporarily unbound in FK and restored in IK using an exact persistent ownership marker. The objects remain available for restoration; artist custom shapes are protected. Both Bone and PoseBone hide flags are synchronized. Stable VIS guides and Direct drawn guides follow their pole; switching a Stable pole from Sphere to Arrow immediately restores its guide. Existing Sphere styling continues to omit a guide. Collection eyes, solo choices, overlay settings and artist manual visibility remain respected.

Display state, shape bindings, marker, managed Body membership and recovery metadata participate in failure rollback. Animation frame changes update the display, while steady frames avoid repeating the full display plan. Pressing the current mode repairs its display without matching poses or inserting Auto Key keys.

## Validation

| Suite | Blender 5.1.0 | Blender 5.2.0 LTS |
| --- | ---: | ---: |
| Native display, ownership, save/load and rollback | [10 passed](D:/Blender/Projects/Character/X/Validation/fk_ik_display_20261004/display_blender51.log) | [10 passed](D:/Blender/Projects/Character/X/Validation/fk_ik_display_20261004/display_blender52.log) |
| Stable/Direct guide styling and keyed frame following | [5 passed](D:/Blender/Projects/Character/X/Validation/fk_ik_display_20261004/follow_blender51.log) | [5 passed](D:/Blender/Projects/Character/X/Validation/fk_ik_display_20261004/follow_blender52.log) |
| Batch pose, animation, Original, Dress and late-failure protection | [14 passed](D:/Blender/Projects/Character/X/Validation/fk_ik_display_20261004/batch_blender51.log) | [14 passed](D:/Blender/Projects/Character/X/Validation/fk_ik_display_20261004/batch_blender52.log) |
| Installed-driver evaluation behavior | [2 passed](D:/Blender/Projects/Character/X/Validation/fk_ik_display_20261004/perf_blender51.log) | [2 passed](D:/Blender/Projects/Character/X/Validation/fk_ik_display_20261004/perf_blender52.log) |

Each runtime passed 31 native cases, totaling 62 executions. Eight pure draw tests also passed in task tool output; these are not 70 different native cases. Compilation and scoped diff checks passed.

The [current Cosha isolated validation](D:/Blender/Projects/Character/X/Validation/fk_ik_display_20261004/new1.json) passed FK/IK cycles, visible native FK checks, data protection and candidate save/reopen. Final raw mesh, native Rest, Hair/Dress and animation differences were zero. Pose matrix maximum was 0.0001973519, skin matrix 0.0001847055 and evaluated surface displacement 0.00005112054 rig units. The existing 0.0002 pose/skin and 0.0001 surface limits were retained. Tests reset the complete checkpoint between measured cycles outside the timer to avoid accumulating roundtrip error.

The previous 0.75.0 record verified native FK numeric behavior and deformation but did not establish native FK appearance, PoseBone hiding or coupled pole/guide visibility. This release adds that coverage.

## Performance evidence

Installed switching drivers no longer trigger a redundant dependency-graph solve. A successful full display sync caches its final state so the subsequent frame follow does not repeat the full plan. First-time driver installation still tags and evaluates the graph; strict pose, surface, animation and rollback checks remain.

[Same-process comparison](D:/Blender/Projects/Character/X/Validation/fk_ik_display_20261004/measure_extra_solve.json): Blender 5.1, one requested thread, the same 0.75.1 source/display, one warmup and three alternating measured pairs. The comparison wrapper forces only the redundant installed-driver solve; reset and external validation are outside timing.

| Direction | Forced extra solve, median seconds | Optimized, median seconds | Matcher update calls |
| --- | ---: | ---: | ---: |
| FK → IK | 1.0704434 | 1.1667054 | 29 → 28 |
| IK → FK | 0.7151956 | 0.7450280 | 21 → 20 |

The update reduction is confirmed; measured wall time did **not** show a stable improvement. Separate old/new process runs also varied with order and load. There is no claimed speedup percentage, instant GUI response, FPS or memory benefit. No cross-chain matcher batching was introduced because existing inventory validation does not establish independence from all foreign constraints and drivers.

## Native X integration and save

The [final live report](D:/Blender/Projects/Character/X/Validation/fk_ik_display_20261004/live_display.json) confirms installed 0.75.1, successful Refresh and registration integrity, 12 visible/selectable native FK chain bones, eight owned ring bindings suppressed and zero Direct FK guide lines. Same-mode display repair did not match poses or insert keys. Pose Mode, frame39, active CoshaRig / CTRL_torso_2, no selected bones, Auto Key off and Arms Review were retained. The temporary Console was restored to Geometry Nodes.

Refresh/display/save comparisons against the current native window had zero pose, skin and surface error, zero raw mesh/Rest/Hair/Dress/animation differences, and exact preservation of every PoseBone transform input, rotation mode and lock. The live artist did not receive the candidate pose or an IK roundtrip.

The first integration attempt lacked an import and stopped before changes. A subsequent cross-process evaluated-pose gate also stopped before Refresh: asset and Rest data were exact, while GUI/background pose matrices differed by 0.0003788583. That observed difference has no confirmed cause. The final script keeps the exact asset gate and protects the current native pose across the operation with unchanged thresholds; it does not restore background matrices. Both initial reports are retained in the evidence folder.

Native `save_mainfile` returned **FINISHED**, the GUI title has no unsaved star, and disk SHA matched the live report. Saved **2026-10-04 07:16:49.4838186 +08:00**: [X.blend](D:/Blender/Projects/Character/X/X.blend), **32,235,486 bytes**, SHA-256 `87d9e22bfcc1cb37449f3a76f0a8674959751ffc8c3e25ceb6d0c50ad17b26ca`.

Latest independent recovery copy: [X_before_display_20261004_071640_996202.blend](D:/Blender/Projects/Character/X/Validation/fk_ik_display_20261004/X_before_display_20261004_071640_996202.blend), 32,231,787 bytes, SHA-256 `4d1b1827817b05768a615f4caa54c2cdd60ca58736790d76cf3889de5c2ccdfc`. The candidate was reopened only in isolation; no final artist disk-reopen audit was added.

## Package, deployment and next trigger

[character_designer-0.75.1.zip](D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.75.1.zip): **138 files**, **1,027,037 bytes**, SHA-256 `c110bfb13ed7718cf051c31ca2a4dca6a7926b5dfca17b806e6e2493f7fc4d78`. Blender 5.1 user installation, 5.2 user installation and X validation copy each passed deployment `--check` with zero differences. X 5.1 GUI was refreshed; Builder6 5.2 GUI was not touched. All task background processes exited normally; only the two original Blender GUIs remain. Foreground/heavy-work occupancy was released. No Commit, Push, PR or GitHub upload.

Recheck when native FK remains hidden, an IK pole and guide disagree, ownership/animation/rest dependencies change, or GUI latency is reproducible on the verified loaded version. The next performance investigation should measure click-to-draw latency and relevant evaluation calls in that situation; reuse these raw results rather than repeating the entire old investigation.
