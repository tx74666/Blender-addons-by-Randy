# Character Designer 0.77.164 — controller animation

Code-delivery acceptance: validation/controller_authoring_design_20261102/acceptance164_01.json, SHA31725c0183622672d129e5c0e62eea5f15a0c75afe35aceea4975e87d14f3ff6. Software delivery is separate from subsequent artist conversion.

Complete Source:262 files/230 Python, extending the complete Main163 parent (258/226). Projection [stage164_01](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/stage164_01/projection.json), SHA `1d9381986e6c3c2d23d0ee334924ccae9de231c1b770f3e87ea149f3a4c52830`; parent [Main163](D:/Blender/Projects/Character/X/validation/wrist_reset_owner_20261103/stage163_01/projection.json), SHA `26f676232bf264f3f443d1fca0ace1b366fda02d463a86be2e739479ea3a34e8`. All252 other parent files remain exact, including Main163/Hair161 work, HAND assets, native matcher and current-Rig capability.

## Release note core

Character Designer now prepares controller motion across a clip’s complete interval. Supported new ordinary Worklist imports automatically receive a separate controller Custom. For an existing animation, select its Custom and choose **Make Controller Animation**. Installed arms and legs default to IK trajectories; native FK remains available elsewhere. Imports without generated limbs retain native FK, and specialized Rigging imports keep their existing route.

For a uniform adjustment, turn **Auto Key off**, select an IK hand or foot target, move it, then choose **Apply Move Across Clip**. This publishes a new Custom with the same target-local displacement throughout the clip, preserving timing and handles. Its world direction follows the target’s parent. Ordinary **Insert** still edits the current time; it does not spread a correction across the clip. Time-faded local corrections are not included.

**Current Frame** IK/FK switches match the current pose and key that switch when keying is enabled. They are distinct from full-interval controller conversion. Automatic reverse editing between controller curves and a second complete bone-curve projection is not implemented.

Conversion and WholeMove preserve Source, earlier Customs, checkpoint history and Rest. Recovery remains tied to the current owned operation. Current-Custom conversion refuses unkeyed changes on animated body inputs; insert their keys first. Before the separate artist operation, readonly readiness also checked the authenticated secondary Pose channels. Pause running Hair Physics before conversion.

Every sampled or matched time retains strict native matrix verification at `2e-4`, together with world checks. Intermediate playback probes permit up to **1 mm** position and **0.25°** orientation error; scale and shear guards remain. These are sampled checks, not a continuous-time guarantee.

Validation includes 243 focused tests, unchanged solver-source checks, and a genuine two-frame Controller-to-keyed-FK regression preserving an unselected right arm’s pending input. The retained edited-snapshot export passed all 217 bones, 60 samples and two FBX readbacks. That export reused earlier numerical and host evidence; it performed no new solver or Make operation.

Official packaging and disk deployment passed:262 archive entries match the projection, CRC is valid, and both Blender5.2 and X deployment checks report zero differences. ZIP [character_designer-0.77.164.zip](D:/MyRepository/Blender-addons-by-Randy/dist/character_designer-0.77.164.zip), SHA `40028c220f85fd99430e6a27bad86d20a6066106c5cd153135ea9e097bfda863`.

Scoped live adoption also passed. Updater09 changed five functions, registered two operators and repaired exactly one known156 documentation value. The validator’s code/default objects stayed identical, and all45/14/22/9 functions in the four protected Main163 modules retained identity/state and module bindings. Independent post02 returned ALREADY_READONLY with zero code, operator, documentation or binding writes. The bounded witness was identical throughout; Pose/frame15/Hair4/AutoKeyOff/dirtytrue and current Action/slot remained unchanged.

The earlier updater08 refusal and automatic rollback remain recorded. Updater09’s narrow remedy passed private native20:unknown docs/changed patch flags refuse before writes; code+doc and final-metadata faults restore the exact fixture. No164 product source changed for this remedy.

