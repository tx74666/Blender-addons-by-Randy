# Character Designer 0.77.195 — Local motion recovery and plugin review

Reviewed the complete approved194 package and its current delivery records on
2026-11-15. This was a focused reliability and workflow review, not an exhaustive
certification of every feature or a reopening of the closed playback-FPS work.

## Fixed

- A Local Motion worker could start before its poll timer failed to register,
  leaving an unpolled job that blocked later Generate requests. Startup now
  removes its new poll callback, cancels only its owned worker and confirms exit
  before releasing ownership. The previous usable result path and preview are
  retained on startup failure. If cancellation or exit confirmation fails, the
  job remains owned and Cancel Generation gives a retryable error.
- BVH Import Preview restored only the integer frame. It now restores the
  original fractional time on both success and failure, including negative
  fractional positions. Negative native subframes are converted to equivalent
  arguments accepted by Blender's frame-setting API.
- Finish Painting fallback used the existence of a managed Body collection to
  claim that no usable local pose controls existed. The status now describes
  the actual display fallback. Three string changes only: readiness, routing,
  native IK/FK controls, weights, ownership and recovery policy are unchanged.
- README had a193 title above194 instructions and retained the superseded
  Original-as-painting directions. It now leads with the current Paint Weights /
  Test Pose / Paint Weights / Finish Painting sequence, distinguishes formal
  Original Pose, and reports the current version. Repository source-workflow
  instructions now start with the complete-projection release path.

## Remaining work

| Priority | State | Next concrete step |
| --- | --- | --- |
| Current-mesh Rig / Unity topology replacement | Planned, not implemented or formally accepted. Existing source meshes differ physically from the old Unity meshes. | Continue the existing [current Rig repair plan](D:/Blender/Projects/Character/X/docs/CURRENT_RIG_REPAIR_PLAN_20261111.md): fresh complete ARM graph validation, private current-topology implementation, physics initialization, rollback and reviewed acceptance. No automatic topology replacement or old-index weight transfer. |
| Private exporter192 changes | Private Finish/painting-owner guards passed13 native groups; not a released package. | Rebase the explicitly reviewed delta onto the latest complete package, preserving current Original / painting / Reset behavior, then validate and release separately. Do not install private192 or plain canonical. |
|194 Reset + passive Action typed-array combination | An explicitly recorded test gap, not a demonstrated defect. | Add a focused isolated native combination test. Current successful component receipts retain their original scope. |
| Real artist Original / Paint / Finish / Reset / brush / Play acceptance | Not established by194's strict code-only artist audit or this review's generated fixtures. | Verify against fresh artist RAM and user workflow when that scene is available. |
| Restore Main Rig and broad historical protocol alias split | Explicitly deferred; strict ownership remains. | Handle as a separate scoped recovery task with current authority; this review grants no historical or Rest repair rights. |

The eight unselected canonical differences and the extra unshipped
`unity_rig_update.py` remain excluded and preserved. Exporter candidates include
four existing files plus that new module. Other differences include a function
relocation, defensive UI getters, an unaccepted provenance-proof optimization
and a display-policy change; their presence is not proof of four missing features.
The old Rig diagnosis's final191 version is historical; current release authority
comes from the latest AGENTS entry and complete projection.

## Verification and delivery

Complete195 extends approved194 by four files (`__init__.py`, `README.md`,
`animation.py`, `weight_paint_entry.py`),271 files/239 Python,267 inherited exact.
Projection: `D:/Blender/Projects/Character/X/validation/plugin_review_20261115/stage195_02/projection.json`.
SHA256: `43f9041b5cfab50fc68654a0343abf1fcc20d2a139291a5188e1e807e6d1f60f`.

58 focused tests pass:8 local-motion lifecycle,36 painting pose/session feedback,
2 lightweight real-process backend protocol and12 existing export-poll lifecycle.
Actual Blender5.2 tiny BVH success and post-allocation failure pass at positive
and negative fractional times (two test methods/four contexts). Existing native
Action/slot/curves, pose, selection, mode, frame/subframe, frame rate/ranges and
Auto Key remain exact. Failure removes only newly created preview IDs.
The complete package cold-imports, registers354 classes, passes registration
integrity and unregisters cleanly.

Before/failure receipts remain unchanged: the approved194 regression failures,
the first195 negative-subframe failure, its exact product/test inputs, and the
intermediate pure-test fixture error. Later passes do not relabel those results.
No model-generation quality, native solver speed, GUI FPS, Unity migration or
whole-plugin correctness claim follows from these checks.

20 additional native lifecycle checks pass using the registered Generate/Cancel
operators and actual lightweight Python workers through LocalMotionJob. Injected
poll registration failure reaps the exact new worker and retains the previous
preview/result; a real retry creates a native timer and Cancel reaps that worker.
Owner/timer cleanup and the native author/context witness pass. Both Python
workers and the private Blender exited, with no cleanup errors. This uses a tiny
test adapter, not an inference model or a claim about motion-generation quality.

Official build, Roaming Blender5.2/X deployment and matching check completed once,
all exit0. Both targets match271 files/0 differences. ZIP CRC/payload271 hashes
are exact; ZIP SHA256 `f58b108f8977bc50f008ff9858ff63373978801853ec73be37f218421efd0c4f`.
Deployment backup: `C:/Users/Randy/AppData/Local/CodexBackups/addon-deploy/20261115-042450-f1e4a3c6`.
Final acceptance: `validation/plugin_review_20261115/acceptance195_01.json`.
No artist Blender process was running at initial/repeated observations; no scene
or preferences were saved, and no live refresh/restart, Unity operation or Git
commit/push is part of this work.
