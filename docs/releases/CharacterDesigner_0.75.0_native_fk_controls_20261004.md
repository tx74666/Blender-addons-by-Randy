# Character Designer 0.75.0 — native FK and compact daily controls

Date: 2026-10-04, Asia/Shanghai. Canonical source: `D:/MyRepository/Blender-addons-by-Randy/addons/character_designer`. Artist integration owner: X. The actual Codex model and reasoning effort were not recorded.

The daily Body panel previously required choosing a limb before switching its mode, and Spine IK/FK appeared beside ordinary shared bending. **Rig > Body > Bone Setup** now provides one **IK / FK** row for all installed arm and leg chains. FK uses the existing upper, lower and end bones directly; this change creates no second FK skeleton. Individual limb switches, Foot details and Auto Align remain in **Advanced**.

**Spine Controls** presents **Bend Spine** and the individual section selectors. New Body Setup plans skip optional Spine IK; Update preserves an existing optional branch and its animation/dependency checks. Explicit Add, mode matching, Reset and Remove remain in Advanced. Hair retains its existing setup.

## Daily use

1. Select the character's main armature and use Object or Pose Mode.
2. Choose **Rig > Body > Bone Setup > FK** to pose the original arm and leg bones. In Pose Mode, select a bone and rotate it with **R**.
3. Choose **IK** to return to hand/foot targets. The switch matches the current pose before changing modes.
4. Use **Bend Spine** for shared bending and the section buttons for individual adjustments. Use Advanced only when separate limb settings or optional legacy Spine IK are needed.

**Bone Display > Original / Controls** remains the broader Body/Hair/Dress workspace choice. Selecting a limb mode from an active Original workspace first transfers its current pose back to Controls within the same transaction. A switch already in Controls changes the limb modes and does not change Hair or Dress mode.

## Transaction and animation protection

The batch matches all requested limbs and verifies the native solved pose before committing. A failure restores the complete batch, including an Original workspace that had already been left, source channels and locks, owned constraints and switching drivers, saved display state, Rest adaptation registry/provenance, Forearm corrective outputs and Dress local corrections. Native Rest geometry is not rewritten by the limb switch.

Blender Auto Key is respected. Matched channels and discrete mode cuts use the existing constant switch keys and pose bookends. Key insertion is staged in transaction-owned Actions; shared artist Actions, fake-user preferences and unrelated curves are preserved. Failed staging removes only owned copies with no real users. NLA Tweak Mode, non-editable Actions and ambiguous multi-slot, multi-layer or multi-strip Actions are refused before an Auto Key switch writes poses or keys. Pending Forearm previews and invalid ownership also block switching. The public operator supports native Undo.

## Validation

| Check | Runtime | Result | Persistent evidence |
| --- | --- | --- | --- |
| Final batch suite | Blender 5.1.0 | 14/14 passed | [5.1 log](D:/Blender/Projects/Character/X/Validation/native_fk_controls_20261004/limb_batch_14_blender51.log) |
| Final batch suite | Blender 5.2.0 LTS | 14/14 passed | [5.2 log](D:/Blender/Projects/Character/X/Validation/native_fk_controls_20261004/limb_batch_14_blender52.log) |
| Current Cosha checkpoint, public FK → IK → FK operators and six rotation probes | Blender 5.1.0 / canonical 0.75.0 | Passed | [Isolated report](D:/Blender/Projects/Character/X/Validation/native_fk_controls_20261004/validate_native_fk.json), [validator](D:/Blender/Projects/Character/X/Validation/native_fk_controls_20261004/validate_native_fk.py) |
| Installed runtime Refresh, combined artist operation and native Save | Blender 5.1.0 / installed 0.75.0 | Passed | [Live report](D:/Blender/Projects/Character/X/Validation/native_fk_controls_20261004/live_native_fk.json), [live script](D:/Blender/Projects/Character/X/Validation/native_fk_controls_20261004/live_native_fk.py) |

The 14-case batch suite covers four-limb native FK, separate Advanced switching, late-chain failure, switching-driver rollback, shared and fake-user Action success/failure, ambiguous Action slots, Original transfer and rollback, pending local Rest adaptation, Original Dress correction rollback, reverse-foot/toe poses and preview/rig guards. The existing 11-case limb matcher, three new Body/Spine default cases and Spine UI check also passed in Blender 5.1 during this task; their evidence is the task's tool output, without separate persistent log files.

The isolated validator used the artist's unsaved current state captured through native `save_as_mainfile(copy=True)` at frame 39, not an older disk scene. Its input and artist file fingerprints remained unchanged. Only the candidate copy received temporary FK/IK roundtrips and rotations.

