# Character Designer 0.77.39 Playback validation cost

Animation playback on an adopted Main Rig repeatedly authenticated a complete Worklist recovery snapshot even when its bone display had no changes. Version 0.77.39 checks the existing display state first and performs the full current ownership proof whenever it needs to write display state. It also removes a second preparation of each already checked Forearm side within the same synchronous pass.

## Background evaluation

Blender 5.2.0 LTS, one thread, the same saved X Scene/CoshaRig and Walk_N action, two warmup frames and eight measured frames per block, in ABBA order:

| Block | Background frame evaluation median |
| --- | ---: |
| 0.77.38 baseline A | 354.028 ms |
| 0.77.39 code B | 175.027 ms |
| 0.77.39 code B | 170.293 ms |
| 0.77.38 baseline A | 354.818 ms |

The changed functions were exchanged in one disposable process; the candidate code was measured before its metadata version bump. All observed bone poses, complete managed corrective-key coordinates and state, calibration records, bone collections, hidden flags, widget assignments and palettes matched baseline at the same eight frames. Display callbacks fell from about 177–182 ms per frame to about 1 ms in these blocks. Timings are background evaluation, not viewport FPS; the live artist had unsaved changes outside the saved snapshot.

The first Forearm-only comparison reduced repeated side preparation from four calls to two but showed no consistent whole-frame speedup. A subsequent call profile identified the display ownership proof as the larger cost. Forearm still takes about 136–147 ms per frame in the combined experiment; full Worklist Rest authentication and inverse skinning remain active.

## Correctness and scope

The unchanged-display return performs no scene writes or ownership authorization. A mode or collection-state change still runs the complete live single-user check before cache publication and display writes. Native tests cover foreign ID-property references, unchanged artist hidden/solo flags, rejected changes, recovery after removing the foreign reference, and normal IK/FK changes.

Forearm runtime preparation reuses only records validated earlier in the same call. Ordinary mirror/setup callers keep their validation, and the next runtime update rechecks inputs. Native cache and recovery suites preserve direct RNA topology/Basis/weight/artist-key/output edits, changed saved Rest, invalid-input recovery, disabled state and Undo behavior.

Original X.blend, Rig/Rest, topology, weights, Shape Keys, modifiers, Actions and user content were not edited or saved by this task. No auxiliary bones, physics backend, quality reduction or frame dropping was introduced. Three native suites passed. Model and reasoning effort were unrecorded.

## Installation and evidence

The 190-file 0.77.39 package is built and deployed to Blender 5.2 and X/addons; both deployment checks report zero differences. The running artist was not refreshed or restarted. Changes are local, without commit or push.

[Delivery receipt](D:/Blender/Projects/Character/X/validation/playback_performance_20261014/delivery_receipt.json) includes source, package, input and evidence hashes. [Combined comparison](D:/Blender/Projects/Character/X/validation/playback_performance_20261014/combined_frame_step_report.json) retains all measured blocks. Prior reports and failures remain intact.

After loading 0.77.39, the next check is sustained GUI playback in material preview and Solid on the same action. Remaining slowdown should target current Worklist Rest proof, viewport rendering and other live callbacks rather than repeat the old fixed-frame arm-drag investigation.

## Hardware and geometry comparison

The saved character contains 38,727 base vertices and 37,872 base faces. Its six visible skin meshes evaluate to 83,915 vertices, 82,477 faces and 164,902 triangles with the native modifiers intact. Thus the approximately 160,000 figure corresponds to evaluated triangles. Shoes contribute 111,528 triangles (about 68%) after Mirror; this is a separate geometry cost to measure, without changing the authored asset.

The local computer is a Dell G15 5510, i7-10870H (8 cores/16 threads), 16 GiB installed RAM and RTX 3060 Laptop 6 GiB. Fresh WMI/nvidia-smi checks agree on model/CPU/GPU capacity; current free-memory readings are separate from the previous timing intervals.

[Blender Studio Sprite Fright](https://studio.blender.org/blog/sprite-fright-scene-fps/) reported capped 24 FPS for a single Rex character on i9-9900K and i7-5820K: 41,000 vertices, 2,000 total bones, 1,300 drivers,Subdivision disabled with Simplify, without baking or caching. [Blender 5.1 official benchmarks](https://developer.blender.org/docs/release_notes/5.1/animation_rigging/) report Amy production evaluation at 10.4/8.9/9.5 ms with 4/8/24 threads on Ryzen 9 9900X/Linux. These are context, not a matched hardware/mesh/rig guarantee for X.

A 24 FPS target gives 41.7 ms for the entire frame. The same X comparison retained all modifiers and geometry while diagnostic Forearm isolation measured 37.8 ms, supporting further optimization of the remaining Forearm path rather than attributing 170–175 ms solely to polygon count. Isolation leaves correction stale and cannot be the delivered solution; live GUI drawing remains unmeasured. [Comparison receipt](D:/Blender/Projects/Character/X/validation/playback_performance_20261014/comparison_receipt.json) records sources, geometry hashes and limits. This comparison added no runtime patch, deployment, artist refresh or scene save.
