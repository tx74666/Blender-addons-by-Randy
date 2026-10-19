# Character Designer 0.77.61 · runtime proof and source watcher

Delivered locally on 2026-10-18 for Blender 5.2.0 LTS, build `fbe6228777e7`.
Actual AI model and reasoning effort were not recorded. The artist refreshed
to `.61`, passed 65 author-input checks with exact active corrective outputs,
and saved `X.blend` through the normal GUI. No independent native saved-file
readback was performed this round.

Two sources of redundant work are reduced:

- Forearm's mesh resolver already attempts strict Original validation before
  reading its actual support. Its scoped fallback now reuses that same call's
  rejection instead of attempting it again. Geometry, weighted arm dependencies,
  ancestor Rest, relations, ownership, drivers, mutes and the independently fresh
  Rest-view proof remain mandatory. The ordinary global validator is unchanged;
  no cross-frame successful validation cache is introduced.
- The development source watcher uses a ten-second idle interval and skips
  animation playback, interface lock and a pending Refresh. Manual Refresh keeps
  its immediate complete source check. The configured idle polling frequency
  changes from 1 Hz to 0.1 Hz; that is not a per-scan or application CPU claim.

## Evidence and limits

The immutable healthy `.60` X-Cosha input is SHA
`35953d9231ffa041e8dfbbbb1bf83e6c370dd3db26a0794e6599fa3d79964a37`,
Original mode, frame 15/subframe 0, with intentional leg Rest edits. Each version
uses one warm-up and eight unprofiled samples per phase in separate single-thread
native processes. Cached-output updates observe median 80.6543→68.8140 ms,
approximately 14.68% lower. Forced recomputation observes 89.59035→87.86505 ms,
approximately 1.93%; no meaningful speed claim is made for that difference.
Run order and shared machine load were not controlled. These are operation
times at one fixed pose, not viewport FPS or changing-animation latency.

The named inventory `validate` calls fall from 3 to 2 (strict global attempts
from 2 to 1), native Rest reads from 4 to 3, while all listed author inputs and
both full active corrective outputs remain exact. New reuse 4, local Rest 7 and
cache 9 native cases pass with zero skips. Twelve watcher unit checks pass.
Native watcher V3 proves real playback skips filesystem access, cancellation
stops/restores the frame, and two genuine ten-second callbacks each scan once;
the read-only interface-lock input is explicitly simulated.

The initial combined cache fixture failures and watcher V1/V2 harness failures
remain preserved. An unmodified cache suite passes in a fresh process. The final
cache fixture changes one setup assertion to check the current owner while
retaining old errors and all negative test bodies. Watcher V3 accepts observed
native `PASS_THROUGH` only with all functional, source, scene and timing guards.
The older `.60` full-suite failures are also retained, without a complete-suite
pass claim. No new physical Undo/Redo, full FBX/Unity, all-RNA/attributes or model
reduction/correction-disable acceptance is implied.

## Local source and delivery

Frozen C3 has 204 files; 201 retain accepted `.60` bytes. Only initializer
version/four watcher nodes and the two approved Forearm modules change. C3 and
benchmark/watcher C2 have identical runtime files. Remaining canonical
`body_region`/`bone_display` optimizations are excluded.

- Manifest SHA:
  `2ea94df68db414cdcff42caef401de4b86bc26397f936d6ebd280b15ede0d6be`.
- Official local ZIP, 1,563,552 bytes, SHA:
  `d681d1b4f1c8914214f9c04e56222506635a6d867e704645476651d7e9c3fb03`.
- Blender 5.2 and X deployment copies independently match all 204 shipped files;
  both checks have zero differences. Three files changed per target; all six
  deployment backups match accepted `.60`.
- Backup:
  `C:/Users/Randy/AppData/Local/CodexBackups/addon-deploy/20261018-205448-a7fb5d80`.
- Actual fresh `.61` runtime/panel, registered ten-second watcher and all 65 live
  protection checks pass. Managed outputs are recalculated from current inputs,
  then required exact; old surface coordinates are never restored.
- GUI `Saved X.blend` and a clean title were observed after restoring the
  original ImagePaint editor. Disk/copy SHA:
  `fae486347a49f00c1dca0cc126c3ee20c0502721741d7525cf15122c0ef836cc`,
  34,520,160 bytes. This is native-save observation plus disk/copy verification,
  not a native saved-file readback.

Raw evidence and final acceptance:
`D:/Blender/Projects/Character/X/validation/plugin_optimization_20261018/FINAL_README.md`
and `acceptance_summary.json` in the same folder. Only the artist Blender process
remains after task-owned native checks exit. No preferences were reset, and no
Git operation, commit or push was performed. Next investigation should target a
new reproducible pause, changed real Rest/deform dependency/topology, failed
refresh detection or a controlled changing-pose sample while preserving the
complete artist model and enabled corrections.