**Current Walk status:**not converted by this core delivery, and no scene/preferences save, pose/frame/Action operation or full reload occurred during adoption. Root’s subsequent read-only readiness/current-Action work is separate. No full-runtime reload, full artist author-array acceptance, physical-I/GUI Undo, new FPS/performance result, Unity import or complete C# Rigging workflow is claimed.

## Exact ten-file delta versus Main163

| Status | File | Scope |
| --- | --- | --- |
| Changed | [__init__.py](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/stage164_01/character_designer/__init__.py) | Version literal only; package lifecycle inherited. |
| Changed | [README.md](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/stage164_01/character_designer/README.md) | Controller conversion and editing guide. |
| Changed | [animation_worklist.py](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/stage164_01/character_designer/animation_worklist.py) | Supported ordinary import-to-Custom conversion hook and helper. |
| Changed | [animation_worklist_ui.py](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/stage164_01/character_designer/animation_worklist_ui.py) | Controller UI imports, registration tuple and draw integration. |
| Changed | [control_pose_offset.py](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/stage164_01/character_designer/control_pose_offset.py) | Reviewed prepare/publication/create spans, fresh recovery, stable row lookup and typed owned-Action reference. |
| Changed | [animation_export_worker.py](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/stage164_01/character_designer/animation_export_worker.py) | Pinned bounded near-TRS fit/admission plus itertools/Vector imports; other nodes/constants and all export tolerances exact. |
| New | [controller_animation.py](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/stage164_01/character_designer/controller_animation.py) | Complete controller conversion, verification, publication and recovery. |
| New | [controller_animation_curves.py](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/stage164_01/character_designer/controller_animation_curves.py) | Verified controller-channel curve preparation/reduction. |
| New | [controller_animation_move.py](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/stage164_01/character_designer/controller_animation_move.py) | Whole-clip target-local move with owned recovery/discard. |
| New | [controller_animation_ui.py](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/stage164_01/character_designer/controller_animation_ui.py) | Make/WholeMove operators, context gates and clear editing hints. |

## Key evidence

- [Package/disk delivery](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/package_delivery164_01.json), SHA `a6481431…`; official ZIP262 exact/CRC and dual5.2-X checks0diff.
- [Scoped apply02](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/artist_scoped164_apply02.json), SHA `e07fecc5…`; [independent post02](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/artist_scoped164_post02.json), SHA `577c25bd…`; bounded witness `98052420…` equal.
- [Preserved08 refusal](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/artist_scoped164_failure01.json), SHA `ff6ace97…`; [09 private native20](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/installer09_oneDoc_component20.json), SHA `944ec0fc…`. Documentation156 SHA `e2790718…` to target SHA `df3a509f…`; no validator code/default-object replacement.
- [134 CurrentRig checks](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/current_pole_selected_pure16401.json), SHA `981423cb…`; [109 candidate checks](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/candidate164_pure01.json), SHA `4d56e061…`; [tiny genuine Controller/Main163 regression](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/main163_controller164_increment_component03.json), SHA `2133d95f…`.
- [Static solver inheritance](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/solver_inheritance164_01.json), SHA `76a7227e…`; source equality only, not runtime or current-Walk acceptance.
- [Worker28 and two native FBX readbacks](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/edited_snapshot_export28_component01.json), SHA `5c01e8a0…`;217 bones/60 samples, retained15/17 snapshot evidence reused, zero new solver/Make/host lease. [Numerical helper27](D:/Blender/Projects/Character/X/validation/controller_authoring_design_20261102/export_TRS_fallback27_component01.json), SHA `1146dcf2…`; local/bake/readback gates remain `2e-5`/`2e-4`/`4e-4`.


Separate current-Walk readiness passed with zero observed writes:346 raw poses,1974 current curves/112010 keys,1280 active secondary Pose components; no pending inputs or uncovered active paths. Receipt artist_controller_readiness164_01.json, SHA c4812ebd387006066a4cf17d0cafaed82eb9e35d098b44e77e5629366d694ab8. This is admission, not conversion evidence.