After removing the authorized optional Spine branch, checks retained **596 mesh objects** and their geometry, UV, weights, relationships and Shape Key data, including **10 Shape Key blocks**. **184 native Body/Hair Rest bones** compared exactly. Hair/Dress graph and color metadata, animation snapshots, baseline, calibration and Direct registry had no exact differences. The two removed owned Spine widget meshes were explicitly excluded from the retained-mesh comparison; the three removed owned Spine driver paths were explicitly excluded from retained animation comparisons.

| Cosha comparison against captured input | Native pose matrix maximum | Native skin matrix maximum | Evaluated surface maximum, rig units |
| --- | ---: | ---: | ---: |
| Isolated final FK after FK → IK → FK | 0.00010752305388450623 | 0.00009964592754840851 | 0.000027676398876342038 |
| Actual artist after combined change and Save | 0.000030174851417541504 | 0.00002805422991514206 | 0.000007711203344517876 |

Pose and skin checks used a 0.0002 matrix tolerance; evaluated surfaces used 0.0001 rig units. Each isolated 0.12-radian local-X rotation of `upper_arm.L`, `upper_arm.R`, `thigh.L`, `thigh.R`, `CTRL_torso_bend` and `CTRL_torso_2` changed a bound mesh, restored channels exactly and restored evaluated surfaces with zero measured error. These probes verify that native FK and the retained Spine controls actually drive the character, beyond merely displaying a mode label.

## Applied X scene and save

The artist operation explicitly removed **5 optional Spine IK bones** and **2 owned widgets**, preserving the current pose and the shared `CTRL_torso_bend` with the three section controls: `CTRL_torso_1` for spine, `CTRL_torso_2` for Chest and `CTRL_torso_3` for UpperChest. All four limbs finished in FK. Hair and Dress configuration, pose inputs, palette and recovery records were retained. Auto Key stayed off and no switch keys were added. Pose Mode, the selected `CTRL_torso_2`, active CoshaRig and the Geometry Nodes editor were restored.

The GUI refreshed the installed add-on from 0.74.0 to **0.75.0** and passed runtime integrity checks. Native `save_mainfile` returned **FINISHED**; the window title's unsaved star cleared. The live artist did not open a candidate scene, receive temporary test rotations or perform the isolated IK roundtrip.

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| [Saved X.blend](D:/Blender/Projects/Character/X/X.blend) | 32,235,711 bytes | `ab696e718b36071d6abc9d5933004532bb6569af04397d8d586e45d1656c29a2` |
| [Independent checkpoint immediately before Refresh](D:/Blender/Projects/Character/X/Validation/native_fk_controls_20261004/X_before_live_native_fk_20261004_052853_421023.blend) | 32,240,347 bytes | `805b3a1659efe3582911cb8c32847a4c2ef75b91f9b4d138a4450bf771cbcbd4` |
| [Captured input for isolated validation](D:/Blender/Projects/Character/X/Validation/native_fk_controls_20261004/X_live_input.blend) | 32,237,242 bytes | `860b5981b281d908f7e87f662e70fb7bf63f60b18b18aedcbc0eb10626246a40` |
| [Isolated candidate](D:/Blender/Projects/Character/X/Validation/native_fk_controls_20261004/X_native_fk_candidate.blend) | 32,042,701 bytes | `65127f78e4e3a2e26ad6926f839ab32cca1e963a385b565b4660c4b7845826be` |

The artist file modification time is **2026-10-04 05:29:05.826289 +08:00**. The live report completed at **05:29:07.898907 +08:00**.

## Local package and deployment

The local [character_designer-0.75.0.zip](../../dist/character_designer-0.75.0.zip) contains **138 files**, is **1,022,286 bytes**, and has SHA-256 `b25f127b226096a37fe4f732af0bfabad350515236177f0952d549c2e431cf90`.

Deployment was made from canonical source with `tools/deploy_local.py --module character_designer`: the verified Blender **5.1 user installation**, **X/addons validation copy** and **5.2 user installation** each passed the corresponding `--check` with **zero differences**. The actual X GUI runs 5.1.0. These are local source, package and deployment changes; no commit, push or GitHub release was made.

## Limits and next triggers

This release simplifies controls and adds an atomic global handoff. It has no comparable performance benchmark or claimed speedup percentage; 0.74 timings are not evidence for 0.75. The final artist file was checked in the running GUI after native Save and fingerprinted on disk, but no final background disk-reopen audit was performed. Real-model rotation probes covered the captured Cosha at frame 39; fixture suites cover the listed animation and failure cases rather than every possible artist Action or pose.

Investigate again if a new pose fails the strict match, intentional Rest edits exceed the supported local adaptation, an unsupported Action layout is needed, or ordinary switches show reproducible new latency. Preserve the current scene and retain exact failure evidence before broadening changes. Animated or externally referenced optional Spine IK remains protected and is not automatically removed from other characters.
