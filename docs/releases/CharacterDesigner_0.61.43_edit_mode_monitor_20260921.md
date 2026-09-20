# Character Designer 0.61.43 — Edit Mode idle monitoring

## Diagnosis and fix

With the joint eye enabled and the active finger unprepared, the monitor read the
whole Edit Mode mesh before discovering the missing pair/recipe. Its failure
handler then assigned the same RNA status again. The resulting dependency update
queued another failed refresh. Full validation reads could also leave updates
queued when a prepared source was stale, creating the same idle loop.

`finger_workflow_ui._show` now checks the active pair, source and recipe before
updating the view layer or computing a mesh proof. `_refresh` changes error status
only when necessary and drains dependency updates under the rebuild guard after
handling a failed refresh. Real later changes can still request validation.

The eye retains the artist's enabled intent. A stale prepared preview remains an
explicitly labelled comparison reference; restoring the actual source triggers
revalidation and recovery. Full prepared-source equality and all topology/weight
write guards remain in force. No preload, new timer or polling service is added.

## Validation

All **22 focused background cases passed**: five new idle-monitor cases, six
combined-preview cases and eleven eye/lifecycle cases. The new tests pump actual
dependency updates and consume only queued refreshes. They cover missing pair,
missing source, blank recipe, zero scans for these prerequisites, settling after
an actual mesh edit, stale Apply rejection, restoration recovery and explicit Hide.

For an independent baseline check, the old .42 `_show` and `_refresh` bodies were
injected only into a disposable factory-startup Blender process. Both the
unprepared-finger and stale-source regressions failed to settle after 12 queued
rebuilds. Log: `D:\Blender\Projects\Character\X\outputs\monitor_baseline_regression.log`.

This release reran the 22 cases above, not all 51 cases reported for .42. The
other workflow, drag/proof, preview-cache, multi-selection and defaults suites
were not rerun for this change.

## Live measurements and preservation

The live object was Cosha: 3,418 vertices, ten Shape Keys, active `MIDDLE.L`, with
Pinky, Ring, Thumb and Index prepared but Middle unprepared. The eye was enabled.
The earlier roughly eight-second cProfile sample recorded nine workflow refreshes
totaling **7.2913002 seconds**, approximately **0.810 seconds per refresh**.

After the fix, an **8.0108869-second** scripted orbit/redraw probe including entry
into Edit Mode recorded **one workflow refresh taking 0.0002828 seconds** and
**zero `verify`, `signature` or `layout.fingerprint` calls during the measurement**.
The eye remained enabled and no probe error occurred. Other add-on work and the
first Edit Mode entry still contribute to the probe; this is not a frame-rate or
physical input-latency measurement, and the two runs are not a controlled FPS
benchmark.

Measurement artifacts in `D:\Blender\Projects\Character\X\outputs`:

- Before: `monitor_before.prof`.
- After: `monitor_fix_20260921\live_after.json` and
  `monitor_fix_20260921\after_profile.txt`.

The mesh fingerprint, selection and Shape Key values matched before and after.
The saved blend-file SHA-256 remained
`e352e217dd4183f78f53d164dc54eda2d2c82fde43667c0f84d8bf5af3107153`.
The original 3D view and Object Mode were restored. Only the two affected live
function bodies were replaced, preserving existing registered timer identities;
the add-on was not unregistered and Blender was not restarted or saved.

A later `monitor_fix_20260921/final_audit.json` confirms no pending monitor
request, monitor timer, active rebuild or profiler; the eye remains enabled.
The source watcher acknowledged exactly the two applied files, and the original
3D editor remains in Object Mode. The transient request recorded in the first
report was caused by returning to Object Mode and had settled by this audit.

## Local deployment

`character_designer-0.61.43.zip` was built with 104 files. The standard Blender 5.2
installation and X's validation copy each received two changed files, and
`deploy_local.py --check` reported zero mismatches for both destinations.
Previous files are backed up at
`C:\Users\Randy\AppData\Local\CodexBackups\addon-deploy\20260921-000524-f9248b39`.
Changes are local, uncommitted and unpushed.
