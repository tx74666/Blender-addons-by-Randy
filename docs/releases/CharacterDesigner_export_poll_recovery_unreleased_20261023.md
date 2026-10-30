# Export poll recovery — delivered in 0.77.91, 2026-10-23

If native poll setup fails after launching an Action export, the worker and
Link lock now dispose normally so the user can retry. This applies to normal
and linked export, existing and new worklist Actions, and Composition. An
already registered poll and its owner are preserved. Successful publication
and previous publication receipts retain their existing behavior.

X integrated the four validated runtime files into approved release 0.77.91,
excluding the separately paused canonical body-region candidate. The guarded
artist-session update and preservation checks passed; the final receipt is
recorded below. Do not use a plain canonical deployment.

Evidence: `D:/Blender/Projects/Character/X/validation/export_poll_recovery_20261023/`.
The frozen candidate manifest pins every source and its approved .88 baseline.
108 focused pure tests passed, including failure, partial registration, existing
timer preservation, retry, Link-lock cleanup, and normal publication. Eight
isolated Blender 5.2 checks used real native timers, production cancel/dispose,
real Link locks and four task-owned Python child processes; all exited normally.
The validation did not load X or perform a character export, artist scene edit,
scene/preference save, Unity operation, Git commit or push.

The subsequent cross-poll review found that a residual generic poll could see a
new Composition job after a second cleanup failure. The generic poll now exits
before touching a Composition job or its owner; the dedicated Composition poll
keeps its receipt validation. The two injected residual-callback cases failed
before this guard and passed afterward. The full focused pure-test count is now
111; the 108 count above records the earlier candidate.

`candidate91.json` and `candidate_sources91/` in the evidence directory retain
the amended sources. The earlier candidate and withdrawn .90 projection/archive
remain unchanged. X owns the final approved .91 projection and delivery. The
original eight native lifecycle checks alone do not establish that the guarded
installer or the added poll guard was loaded; separate integration and live
delivery evidence follow.

The combined .89 to .91 private integration has since passed 34 native checks
under a fresh isolated CONFIG. Complete author snapshots match, every rollback
stage and NOOP passed, the new poll guard was exercised, the pinned input stayed
unchanged and the owned process quit normally. See `private91_acceptance.json`
in the export recovery evidence root. That isolated receipt does not establish
the artist runtime's version; the live delivery receipt below establishes it.

The two lazily loaded worklist modules were absent in the live .89 session.
A separate pinned preparer now passes 23 factory-only Blender 5.2 checks:
approved .89 definitions, exact dependency bindings, no-write refusal,
transaction rollback, unchanged author data/callbacks and repeat NOOP. See
`lazy89_preparation_acceptance.json` beside the private integration receipt.
This preparer only initializes missing modules; the guarded .91 installer and
the artist owner's final author-preservation receipt are separate steps.

For a narrow live update, add `_track_action_export` using the live `animation`
module globals, retain the original poll function identity, then update both
registered animation operator `execute` methods and the worklist/new-Action
`sync` methods. Composition changes only its own `begin_sync`. Preserve unrelated
functions, classes, handlers and all author data. The live delivery below used
the release owner's guarded integration and preservation checks.

## Live delivery accepted

X completed the guarded update in the existing artist process on 2026-10-23.
Character Designer 0.77.91 is loaded. All six outer preservation checks and
19 installer checks passed, including full author-state comparisons before
SaveCopy, after SaveCopy and after update. The latest dirty work has a separate
`artist_latest_before91.blend` backup; the formal X.blend bytes were not changed.
Frame 73, Object mode, CoshaRig selection, both armatures' Pose display and the
dirty state were retained. No full add-on reload or preference save occurred.

The task SDK stopped normally, its module was removed, its port has no listener,
and the artist process remains alive. Runtime source-change and refresh-pending
indicators are clear. See `artist91_delivery_acceptance.json` in the evidence
directory, indexing X's `artist91_runtime.json`, `artist91_sdk_cleanup.json` and
`final_os_cleanup.json`. The latest SaveCopy was hash-verified; a separate reopen
of that newest backup is not claimed.
