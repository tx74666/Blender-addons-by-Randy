# Character Designer 0.74.0 — Original calibration and local Rest adaptation

2026-10-04, Asia/Shanghai. Owner: X / Character Designer. Local source, package,
deployment and scene integration only; no commit, push or GitHub release.
Execution model variant and reasoning effort were not recorded.

## Behavior and use

After a supported intentional upper/lower Direct limb edit, finish Edit Mode
and choose **Rig / Weight > Bone Display > Controls**. The existing controls
adapt locally to the current author Rest while preserving the current pose.
The accepted Rest becomes the next Original session's baseline and the endpoint
for removing the controls. The exact older Direct provenance is retained.

The first adaptation performs full pose, skin and bound-surface validation and
can take longer than an ordinary display/pose transfer. It does not re-plane or
restore author bones. Existing Pose assets and Body Calibration confirmations
remain subject to their own compatibility checks; this operation does not
silently retarget or reconfirm them.

For forearm calibration, select the bound body mesh in Object Mode and use
**Capture & Preview**. Original uses the native hand, validates its intentionally
paused owned constraints without toggling them, and distinguishes constraint
influence drivers from actual hand transform animation. Confirm or cancel the
preview before switching Original / Controls. Real hand animation remains
protected. Mesh-only edits and microscopic Edit Mode float roundtrips do not
constitute skeleton structure edits.

Changed names, ownership, parenting, connection flags, disconnected joints or
unsupported animation/dependencies are refused before graph adaptation. Only
existing Direct schemas 4/5 upper/lower source geometry is supported. Failures
restore the Original session, channels, constraint mutes, display, Direct
registry, provenance, pole angles and managed corrective records/outputs.
Vertex coordinates, topology, all Shape Keys, UVs and weight values are retained.

## Validation

Blender **5.1.0**, canonical sources, isolated factory processes:

- Forearm Original: **4 passed**, including actual ±90° preview, cancellation,
  all Body extensions and real transform animation protection.
- Original Rest proof: **10 passed** (5 pure + 5 native); run before the final
  precision hook. Its no-edit path was subsequently covered by the real-X
  paired test and the existing Original suite.
- Local Rest adaptation: **10 passed** after the precise matching hook,
  including Direct 4/5, author data protection, real post-match failure rollback
  and managed corrective rollback. The first UUID refusal test used a stale
  Edit Mode Bone handle; the corrected test proves live ownership tampering.
- Existing Original suite: **18 passed** after the final runtime fixes.

Actual Cosha input was a native saved independent GUI copy. The two intentional
Rest changes were `upper_arm.L` and `upper_arm.R`; the right upper arm also had
a substantial axis change. The ordinary IK matcher initially exceeded the
strict skin limit (0.000202043 versus 0.0002) and rolled back. Only the affected
limbs now use the existing precise pole/reach refinement. Limits were retained.

Final actual-X validation passed: **598 raw meshes**, all keys/UVs/weights and
relationships exact; **4 armatures / 2,253 native Rest bones** exact; maximum
native pose matrix error **0.0000184178**; **5 bound surfaces / 27,561 evaluated
vertices**, maximum position error **0.00000619688 in rig-local units** against
the existing 0.0001 limit. Cosha automatic capture found **4 loops**; the native
hand reached **1.570798 rad** for a requested π/2. Cancel restored the raw model,
records and native pose exactly. The isolated result was saved separately.

## Comparable timing

One thread, same accepted Cosha input, six interleaved paired samples per
direction after warmup. Baseline is the saved 0.73.4 Original module, sharing
the same dependency runtime; timings are background function times.

| Direction | 0.73.4 median | 0.74.0 median | Change |
| --- | ---: | ---: | ---: |
| Controls → Original | 322.05 ms | 306.86 ms | −4.7% |
| Original → Controls | 274.04 ms | 234.21 ms | −14.5% |

Original JSON reads on return fell **4 → 1**, retaining one preflight and one
native Rest capture. This does not establish instant UI response, FPS or memory
improvement. The one-time author Rest adaptation took 10.12 s in the background
and 25.51 s in the artist window, including stricter matching and protection.

## Deployment and artist save

Package `character_designer-0.74.0.zip`: **137 files**. Blender 5.1 user add-on,
5.2 user add-on and X deployment copy all passed `--check` with **0 differences**.
5.2 deployment was checked only; native tests and artist integration used 5.1.

Current X refreshed to **0.74.0** from its verified 5.1 user installation.
Before refresh, a separate current checkpoint was saved as
`Validation/original_calibration_20261004/X_before_live_resync_20261004_031319_348214.blend`.
Refresh preserved the exact Original session, channels and model. Local
adaptation returned to Controls; the temporary console was restored to Geometry
Nodes. Native `save_mainfile` reported **FINISHED**, and the GUI title had no
unsaved marker. Artist `D:/Blender/Projects/Character/X/X.blend` was saved at
**2026-10-04 03:13:54.809 +08**, **32,234,335 bytes**, SHA256
`34700fb4b93e2d5bb52410ad56292c905b06652d4e9bfe87683b67644587ba7e`.
Post-save live protection checks passed. No new background disk-reopen audit
was claimed. Background Blender processes exited; UI/resource boundary released.

Evidence in X:
[actual validation and timing](D:/Blender/Projects/Character/X/Validation/original_calibration_20261004/validate_current_resync.json),
[live refresh/adaptation/save](D:/Blender/Projects/Character/X/Validation/original_calibration_20261004/live_refresh_resync.json),
[first strict refusal](D:/Blender/Projects/Character/X/Validation/original_calibration_20261004/validate_current_resync_initial_failure.json),
[ordinary match diagnostic](D:/Blender/Projects/Character/X/Validation/original_calibration_20261004/ordinary_match_diagnostic.json).

Future investigation should target a new failing native edit/dependency, actual
loaded-version mismatch, or measured GUI latency. Reuse these results and the
central Blender ledger instead of repeating the earlier arm-drag investigation.
