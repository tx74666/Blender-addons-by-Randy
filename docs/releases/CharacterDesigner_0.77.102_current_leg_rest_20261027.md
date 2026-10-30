# Character Designer 0.77.102 — Fit controls to edited legs

An adopted character whose native leg Rest was edited before releasing controls
can now explicitly fit Both Legs to that current Rest. The current skeleton,
painted weights, author Shape Keys and historical Actions remain authoritative.
Historical animation recovery keeps its original compatibility checks.

The one-time Advanced operation authenticates the same actual local Rig/data,
historical owners and complete existing graph before recording a separate
current-controls-only revision. It uses the normal regional release/bind inside
an in-place rollback transaction. Forearm refresh follows the current scene,
including hidden objects; protection checkpoints still include every bound
object in the Main database.

In the real file, the current 3324-vertex body had valid135-point support on both
arms. Two out-of-scene reduced objects carried old invalid profiles. Refreshing
those checkpoint-only objects had incorrectly blocked the new operation. Their
data and outputs are retained. No current-body calibration was recaptured, no
old paint was restored and no Forearm algorithm was changed.

Complete219-file/187-Python approved projection:
`D:/Blender/Projects/Character/X/validation/leg_rest_rebuild_20261026/projection102/projection.json`
SHA256 `9030f22471a9837d2435a6a509baee7a7605a4cfe50bc39cee6462cf3908b35a`.
It extends101 by five existing files and two new modules;214 existing files are
exact. Paused canonical Body/Forearm candidates are excluded. Future changes
extend this complete projection rather than plain canonical sources.

Validation:44 pure tests,6 native functional/refusal/rollback cases,2 late faults,
real-model functional evidence with a finite six-owned-driver UUID supplement,
9 installer host methods and3 real RNA installer cases. Original raw refusals
remain refusals. Official build and dual219-file deployment/check passed.

Artist12548 received a narrow update of five original functions, two modules,
one operator and three class tuples, retaining original function/RNA/handler/
timer identities. A pre-code refusal came from our Console expression adding
the shared last-result builtin. Its exact owned leaf was restored; the original
guard remained unchanged and the unused core capability installed once.

The native operation and normal artist Save returned FINISHED. Fifteen event-loop
checks passed, including current native Rest, painted source/author Keys, all
Action content, bindings, history, the original Base Pose raw state and strict
ownership. Only the six newly generated constraint driver paths renew their
authenticated UUIDs. Edit/Cosha/frame29/366 selected vertices and Image Editor
layout were restored; the real event loop reported dirty=false.

Saved X.blend:40,245,249 bytes, SHA256
`71dce774bc4b7ba57d5634361f235e3a1c71612dadf11ff8eaea1bfa968228d1`.
Protection copy:40,231,672 bytes, SHA256
`8d4b098a2531f0188df4ca702481933c50d904e579b67e3fa245e69108900d25`.
No independent serialized reopen, unrestricted whole-project guard or new
numeric pose-quality/FPS result is claimed. Runtime-owned corrective output
coordinates may be recomputed. Historical Base Pose recovery is not rebased.

Acceptance: `D:/Blender/Projects/Character/X/validation/leg_rest_rebuild_20261026/acceptance_summary102.json`
SHA256 `d1e99226c29c08de15749cf6fc8019d3949efcc87e00f5862dd93d45265bb703`.
Owned runtime helpers/timers were released and private native children exited
normally. Auto-review refused cleanup of one owned host test directory, which
is retained. No preference save, Unity operation, Git commit/push or FPS claim.
