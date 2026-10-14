# Character Designer 0.77.40 Playback Rest validation

Playback on an adopted Main Rig repeatedly decoded the same recovery record and rebuilt its saved Rest data. Version 0.77.40 retains a bounded immutable plan for the exact recovery text and precompiles ordinary saved Rest constants. Every call still resolves all recorded native references, checks rig identity and eagerly reads the complete current Rest. Full recovery loading keeps its original path. Unusual schemas and values fall back to that path without changing refusal order.

Forearm calculation also reuses immutable deformation matrix rows within one synchronous call. Direct source/copy Rest comparison reads each current matrix once after the original guards. Weight normalization order, scalar sums, float rounding, singular and condition checks, pose limits and output commit order remain unchanged. No current-state proof or mathematical output is reused across frames.

## Comparable measurements

Blender 5.2.0 LTS, saved X-Cosha input SHA256 AEA53AB4F324C7D99FDFE0CE40FC3D220B01D77B6C15671A5744E2BEA86008F6, one disposable process, one thread, two warmup and eight hot frames per block. Selected functions from the pinned 0.77.39 source and the new code were exchanged in that process.

| Block | Median background frame evaluation |
| --- | ---: |
| 0.77.39 baseline, first | 157.676 ms |
| Final 0.77.40 code, first | 102.898 ms |
| Final 0.77.40 code, second | 104.694 ms |
| 0.77.39 baseline, last | 157.748 ms |

The median of the two block medians decreased about 34%. All twelve staged blocks matched the observed bone pose, complete managed corrective coordinates and state, calibration records, bone collections, hide flags, widget assignments and palettes at identical frames. This is background frame evaluation, not viewport FPS or a measurement of unsaved artist edits.

Separate four-block ABBA comparisons used sixteen hot samples per block. Saved-constant Rest alone measured 112.157 / 116.519 ms with materialization and 103.491 / 107.565 ms with constants. Matrix-row reuse reduced local Forearm calculation from 38.672 / 40.913 ms to 37.174 / 37.657 ms; its whole-frame gain was smaller and varied with shared load.

The unmeasured reuse probe found 382 corrected vertices but 248 unique exact ordered weight groups per calculation; 172 successful inverses involved 98 groups. These are potential duplicate counts, not speedup measurements. Additional weighted-matrix and inverse caches were not introduced because their limited scope would require more numerical and failure-order logic. Further work should start from a new symptom or a measured GUI bottleneck.

## Validation and preservation

The new immutable saved-state tests, Worklist target, source Rest view, animation controls Rest view and matrix-read tests passed (102 pure tests; six additional Forearm scope tests also passed). Five native suites passed: Forearm mathematics, cache, recovery, native Rest projection and Unity animation preview/restore. The sixteen native Rest cases compare the optimized path with the original full loader after warm-cache reference deletion/replacement, direct Rest edits, record tampering, ownership changes and malformed input; lookup does not write native scene content.

The first native fixture run failed because its snapshot helper assumed every newly linked foreign armature already had a Pose. Its corrected snapshot records missing and empty Pose distinctly. The first comparison retained twelve matching timing blocks but failed when importing the optional diagnostic helper. Both failures, logs and exact source snapshots remain preserved; later complete runs passed.

Original artist X.blend retained its SHA256 and 34,686,989 bytes. No artist scene operation, live refresh, restart or save was performed. Model, weights, authored Shape Keys, Rest, controls, modifiers and scene content were not changed by this task. Private tests did not create a replacement artist scene. GUI drawing and external live callbacks remain unmeasured. Model and reasoning effort were unrecorded.

## Installation and evidence

The 190-file package was built and deployed to the verified Blender 5.2 installation and X/addons. Both checks reported zero differences. Previous deployment files were backed up by the deployment tool. The running artist has not been refreshed; the installed code takes effect when that module is loaded normally. Changes remain local, without Git staging, commit or push.

[Delivery receipt](D:/Blender/Projects/Character/X/validation/playback_performance_20261014/round2/delivery_receipt_07740.json) contains package/source/evidence hashes and deployment state. [Final staged comparison](D:/Blender/Projects/Character/X/validation/playback_performance_20261014/round2/final_07740_frame_step_report.json), [saved constants focus](D:/Blender/Projects/Character/X/validation/playback_performance_20261014/round2/saved_constants_focus_report.json) and [matrix rows focus](D:/Blender/Projects/Character/X/validation/playback_performance_20261014/round2/math_rows_focus_report.json) retain the raw small measurements. The accepted r2 sources and original 0.77.39 baseline are preserved beside them.

Next trigger: after loading 0.77.40, measure sustained GUI playback on the same action in Material Preview and Solid. If it remains slow, distinguish rendering, other live callbacks and remaining current-data validation before changing authored geometry or adding more caches.
