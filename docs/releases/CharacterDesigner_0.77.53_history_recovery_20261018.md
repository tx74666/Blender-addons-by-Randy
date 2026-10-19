# Character Designer 0.77.53 — Undo recovery isolation

Status: validated scope installed locally in Blender 5.2 and X/addons. Both
202-file projection checks report zero differences; two runtime files changed
from .52. Preserve the artist's current work and load the update in a fresh
Blender process. The current unsaved artist process has not been refreshed.

The artist's second Ctrl+Z crash reached the deferred `_resume_saved` timer in
0.77.52 while it enumerated `context.scene.objects`. Moving recovery out of the
Undo post handler did not resolve the artist's crash. The first .52 acceptance
is retained, with its coverage narrowed by this new incident.

The mitigation removes automatic Dress scene recovery after Undo/Redo. History
post handlers discard Python session references and cancel pending resume work;
an already dispatched resume callback must return before accessing scene RNA.
Idle history-pre also avoids scanning the scene. Undo history is retained.

Saved Automatic preferences remain available. Choose Automatic or Manual on the
selected Dress to authenticate its restored owned settings and resume or stop
that source. Ordinary file-load resume is handled independently. Native Wiggle
flags restored from an older Undo snapshot are not rewritten by history
callbacks, and no global third-party solver callbacks are disabled. This is a
scoped crash-path mitigation, not proof of the underlying native type fault or
a guarantee about every third-party Undo environment.

No shoe, body mesh, weights, Rest skeleton, or controller geometry is changed
by this release. The .52 regional evaluation/subframe fixes are retained.
Paused .48 watcher/provenance/display optimizations remain excluded from the
release projection.

Evidence and fixed reproduction entry:
`D:\Blender\Projects\Character\X\validation\body_history_crash_20261018\README.md`.
The latest crash log and original disk input pins are preserved there. The
artist's current unsaved state has not been operated on or overwritten.

Validation: 9 pure history and 7 unregister checks, 14 native checks using the
actual verified Wiggle 2 backend, and an isolated saved-X GUI public Rebuild,
Undo and Redo all passed. Author mesh/weights/keys, native Rest, custom styles,
object world matrices and exact history ownership/recovery revision comparisons
passed. Maximum rebuilt world surface error was 7.175636341301851e-6 m, under
the existing 1e-4 m limit; this is not a full gait or shoe-contact test.

The new GUI case invokes actual Refresh, releases old harness module/type/RNA
references, runs garbage collection, then performs three native Undo and three
Redo across real object transformations with typed scalar/Pointer/Collection
properties. All six states and protected X values passed for both .52 and .53.
Thus this minimal case does not reproduce the artist's native crash. Its
post-history observers confirm .52 queued the resume scan on all six steps and
.53 queued it on none. It omits mesh editing and failed Rebuild and does not
claim physical keyboard coverage.

All native processes ran serially and exited normally (43876, 38540, 35448);
only the original artist PID10432 remains. GUI quit recovery files are confined
to each test's own directory; the original Temp quit remains the pinned Builder6
file. No new X autosave was found in the scoped recovery scan. The artist disk
file remains SHA256 8d25e5ef2dc158b42acbeeb41ccdba7ed4aaff068cd55b22fd57118ebb792581.

The pinned `accepted_summary.json` records package/projection hashes, checks,
source/runtime versions and limitations. AI model and reasoning effort are
unrecorded. No Git commands, commit, push or remote release publication were used.
